import os
import base64
import hashlib
import hmac
from typing import Optional, Tuple
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend


class CryptoManager:
    def __init__(self, encryption_key: Optional[str] = None):
        self._encryption_key = encryption_key
        self._salt = b"backup_system_salt_v1"
        self._iteration = 100_000
        self._key_length = 32

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self._key_length,
            salt=salt,
            iterations=self._iteration,
            backend=default_backend(),
        )
        return kdf.derive(password.encode("utf-8"))

    def _get_key(self, salt: bytes) -> bytes:
        if self._encryption_key:
            return self._derive_key(self._encryption_key, salt)
        env_key = os.environ.get("BACKUP_ENCRYPTION_KEY")
        if env_key:
            return self._derive_key(env_key, salt)
        return self._derive_key("default_backup_key_change_me", salt)

    def encrypt(self, data: bytes) -> bytes:
        salt = os.urandom(16)
        key = self._get_key(salt)
        nonce = os.urandom(12)
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()
        tag = encryptor.tag
        encrypted = salt + nonce + tag + ciphertext
        return base64.b64encode(encrypted)

    def decrypt(self, encrypted_data: bytes) -> bytes:
        try:
            raw = base64.b64decode(encrypted_data)
            salt = raw[:16]
            nonce = raw[16:28]
            tag = raw[28:44]
            ciphertext = raw[44:]
            key = self._get_key(salt)
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
            decryptor = cipher.decryptor()
            return decryptor.update(ciphertext) + decryptor.finalize()
        except Exception as e:
            raise ValueError(f"Decryption failed: {str(e)}")

    def encrypt_file(self, input_path: Path, output_path: Path) -> None:
        with open(input_path, "rb") as f:
            data = f.read()
        encrypted = self.encrypt(data)
        with open(output_path, "wb") as f:
            f.write(encrypted)

    def decrypt_file(self, input_path: Path, output_path: Path) -> None:
        with open(input_path, "rb") as f:
            encrypted_data = f.read()
        decrypted = self.decrypt(encrypted_data)
        with open(output_path, "wb") as f:
            f.write(decrypted)


def compute_sha256(data: bytes) -> str:
    sha256_hash = hashlib.sha256()
    sha256_hash.update(data)
    return sha256_hash.hexdigest()


def compute_file_sha256(file_path: Path, chunk_size: int = 8192) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def compute_hmac_sha256(data: bytes, key: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def generate_encryption_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
