import os
import json
import tarfile
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from .config import backup_config, get_backup_dir
from .models import (
    BackupRecord,
    BackupItem,
    BackupType,
    BackupStatus,
    RestoreRecord,
    RestoreStatus,
)
from .crypto import CryptoManager, compute_file_sha256
from .storage import MultiRegionStorage


class RestoreEngine:
    def __init__(self):
        self.config = backup_config
        self.crypto = CryptoManager(self.config.get_config().encryption_key)
        self.storage = MultiRegionStorage(self.config)
        self._restore_records: Dict[str, RestoreRecord] = {}

    def _get_backup_chain(self, backup_id: str) -> List[Dict[str, Any]]:
        from .engine import backup_engine

        chain = []
        current_id = backup_id

        while current_id:
            backup_data = backup_engine.index.get_backup(current_id)
            if not backup_data:
                break
            chain.append(backup_data)

            if backup_data.get("backup_type") == BackupType.FULL:
                break

            current_id = backup_data.get("parent_backup_id")

        chain.reverse()
        return chain

    def _download_and_extract(
        self,
        backup_data: Dict[str, Any],
        temp_path: Path,
    ) -> Tuple[bool, Optional[str]]:
        record = BackupRecord(**backup_data)
        remote_key = record.storage_location

        if not remote_key:
            return False, "No storage location found"

        file_name = remote_key.split("/")[-1]
        download_path = temp_path / file_name

        region, success = self.storage.download_from_any(remote_key, download_path)
        if not success:
            return False, f"Failed to download backup from any region"

        if self.config.get_config().verify_after_backup:
            actual_checksum = compute_file_sha256(download_path)
            if actual_checksum != record.checksum:
                return False, f"Checksum verification failed for {record.id}"

        extract_path = temp_path / f"extracted_{record.id}"
        extract_path.mkdir(exist_ok=True)

        if file_name.endswith(".enc"):
            decrypted_path = temp_path / file_name[:-4]
            try:
                self.crypto.decrypt_file(download_path, decrypted_path)
            except Exception as e:
                return False, f"Decryption failed: {str(e)}"
            archive_path = decrypted_path
        else:
            archive_path = download_path

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(extract_path)
        except Exception as e:
            return False, f"Failed to extract archive: {str(e)}"

        return True, str(extract_path)

    def _restore_items(
        self,
        extract_path: Path,
        target_path: Path,
        items: List[BackupItem],
    ) -> Tuple[int, List[str]]:
        restored = 0
        errors = []
        base_path = get_backup_dir().parent

        for item in items:
            try:
                source_file = extract_path / item.path
                if not source_file.exists():
                    continue

                dest_file = target_path / item.path
                dest_file.parent.mkdir(parents=True, exist_ok=True)

                shutil.copy2(source_file, dest_file)
                restored += 1
            except Exception as e:
                errors.append(f"{item.path}: {str(e)}")

        return restored, errors

    def restore_backup(
        self,
        backup_id: str,
        target_path: Optional[str] = None,
        dry_run: bool = False,
    ) -> RestoreRecord:
        from .engine import backup_engine

        restore_record = RestoreRecord(
            backup_id=backup_id,
            status=RestoreStatus.PENDING,
            target_path=target_path,
        )
        self._restore_records[restore_record.id] = restore_record

        try:
            restore_record.status = RestoreStatus.RUNNING
            restore_record.started_at = datetime.now().isoformat()

            backup_data = backup_engine.index.get_backup(backup_id)
            if not backup_data:
                raise ValueError(f"Backup {backup_id} not found")

            if backup_data.get("status") != BackupStatus.COMPLETED:
                raise ValueError(f"Backup {backup_id} is not completed")

            backup_chain = self._get_backup_chain(backup_id)
            if not backup_chain:
                raise ValueError(f"Could not build backup chain for {backup_id}")

            total_items = set()
            for b in backup_chain:
                for item in b.get("items", []):
                    total_items.add(item.get("path"))
            restore_record.total_items = len(total_items)

            if dry_run:
                restore_record.status = RestoreStatus.COMPLETED
                restore_record.completed_at = datetime.now().isoformat()
                restore_record.items_restored = restore_record.total_items
                restore_record.verify_passed = True
                return restore_record

            actual_target = Path(target_path) if target_path else get_backup_dir().parent

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                all_items: Dict[str, BackupItem] = {}

                for backup_data in backup_chain:
                    record = BackupRecord(**backup_data)
                    success, result = self._download_and_extract(backup_data, temp_path)
                    if not success:
                        raise ValueError(result)

                    extract_path = Path(result)
                    for item in record.items:
                        all_items[item.path] = item

                    items_list = [BackupItem(**i) if isinstance(i, dict) else i for i in record.items]
                    restored, errors = self._restore_items(extract_path, actual_target, items_list)
                    restore_record.items_restored += restored

                    if errors:
                        raise ValueError(f"Restore errors: {'; '.join(errors)}")

            restore_record.status = RestoreStatus.VERIFYING

            verify_passed = True
            for item_path, item in all_items.items():
                restored_file = actual_target / item_path
                if restored_file.exists():
                    actual_hash = compute_file_sha256(restored_file)
                    if actual_hash != item.hash:
                        verify_passed = False
                        break
                else:
                    verify_passed = False
                    break

            restore_record.verify_passed = verify_passed
            restore_record.status = RestoreStatus.COMPLETED
            restore_record.completed_at = datetime.now().isoformat()

            backup_engine._log_task(
                backup_id,
                "info",
                f"还原完成: {restore_record.items_restored}/{restore_record.total_items}个文件, 校验{'通过' if verify_passed else '失败'}",
                "restore",
                {"restore_id": restore_record.id, "verify_passed": verify_passed}
            )

            return restore_record

        except Exception as e:
            restore_record.status = RestoreStatus.FAILED
            restore_record.completed_at = datetime.now().isoformat()
            restore_record.error_message = str(e)

            from .engine import backup_engine
            backup_engine._log_task(
                backup_id,
                "error",
                f"还原失败: {str(e)}",
                "restore",
                {"restore_id": restore_record.id, "error": str(e)}
            )
            raise

    def quick_restore(self, backup_id: str) -> RestoreRecord:
        return self.restore_backup(backup_id)

    def get_restore_record(self, restore_id: str) -> Optional[RestoreRecord]:
        return self._restore_records.get(restore_id)

    def list_restore_records(self) -> List[Dict[str, Any]]:
        records = list(self._restore_records.values())
        records.sort(key=lambda x: x.created_at, reverse=True)
        return [r.model_dump() for r in records]

    def preview_restore(self, backup_id: str) -> Dict[str, Any]:
        from .engine import backup_engine

        backup_data = backup_engine.index.get_backup(backup_id)
        if not backup_data:
            return {"error": "Backup not found"}

        backup_chain = self._get_backup_chain(backup_id)

        all_items: Dict[str, Any] = {}
        for b in backup_chain:
            for item in b.get("items", []):
                all_items[item["path"]] = item

        total_size = sum(item.get("size", 0) for item in all_items.values())

        return {
            "backup_id": backup_id,
            "backup_chain": [b["id"] for b in backup_chain],
            "chain_types": [b["backup_type"] for b in backup_chain],
            "total_files": len(all_items),
            "total_size": total_size,
            "items": list(all_items.values())[:100],
        }


restore_engine = RestoreEngine()
