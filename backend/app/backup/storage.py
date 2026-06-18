import os
import json
import shutil
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from abc import ABC, abstractmethod
from datetime import datetime

try:
    import boto3
    from botocore.exceptions import ClientError
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False

from .crypto import compute_file_sha256


class StorageAdapter(ABC):
    @abstractmethod
    def upload(self, local_path: Path, remote_key: str) -> bool:
        pass

    @abstractmethod
    def download(self, remote_key: str, local_path: Path) -> bool:
        pass

    @abstractmethod
    def exists(self, remote_key: str) -> bool:
        pass

    @abstractmethod
    def delete(self, remote_key: str) -> bool:
        pass

    @abstractmethod
    def list(self, prefix: str = "") -> List[str]:
        pass

    @abstractmethod
    def get_size(self, remote_key: str) -> int:
        pass

    @abstractmethod
    def verify(self, remote_key: str, expected_checksum: str) -> bool:
        pass


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, remote_key: str) -> Path:
        return self.base_dir / remote_key

    def upload(self, local_path: Path, remote_key: str) -> bool:
        try:
            dest = self._get_full_path(remote_key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest)
            return True
        except Exception:
            return False

    def download(self, remote_key: str, local_path: Path) -> bool:
        try:
            src = self._get_full_path(remote_key)
            if not src.exists():
                return False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, local_path)
            return True
        except Exception:
            return False

    def exists(self, remote_key: str) -> bool:
        return self._get_full_path(remote_key).exists()

    def delete(self, remote_key: str) -> bool:
        try:
            path = self._get_full_path(remote_key)
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            return True
        except Exception:
            return False

    def list(self, prefix: str = "") -> List[str]:
        try:
            search_dir = self._get_full_path(prefix)
            if not search_dir.exists():
                return []
            result = []
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    full_path = Path(root) / file
                    rel_path = full_path.relative_to(self.base_dir)
                    result.append(str(rel_path).replace("\\", "/"))
            return sorted(result)
        except Exception:
            return []

    def get_size(self, remote_key: str) -> int:
        try:
            path = self._get_full_path(remote_key)
            return path.stat().st_size if path.exists() else 0
        except Exception:
            return 0

    def verify(self, remote_key: str, expected_checksum: str) -> bool:
        try:
            path = self._get_full_path(remote_key)
            if not path.exists():
                return False
            actual_checksum = compute_file_sha256(path)
            return actual_checksum == expected_checksum
        except Exception:
            return False


class S3StorageAdapter(StorageAdapter):
    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = None,
        region: str = "us-east-1",
    ):
        if not S3_AVAILABLE:
            raise ImportError("boto3 is required for S3 storage. Install with: pip install boto3")

        self.bucket = bucket
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url,
            region_name=region,
        )

    def upload(self, local_path: Path, remote_key: str) -> bool:
        try:
            self.s3_client.upload_file(str(local_path), self.bucket, remote_key)
            return True
        except Exception:
            return False

    def download(self, remote_key: str, local_path: Path) -> bool:
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.s3_client.download_file(self.bucket, remote_key, str(local_path))
            return True
        except Exception:
            return False

    def exists(self, remote_key: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=remote_key)
            return True
        except ClientError:
            return False

    def delete(self, remote_key: str) -> bool:
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=remote_key)
            return True
        except Exception:
            return False

    def list(self, prefix: str = "") -> List[str]:
        try:
            result = []
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                if "Contents" in page:
                    for obj in page["Contents"]:
                        result.append(obj["Key"])
            return sorted(result)
        except Exception:
            return []

    def get_size(self, remote_key: str) -> int:
        try:
            response = self.s3_client.head_object(Bucket=self.bucket, Key=remote_key)
            return response["ContentLength"]
        except Exception:
            return 0

    def verify(self, remote_key: str, expected_checksum: str) -> bool:
        try:
            response = self.s3_client.head_object(Bucket=self.bucket, Key=remote_key)
            etag = response.get("ETag", "").strip('"')
            if "-" in etag:
                return True
            return etag == expected_checksum
        except Exception:
            return False


class MultiRegionStorage:
    def __init__(self, config: Any):
        self.config = config
        self._adapters: Dict[str, StorageAdapter] = {}
        self._initialize_adapters()

    def _initialize_adapters(self) -> None:
        cfg = self.config.get_config()
        backup_dir = Path(cfg.backup_dir)

        if cfg.storage_type == "s3":
            if not S3_AVAILABLE:
                raise ImportError("boto3 is required for S3 storage")
            for region in cfg.regions:
                self._adapters[region] = S3StorageAdapter(
                    bucket=cfg.s3_bucket or "",
                    access_key=cfg.s3_access_key or "",
                    secret_key=cfg.s3_secret_key or "",
                    endpoint_url=cfg.s3_endpoint,
                    region=cfg.s3_region,
                )
        else:
            for region in cfg.regions:
                region_dir = backup_dir / region
                self._adapters[region] = LocalStorageAdapter(region_dir)

    def get_adapter(self, region: str = "default") -> StorageAdapter:
        if region not in self._adapters:
            if "default" in self._adapters:
                return self._adapters["default"]
            raise ValueError(f"No storage adapter found for region: {region}")
        return self._adapters[region]

    def upload_to_all(self, local_path: Path, remote_key: str) -> Dict[str, bool]:
        results = {}
        for region, adapter in self._adapters.items():
            results[region] = adapter.upload(local_path, remote_key)
        return results

    def download_from_any(self, remote_key: str, local_path: Path) -> Tuple[Optional[str], bool]:
        for region, adapter in self._adapters.items():
            if adapter.exists(remote_key):
                if adapter.download(remote_key, local_path):
                    return region, True
        return None, False

    def delete_from_all(self, remote_key: str) -> Dict[str, bool]:
        results = {}
        for region, adapter in self._adapters.items():
            results[region] = adapter.delete(remote_key)
        return results

    def list_all(self, prefix: str = "") -> Dict[str, List[str]]:
        results = {}
        for region, adapter in self._adapters.items():
            results[region] = adapter.list(prefix)
        return results

    def verify_all(self, remote_key: str, expected_checksum: str) -> Dict[str, bool]:
        results = {}
        for region, adapter in self._adapters.items():
            results[region] = adapter.verify(remote_key, expected_checksum)
        return results


def create_storage_adapter(config: Any, region: str = "default") -> StorageAdapter:
    multi_region = MultiRegionStorage(config)
    return multi_region.get_adapter(region)
