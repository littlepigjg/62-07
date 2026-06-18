from typing import List, Dict, Any, Set
from datetime import datetime, timedelta
from collections import defaultdict

from .models import BackupType, BackupStatus, RetentionPolicy


class RetentionManager:
    def __init__(self, index_manager):
        self.index = index_manager

    def _parse_date(self, date_str: str) -> datetime:
        try:
            return datetime.fromisoformat(date_str)
        except Exception:
            return datetime.min

    def _get_backup_period(self, dt: datetime, period_type: str) -> str:
        if period_type == "daily":
            return dt.strftime("%Y-%m-%d")
        elif period_type == "weekly":
            week_start = dt - timedelta(days=dt.weekday())
            return week_start.strftime("%Y-%m-%d")
        elif period_type == "monthly":
            return dt.strftime("%Y-%m")
        elif period_type == "yearly":
            return dt.strftime("%Y")
        return ""

    def _group_backups_by_period(
        self,
        backups: List[Dict[str, Any]],
        period_type: str,
        backup_type: BackupType,
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups = defaultdict(list)
        for backup in backups:
            if backup.get("backup_type") != backup_type:
                continue
            if backup.get("status") != BackupStatus.COMPLETED:
                continue
            dt = self._parse_date(backup.get("created_at", ""))
            period = self._get_backup_period(dt, period_type)
            if period:
                groups[period].append(backup)
        return groups

    def _select_backups_to_keep(
        self,
        groups: Dict[str, List[Dict[str, Any]]],
        count: int,
    ) -> Set[str]:
        keep_ids: Set[str] = set()
        sorted_periods = sorted(groups.keys(), reverse=True)
        for period in sorted_periods[:count]:
            backups = groups[period]
            if backups:
                backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                keep_ids.add(backups[0]["id"])
        return keep_ids

    def apply_retention_policy(self, policy: RetentionPolicy) -> List[str]:
        all_backups = self.index.list_backups()
        keep_ids: Set[str] = set()

        for period_type, count in [
            ("daily", policy.daily),
            ("weekly", policy.weekly),
            ("monthly", policy.monthly),
            ("yearly", policy.yearly),
        ]:
            groups = self._group_backups_by_period(all_backups, period_type, BackupType.FULL)
            period_keep = self._select_backups_to_keep(groups, count)
            keep_ids.update(period_keep)

        full_keep_ids = set(keep_ids)
        for backup in all_backups:
            if backup.get("backup_type") == BackupType.INCREMENTAL:
                parent_id = backup.get("parent_backup_id")
                if parent_id and parent_id in full_keep_ids:
                    keep_ids.add(backup["id"])

        deleted_ids = []
        for backup in all_backups:
            backup_id = backup.get("id")
            if backup_id and backup_id not in keep_ids:
                if backup.get("status") == BackupStatus.COMPLETED:
                    deleted_ids.append(backup_id)

        return deleted_ids

    def get_retention_summary(self, policy: RetentionPolicy) -> Dict[str, Any]:
        all_backups = self.index.list_backups()
        completed_backups = [b for b in all_backups if b.get("status") == BackupStatus.COMPLETED]
        full_backups = [b for b in completed_backups if b.get("backup_type") == BackupType.FULL]
        incremental_backups = [b for b in completed_backups if b.get("backup_type") == BackupType.INCREMENTAL]

        summary = {
            "total_backups": len(completed_backups),
            "full_backups": len(full_backups),
            "incremental_backups": len(incremental_backups),
            "policy": policy.model_dump(),
            "retention_groups": {},
        }

        for period_type, count in [
            ("daily", policy.daily),
            ("weekly", policy.weekly),
            ("monthly", policy.monthly),
            ("yearly", policy.yearly),
        ]:
            groups = self._group_backups_by_period(all_backups, period_type, BackupType.FULL)
            summary["retention_groups"][period_type] = {
                "total_periods": len(groups),
                "keep_count": count,
                "periods": sorted(groups.keys(), reverse=True)[:count],
            }

        return summary
