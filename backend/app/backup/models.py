from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class BackupType(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class BackupStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class RestoreStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFYING = "verifying"


class BackupItem(BaseModel):
    path: str
    type: str
    size: int
    hash: str
    modified_at: str


class BackupRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"bkp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{__import__('uuid').uuid4().hex[:8]}")
    backup_type: BackupType
    status: BackupStatus = BackupStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    size: int = 0
    compressed_size: int = 0
    items: List[BackupItem] = Field(default_factory=list)
    parent_backup_id: Optional[str] = None
    encryption_algorithm: str = "AES-256-GCM"
    checksum_algorithm: str = "SHA-256"
    checksum: str = ""
    storage_location: str = ""
    storage_type: str = "local"
    region: str = "default"
    error_message: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RestoreRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"rst_{datetime.now().strftime('%Y%m%d%H%M%S')}_{__import__('uuid').uuid4().hex[:8]}")
    backup_id: str
    status: RestoreStatus = RestoreStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    target_path: Optional[str] = None
    items_restored: int = 0
    total_items: int = 0
    error_message: Optional[str] = None
    verify_passed: Optional[bool] = None


class BackupTaskLog(BaseModel):
    id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    level: str
    message: str
    backup_id: Optional[str] = None
    task_type: str
    details: Dict[str, Any] = Field(default_factory=dict)


class RetentionPolicy(BaseModel):
    daily: int = 30
    weekly: int = 12
    monthly: int = 12
    yearly: int = 3


class NotificationConfig(BaseModel):
    enabled: bool = False
    on_success: bool = False
    on_failure: bool = True
    on_partial: bool = True
    email: Optional[List[str]] = None
    webhook_url: Optional[str] = None


class BackupConfigModel(BaseModel):
    enabled: bool = True
    sources: List[str]
    backup_dir: str
    schedule: str = "0 2 * * *"
    encryption_key: Optional[str] = None
    storage_type: str = "local"
    s3_endpoint: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_region: str = "us-east-1"
    regions: List[str] = Field(default_factory=lambda: ["default"])
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    compression_level: int = 6
    incremental: bool = True
    verify_after_backup: bool = True
    max_parallel_uploads: int = 3
