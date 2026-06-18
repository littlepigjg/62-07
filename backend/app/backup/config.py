import os
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from ..config import BASE_DIR
from .models import BackupConfigModel, RetentionPolicy, NotificationConfig


BACKUP_DIR = BASE_DIR / "backups"
BACKUP_INDEX_FILE = BACKUP_DIR / "_backup_index.json"
BACKUP_LOG_FILE = BACKUP_DIR / "_backup_logs.json"
BACKUP_CONFIG_FILE = BASE_DIR / "config" / "backup.yaml"


class BackupConfig:
    def __init__(self):
        self._config: Optional[BackupConfigModel] = None
        self._load_config()

    def _load_config(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        default_sources = [
            str(BASE_DIR / "config"),
            str(BASE_DIR / "templates"),
            str(BASE_DIR / "logs"),
        ]

        default_config = BackupConfigModel(
            sources=default_sources,
            backup_dir=str(BACKUP_DIR),
            schedule="0 2 * * *",
            encryption_key=None,
            storage_type="local",
            regions=["default"],
            retention=RetentionPolicy(
                daily=30,
                weekly=12,
                monthly=12,
                yearly=3,
            ),
            notifications=NotificationConfig(
                enabled=False,
                on_success=False,
                on_failure=True,
                on_partial=True,
            ),
        )

        try:
            import yaml
            if BACKUP_CONFIG_FILE.exists():
                with open(BACKUP_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    self._config = BackupConfigModel(**data)
                    return
        except Exception:
            pass

        self._config = default_config
        self.save_config()

    def save_config(self) -> None:
        if self._config is None:
            return

        try:
            import yaml
            BACKUP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = self._config.model_dump()
            with open(BACKUP_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        except Exception:
            pass

    def get_config(self) -> BackupConfigModel:
        if self._config is None:
            self._load_config()
        return self._config

    def update_config(self, **kwargs) -> BackupConfigModel:
        current = self._config.model_dump() if self._config else {}
        current.update(kwargs)
        self._config = BackupConfigModel(**current)
        self.save_config()
        return self._config


backup_config = BackupConfig()


def get_backup_dir() -> Path:
    return BACKUP_DIR


def get_backup_index_path() -> Path:
    return BACKUP_INDEX_FILE


def get_backup_log_path() -> Path:
    return BACKUP_LOG_FILE


def get_backup_config_path() -> Path:
    return BACKUP_CONFIG_FILE
