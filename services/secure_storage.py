from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    configured_key = str(getattr(settings, "FILE_ENCRYPTION_KEY", "") or "").strip()
    if configured_key:
        try:
            return Fernet(configured_key.encode("utf-8"))
        except Exception:
            pass

    secret = str(getattr(settings, "SECRET_KEY", "dev-secret-key")).encode("utf-8")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(derived_key)


@deconstructible
class EncryptedFileSystemStorage(FileSystemStorage):
    def __init__(self, location=None, base_url=None):
        default_location = getattr(settings, "PRIVATE_MEDIA_ROOT", Path(getattr(settings, "BASE_DIR", Path.cwd())) / "private_media")
        super().__init__(location=location or default_location, base_url=base_url)

    def _save(self, name, content):
        if hasattr(content, "seek"):
            content.seek(0)

        raw = content.read()
        if hasattr(content, "seek"):
            content.seek(0)

        encrypted = _fernet().encrypt(raw)
        return super()._save(name, ContentFile(encrypted))

    def open(self, name, mode="rb"):
        encrypted_file = super().open(name, "rb")
        try:
            encrypted = encrypted_file.read()
        finally:
            encrypted_file.close()

        decrypted = _fernet().decrypt(encrypted)
        return ContentFile(decrypted, name=Path(name).name)


encrypted_private_storage = EncryptedFileSystemStorage()
