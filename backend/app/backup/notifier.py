import json
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from .models import BackupRecord, BackupStatus, NotificationConfig


class BackupNotifier:
    def __init__(self, config):
        self.config = config

    def _get_notification_config(self) -> NotificationConfig:
        return self.config.get_config().notifications

    def _should_notify(self, status: BackupStatus) -> bool:
        cfg = self._get_notification_config()
        if not cfg.enabled:
            return False
        if status == BackupStatus.COMPLETED and cfg.on_success:
            return True
        if status == BackupStatus.FAILED and cfg.on_failure:
            return True
        if status == BackupStatus.PARTIAL and cfg.on_partial:
            return True
        return False

    def _build_message(self, record: BackupRecord) -> Dict[str, Any]:
        status_text = {
            BackupStatus.COMPLETED: "备份成功",
            BackupStatus.FAILED: "备份失败",
            BackupStatus.PARTIAL: "备份部分完成",
            BackupStatus.RUNNING: "备份进行中",
            BackupStatus.PENDING: "备份等待中",
        }.get(record.status, "备份状态更新")

        return {
            "title": f"[{status_text}] 备份任务通知",
            "backup_id": record.id,
            "backup_type": record.backup_type.value,
            "status": record.status.value,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "size": record.size,
            "compressed_size": record.compressed_size,
            "file_count": len(record.items),
            "storage_location": record.storage_location,
            "region": record.region,
            "error_message": record.error_message,
            "timestamp": datetime.now().isoformat(),
        }

    def send_email(self, recipients: List[str], subject: str, message: Dict[str, Any]) -> bool:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            import os

            smtp_host = os.environ.get("SMTP_HOST", "smtp.example.com")
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_password = os.environ.get("SMTP_PASSWORD", "")
            smtp_from = os.environ.get("SMTP_FROM", smtp_user)

            if not all([smtp_host, smtp_user, smtp_password]):
                return False

            msg = MIMEMultipart()
            msg["From"] = smtp_from
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject

            body = json.dumps(message, ensure_ascii=False, indent=2)
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            return True
        except Exception:
            return False

    def send_webhook(self, webhook_url: str, message: Dict[str, Any]) -> bool:
        if not AIOHTTP_AVAILABLE:
            return False

        async def _send():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        webhook_url,
                        json=message,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        return response.status in [200, 201, 204]
            except Exception:
                return False

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_send())
                return True
            else:
                return loop.run_until_complete(_send())
        except Exception:
            return False

    def notify_success(self, record: BackupRecord) -> bool:
        if not self._should_notify(BackupStatus.COMPLETED):
            return False

        cfg = self._get_notification_config()
        message = self._build_message(record)
        message["status_text"] = "备份成功完成"

        success = False
        if cfg.email:
            success |= self.send_email(cfg.email, f"[备份成功] {record.id}", message)
        if cfg.webhook_url:
            success |= self.send_webhook(cfg.webhook_url, message)

        return success

    def notify_failure(self, record: BackupRecord) -> bool:
        if not self._should_notify(BackupStatus.FAILED):
            return False

        cfg = self._get_notification_config()
        message = self._build_message(record)
        message["status_text"] = "备份执行失败"
        message["error"] = record.error_message

        success = False
        if cfg.email:
            success |= self.send_email(cfg.email, f"[备份失败] {record.id}", message)
        if cfg.webhook_url:
            success |= self.send_webhook(cfg.webhook_url, message)

        return success

    def notify_partial(self, record: BackupRecord) -> bool:
        if not self._should_notify(BackupStatus.PARTIAL):
            return False

        cfg = self._get_notification_config()
        message = self._build_message(record)
        message["status_text"] = "备份部分完成"

        success = False
        if cfg.email:
            success |= self.send_email(cfg.email, f"[备份部分完成] {record.id}", message)
        if cfg.webhook_url:
            success |= self.send_webhook(cfg.webhook_url, message)

        return success

    def test_notification(self) -> Dict[str, Any]:
        cfg = self._get_notification_config()
        test_record = BackupRecord(
            backup_type="full",
            status=BackupStatus.COMPLETED,
        )
        test_record.id = "test_notification"
        test_record.size = 1024 * 1024
        test_record.compressed_size = 512 * 1024

        message = self._build_message(test_record)
        message["status_text"] = "这是一条测试通知"
        message["test"] = True

        results = {
            "email": False,
            "webhook": False,
            "message": message,
        }

        if cfg.email:
            results["email"] = self.send_email(
                cfg.email,
                "[测试] 备份系统通知",
                message
            )

        if cfg.webhook_url:
            results["webhook"] = self.send_webhook(cfg.webhook_url, message)

        return results
