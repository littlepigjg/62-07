import asyncio
import logging
from typing import Optional
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import backup_config
from .engine import backup_engine


logger = logging.getLogger(__name__)


class BackupScheduler:
    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._job_id = "backup_job"

    def _backup_job(self):
        try:
            logger.info("Starting scheduled backup")
            backup_engine.create_backup()
            logger.info("Scheduled backup completed successfully")
        except Exception as e:
            logger.error(f"Scheduled backup failed: {e}")

    def start(self):
        if self._scheduler and self._scheduler.running:
            return

        self._scheduler = BackgroundScheduler()

        cfg = backup_config.get_config()
        if cfg.enabled:
            self.update_schedule(cfg.schedule)

        self._scheduler.start()
        logger.info("Backup scheduler started")

    def stop(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown()
            logger.info("Backup scheduler stopped")

    def update_schedule(self, cron_expression: str):
        if not self._scheduler:
            return

        try:
            if self._scheduler.get_job(self._job_id):
                self._scheduler.remove_job(self._job_id)

            trigger = CronTrigger.from_crontab(cron_expression)
            self._scheduler.add_job(
                self._backup_job,
                trigger=trigger,
                id=self._job_id,
                replace_existing=True,
            )
            logger.info(f"Backup schedule updated: {cron_expression}")
        except Exception as e:
            logger.error(f"Failed to update schedule: {e}")

    def get_status(self) -> dict:
        cfg = backup_config.get_config()
        return {
            "scheduler_running": self._scheduler.running if self._scheduler else False,
            "schedule": cfg.schedule,
            "enabled": cfg.enabled,
            "next_run_time": self._get_next_run_time(),
        }

    def _get_next_run_time(self) -> Optional[str]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(self._job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None


backup_scheduler = BackupScheduler()


def start_backup_scheduler():
    backup_scheduler.start()


def stop_backup_scheduler():
    backup_scheduler.stop()
