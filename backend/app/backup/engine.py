import os
import json
import tarfile
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

from .config import backup_config, get_backup_index_path, get_backup_log_path, get_backup_dir
from .models import BackupRecord, BackupItem, BackupType, BackupStatus, BackupTaskLog
from .crypto import CryptoManager, compute_sha256, compute_file_sha256
from .storage import MultiRegionStorage, StorageAdapter
from .retention import RetentionManager
from .notifier import BackupNotifier


class BackupIndexManager:
    def __init__(self):
        self._index_path = get_backup_index_path()
        self._index: Dict[str, Any] = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        if self._index_path.exists():
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"backups": {}, "file_hashes": {}}

    def _save_index(self) -> None:
        try:
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_backup(self, record: BackupRecord) -> None:
        self._index["backups"][record.id] = record.model_dump()
        for item in record.items:
            self._index["file_hashes"][item.path] = {
                "hash": item.hash,
                "modified_at": item.modified_at,
                "size": item.size,
                "backup_id": record.id,
            }
        self._save_index()

    def update_backup(self, record: BackupRecord) -> None:
        if record.id in self._index["backups"]:
            self._index["backups"][record.id] = record.model_dump()
            self._save_index()

    def get_backup(self, backup_id: str) -> Optional[Dict[str, Any]]:
        return self._index["backups"].get(backup_id)

    def list_backups(self) -> List[Dict[str, Any]]:
        backups = list(self._index["backups"].values())
        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return backups

    def delete_backup(self, backup_id: str) -> bool:
        if backup_id in self._index["backups"]:
            del self._index["backups"][backup_id]
            self._save_index()
            return True
        return False

    def get_file_hash(self, file_path: str) -> Optional[Dict[str, Any]]:
        return self._index["file_hashes"].get(file_path)

    def get_last_full_backup(self) -> Optional[Dict[str, Any]]:
        backups = [
            b for b in self._index["backups"].values()
            if b["backup_type"] == BackupType.FULL and b["status"] == BackupStatus.COMPLETED
        ]
        if not backups:
            return None
        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return backups[0]

    def get_last_backup(self) -> Optional[Dict[str, Any]]:
        backups = [
            b for b in self._index["backups"].values()
            if b["status"] == BackupStatus.COMPLETED
        ]
        if not backups:
            return None
        backups.sort(key=lambda x: x["created_at"], reverse=True)
        return backups[0]


class BackupEngine:
    def __init__(self):
        self.config = backup_config
        self.index = BackupIndexManager()
        self.crypto = CryptoManager(self.config.get_config().encryption_key)
        self.storage = MultiRegionStorage(self.config)
        self.retention = RetentionManager(self.index)
        self.notifier = BackupNotifier(self.config)

    def _scan_source(self, source_path: Path, base_path: Path) -> List[BackupItem]:
        items = []
        if not source_path.exists():
            return items

        if source_path.is_file():
            rel_path = str(source_path.relative_to(base_path)).replace("\\", "/")
            stat = source_path.stat()
            file_hash = compute_file_sha256(source_path)
            items.append(BackupItem(
                path=rel_path,
                type="file",
                size=stat.st_size,
                hash=file_hash,
                modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            ))
            return items

        for root, dirs, files in os.walk(source_path):
            for file in files:
                file_path = Path(root) / file
                try:
                    rel_path = str(file_path.relative_to(base_path)).replace("\\", "/")
                    stat = file_path.stat()
                    file_hash = compute_file_sha256(file_path)
                    items.append(BackupItem(
                        path=rel_path,
                        type="file",
                        size=stat.st_size,
                        hash=file_hash,
                        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    ))
                except Exception:
                    continue
        return items

    def _get_changed_items(self, all_items: List[BackupItem]) -> List[BackupItem]:
        if not self.config.get_config().incremental:
            return all_items

        changed_items = []
        for item in all_items:
            stored = self.index.get_file_hash(item.path)
            if not stored or stored["hash"] != item.hash or stored["size"] != item.size:
                changed_items.append(item)
        return changed_items

    def _create_tar_archive(self, items: List[BackupItem], base_path: Path, output_path: Path) -> Tuple[int, int]:
        total_size = 0
        original_size = 0

        with tarfile.open(output_path, "w:gz", compresslevel=self.config.get_config().compression_level) as tar:
            for item in items:
                file_path = base_path / item.path
                if file_path.exists():
                    try:
                        tar.add(file_path, arcname=item.path)
                        original_size += item.size
                        total_size += item.size
                    except Exception:
                        continue

        compressed_size = output_path.stat().st_size
        return original_size, compressed_size

    def _compute_backup_checksum(self, archive_path: Path) -> str:
        return compute_file_sha256(archive_path)

    def _determine_backup_type(self, force_full: bool = False) -> BackupType:
        if force_full:
            return BackupType.FULL

        last_full = self.index.get_last_full_backup()
        if not last_full:
            return BackupType.FULL

        try:
            last_full_date = datetime.fromisoformat(last_full["created_at"])
            if datetime.now() - last_full_date > timedelta(days=7):
                return BackupType.FULL
        except Exception:
            pass

        return BackupType.INCREMENTAL if self.config.get_config().incremental else BackupType.FULL

    def create_backup(self, force_full: bool = False, backup_type: Optional[BackupType] = None) -> BackupRecord:
        cfg = self.config.get_config()
        actual_type = backup_type or self._determine_backup_type(force_full)

        record = BackupRecord(
            backup_type=actual_type,
            status=BackupStatus.PENDING,
            storage_type=cfg.storage_type,
        )
        self.index.add_backup(record)
        self._log_task(record.id, "info", f"开始{actual_type.value}备份", "backup")

        try:
            record.status = BackupStatus.RUNNING
            record.started_at = datetime.now().isoformat()
            self.index.update_backup(record)

            base_path = get_backup_dir().parent
            all_items: List[BackupItem] = []
            for source in cfg.sources:
                source_path = Path(source)
                source_base = source_path.parent if source_path.is_file() else source_path
                items = self._scan_source(source_path, source_base)
                all_items.extend(items)

            if actual_type == BackupType.INCREMENTAL:
                last_backup = self.index.get_last_backup()
                if last_backup:
                    record.parent_backup_id = last_backup["id"]
                backup_items = self._get_changed_items(all_items)
            else:
                backup_items = all_items

            if not backup_items:
                record.status = BackupStatus.COMPLETED
                record.completed_at = datetime.now().isoformat()
                self.index.update_backup(record)
                self._log_task(record.id, "info", "没有需要备份的变更", "backup")
                self.notifier.notify_success(record)
                return record

            record.items = backup_items
            self.index.update_backup(record)

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                archive_name = f"{record.id}.tar.gz"
                archive_path = temp_path / archive_name

                original_size, compressed_size = self._create_tar_archive(backup_items, base_path, archive_path)
                record.size = original_size
                record.compressed_size = compressed_size

                if cfg.encryption_key or os.environ.get("BACKUP_ENCRYPTION_KEY"):
                    encrypted_path = temp_path / f"{archive_name}.enc"
                    self.crypto.encrypt_file(archive_path, encrypted_path)
                    upload_path = encrypted_path
                    remote_key = f"{record.id}/{archive_name}.enc"
                else:
                    upload_path = archive_path
                    remote_key = f"{record.id}/{archive_name}"

                record.checksum = self._compute_backup_checksum(upload_path)

                metadata = BackupRecord(
                    id=record.id,
                    backup_type=record.backup_type,
                    status=BackupStatus.COMPLETED,
                    created_at=record.created_at,
                    started_at=record.started_at,
                    completed_at=datetime.now().isoformat(),
                    size=record.size,
                    compressed_size=record.compressed_size,
                    items=record.items,
                    parent_backup_id=record.parent_backup_id,
                    encryption_algorithm=record.encryption_algorithm,
                    checksum_algorithm=record.checksum_algorithm,
                    checksum=record.checksum,
                    storage_location=remote_key,
                    storage_type=record.storage_type,
                    region="default",
                    tags={"backup_id": record.id, "type": actual_type.value},
                    metadata={"remote_key": remote_key, "file_count": len(backup_items)},
                )

                metadata_path = temp_path / "metadata.json"
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata.model_dump(), f, ensure_ascii=False, indent=2)

                upload_results = self.storage.upload_to_all(upload_path, remote_key)
                metadata_results = self.storage.upload_to_all(metadata_path, f"{record.id}/metadata.json")

                success_regions = [r for r, ok in upload_results.items() if ok]
                if not success_regions:
                    raise Exception("Failed to upload backup to any region")

                record.storage_location = remote_key
                record.region = success_regions[0]

                if cfg.verify_after_backup:
                    verify_results = self.storage.verify_all(remote_key, record.checksum)
                    all_verified = all(verify_results.values())
                    if not all_verified:
                        self._log_task(record.id, "warning", "部分区域校验失败", "backup", {"verify_results": verify_results})

                record.status = BackupStatus.COMPLETED
                record.completed_at = datetime.now().isoformat()
                self.index.update_backup(record)

                if actual_type == BackupType.FULL:
                    self.retention.apply_retention_policy(cfg.retention)

                self._log_task(record.id, "info", f"备份完成: {len(backup_items)}个文件, {compressed_size/1024/1024:.2f}MB", "backup")
                self.notifier.notify_success(record)

                return record

        except Exception as e:
            record.status = BackupStatus.FAILED
            record.completed_at = datetime.now().isoformat()
            record.error_message = str(e)
            self.index.update_backup(record)
            self._log_task(record.id, "error", f"备份失败: {str(e)}", "backup", {"error": str(e)})
            self.notifier.notify_failure(record)
            raise

    def verify_backup(self, backup_id: str) -> Dict[str, Any]:
        backup_data = self.index.get_backup(backup_id)
        if not backup_data:
            return {"valid": False, "error": "Backup not found"}

        record = BackupRecord(**backup_data)
        if record.status != BackupStatus.COMPLETED:
            return {"valid": False, "error": f"Backup is not completed: {record.status}"}

        verify_results = self.storage.verify_all(record.storage_location, record.checksum)

        all_valid = all(verify_results.values())
        self._log_task(
            backup_id,
            "info" if all_valid else "warning",
            f"备份校验结果: {'通过' if all_valid else '失败'}",
            "verify",
            {"results": verify_results}
        )

        return {
            "valid": all_valid,
            "backup_id": backup_id,
            "checksum": record.checksum,
            "region_results": verify_results,
        }

    def list_backups(self, status: Optional[BackupStatus] = None, backup_type: Optional[BackupType] = None) -> List[Dict[str, Any]]:
        backups = self.index.list_backups()
        if status:
            backups = [b for b in backups if b["status"] == status]
        if backup_type:
            backups = [b for b in backups if b["backup_type"] == backup_type]
        return backups

    def delete_backup(self, backup_id: str) -> bool:
        backup_data = self.index.get_backup(backup_id)
        if not backup_data:
            return False

        record = BackupRecord(**backup_data)

        delete_results = self.storage.delete_from_all(record.storage_location)
        self.storage.delete_from_all(f"{backup_id}/metadata.json")

        success = self.index.delete_backup(backup_id)
        self._log_task(backup_id, "info", "备份已删除", "delete", {"delete_results": delete_results})

        return success

    def _log_task(self, backup_id: Optional[str], level: str, message: str, task_type: str, details: Optional[Dict[str, Any]] = None) -> None:
        log = BackupTaskLog(
            id=f"log_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            level=level,
            message=message,
            backup_id=backup_id,
            task_type=task_type,
            details=details or {},
        )

        log_path = get_backup_log_path()
        try:
            logs = []
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            logs.append(log.model_dump())
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(logs[-1000:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_task_logs(self, backup_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        log_path = get_backup_log_path()
        if not log_path.exists():
            return []

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
            if backup_id:
                logs = [l for l in logs if l.get("backup_id") == backup_id]
            logs.sort(key=lambda x: x["timestamp"], reverse=True)
            return logs[:limit]
        except Exception:
            return []


backup_engine = BackupEngine()
