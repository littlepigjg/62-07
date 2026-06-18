from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from .config import backup_config
from .models import (
    BackupType,
    BackupStatus,
    RetentionPolicy,
    NotificationConfig,
    BackupConfigModel,
)
from .engine import backup_engine
from .restore import restore_engine
from .notifier import BackupNotifier
from .scheduler import backup_scheduler, start_backup_scheduler, stop_backup_scheduler


router = APIRouter(prefix="/api/backup", tags=["Backup"])


class BackupRequest(BaseModel):
    force_full: bool = False
    backup_type: Optional[BackupType] = None


class RestoreRequest(BaseModel):
    target_path: Optional[str] = None
    dry_run: bool = False


class ScheduleUpdateRequest(BaseModel):
    schedule: str


class ConfigUpdateRequest(BaseModel):
    sources: Optional[List[str]] = None
    backup_dir: Optional[str] = None
    schedule: Optional[str] = None
    encryption_key: Optional[str] = None
    storage_type: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_region: Optional[str] = None
    regions: Optional[List[str]] = None
    retention: Optional[RetentionPolicy] = None
    notifications: Optional[NotificationConfig] = None
    compression_level: Optional[int] = None
    incremental: Optional[bool] = None
    verify_after_backup: Optional[bool] = None


def _backup_to_response(backup_data: dict) -> dict:
    return {
        "id": backup_data.get("id"),
        "backup_type": backup_data.get("backup_type"),
        "status": backup_data.get("status"),
        "created_at": backup_data.get("created_at"),
        "started_at": backup_data.get("started_at"),
        "completed_at": backup_data.get("completed_at"),
        "size": backup_data.get("size"),
        "compressed_size": backup_data.get("compressed_size"),
        "file_count": len(backup_data.get("items", [])),
        "storage_location": backup_data.get("storage_location"),
        "storage_type": backup_data.get("storage_type"),
        "region": backup_data.get("region"),
        "checksum": backup_data.get("checksum"),
        "parent_backup_id": backup_data.get("parent_backup_id"),
        "error_message": backup_data.get("error_message"),
    }


@router.get("")
async def list_backups(
    status: Optional[BackupStatus] = Query(None),
    backup_type: Optional[BackupType] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    backups = backup_engine.list_backups(status=status, backup_type=backup_type)
    total = len(backups)
    paginated = backups[offset:offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_backup_to_response(b) for b in paginated],
    }


@router.get("/{backup_id}")
async def get_backup(backup_id: str):
    backup_data = backup_engine.index.get_backup(backup_id)
    if not backup_data:
        raise HTTPException(status_code=404, detail="Backup not found")
    return _backup_to_response(backup_data)


@router.post("", status_code=202)
async def create_backup(
    request: BackupRequest,
    background_tasks: BackgroundTasks,
):
    def _run_backup():
        try:
            backup_engine.create_backup(
                force_full=request.force_full,
                backup_type=request.backup_type,
            )
        except Exception as e:
            pass

    background_tasks.add_task(_run_backup)

    pending_backups = backup_engine.list_backups(status=BackupStatus.PENDING)
    if pending_backups:
        return {
            "message": "Backup task queued",
            "backup_id": pending_backups[0]["id"],
            "status": BackupStatus.PENDING,
        }

    return {"message": "Backup task queued"}


@router.post("/{backup_id}/verify")
async def verify_backup(backup_id: str):
    result = backup_engine.verify_backup(backup_id)
    if "error" in result and not result.get("valid"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/{backup_id}", status_code=204)
async def delete_backup(backup_id: str):
    if not backup_engine.delete_backup(backup_id):
        raise HTTPException(status_code=404, detail="Backup not found")
    return None


@router.post("/{backup_id}/restore")
async def restore_backup(
    backup_id: str,
    request: RestoreRequest,
    background_tasks: BackgroundTasks,
):
    if request.dry_run:
        result = restore_engine.restore_backup(
            backup_id=backup_id,
            target_path=request.target_path,
            dry_run=True,
        )
        return {
            "restore_id": result.id,
            "backup_id": result.backup_id,
            "status": result.status,
            "total_items": result.total_items,
            "items_restored": result.items_restored,
            "dry_run": True,
            "message": "Dry run completed - no files were modified",
        }

    def _run_restore():
        try:
            restore_engine.restore_backup(
                backup_id=backup_id,
                target_path=request.target_path,
            )
        except Exception as e:
            pass

    background_tasks.add_task(_run_restore)

    return {
        "message": "Restore task started",
        "backup_id": backup_id,
        "target_path": request.target_path,
        "status": "running",
    }


@router.get("/{backup_id}/restore/preview")
async def preview_restore(backup_id: str):
    result = restore_engine.preview_restore(backup_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/restores")
async def list_restores():
    return {"items": restore_engine.list_restore_records()}


@router.get("/restores/{restore_id}")
async def get_restore(restore_id: str):
    record = restore_engine.get_restore_record(restore_id)
    if not record:
        raise HTTPException(status_code=404, detail="Restore record not found")
    return record.model_dump()


@router.get("/logs")
async def get_backup_logs(
    backup_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    logs = backup_engine.get_task_logs(backup_id=backup_id, limit=limit)
    return {"total": len(logs), "items": logs}


@router.get("/config")
async def get_config():
    cfg = backup_config.get_config()
    result = cfg.model_dump()
    if result.get("encryption_key"):
        result["encryption_key"] = "********"
    if result.get("s3_secret_key"):
        result["s3_secret_key"] = "********"
    return result


@router.put("/config")
async def update_config(request: ConfigUpdateRequest):
    update_kwargs = request.model_dump(exclude_unset=True)
    updated = backup_config.update_config(**update_kwargs)
    result = updated.model_dump()
    if result.get("encryption_key"):
        result["encryption_key"] = "********"
    if result.get("s3_secret_key"):
        result["s3_secret_key"] = "********"
    return result


@router.get("/retention/summary")
async def get_retention_summary():
    from .retention import RetentionManager
    retention = RetentionManager(backup_engine.index)
    cfg = backup_config.get_config()
    return retention.get_retention_summary(cfg.retention)


@router.post("/retention/apply")
async def apply_retention():
    from .retention import RetentionManager
    retention = RetentionManager(backup_engine.index)
    cfg = backup_config.get_config()
    to_delete = retention.apply_retention_policy(cfg.retention)
    deleted = []
    for backup_id in to_delete:
        if backup_engine.delete_backup(backup_id):
            deleted.append(backup_id)
    return {"deleted_count": len(deleted), "deleted_ids": deleted}


@router.post("/notifications/test")
async def test_notification():
    notifier = BackupNotifier(backup_config)
    result = notifier.test_notification()
    return result


@router.get("/scheduler/status")
async def get_scheduler_status():
    return backup_scheduler.get_status()


@router.post("/scheduler/start")
async def start_scheduler():
    start_backup_scheduler()
    return {"message": "Backup scheduler started", "status": backup_scheduler.get_status()}


@router.post("/scheduler/stop")
async def stop_scheduler():
    stop_backup_scheduler()
    return {"message": "Backup scheduler stopped", "status": backup_scheduler.get_status()}


@router.post("/scheduler/update")
async def update_schedule(request: ScheduleUpdateRequest):
    backup_scheduler.update_schedule(request.schedule)
    backup_config.update_config(schedule=request.schedule)
    return {"message": "Schedule updated", "status": backup_scheduler.get_status()}


@router.get("/stats")
async def get_backup_stats():
    backups = backup_engine.list_backups()
    completed = [b for b in backups if b.get("status") == BackupStatus.COMPLETED]
    full = [b for b in completed if b.get("backup_type") == BackupType.FULL]
    incremental = [b for b in completed if b.get("backup_type") == BackupType.INCREMENTAL]

    total_size = sum(b.get("size", 0) for b in completed)
    total_compressed = sum(b.get("compressed_size", 0) for b in completed)

    last_backup = None
    if completed:
        last_backup = _backup_to_response(completed[0])

    return {
        "total_backups": len(backups),
        "completed_backups": len(completed),
        "full_backups": len(full),
        "incremental_backups": len(incremental),
        "failed_backups": len([b for b in backups if b.get("status") == BackupStatus.FAILED]),
        "total_size": total_size,
        "total_compressed_size": total_compressed,
        "compression_ratio": total_compressed / total_size if total_size > 0 else 0,
        "last_backup": last_backup,
        "storage_type": backup_config.get_config().storage_type,
        "regions": backup_config.get_config().regions,
    }
