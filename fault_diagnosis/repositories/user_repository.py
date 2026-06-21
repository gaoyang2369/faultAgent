"""Read-only file user repository with PBKDF2 password verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field

from ..common.paths import RUN_STATE_DIR
from ..security.contracts import Role

PASSWORD_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$ZmQtdXNlci1kdW1teS12MQ$"
    "v2WTUmF7kzHpkw63E2A1x5zetrCtxoA2BHwvTF-tMn8"
)


class UserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str
    role: Role = "engineer"
    display_name: str = ""
    asset_scope: list[str] = Field(default_factory=list)
    table_scope: list[str] = Field(default_factory=list)
    system_scope: list[str] = Field(default_factory=list)
    location_scope: list[str] = Field(default_factory=list)
    kb_scopes: list[str] = Field(default_factory=list)
    enabled: bool = True


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt_bytes).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 100_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


class FileUserRepository:
    """Loads users on every lookup so operations can rotate assignments safely."""

    def __init__(self, *, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.getenv("USER_STORE_PATH") or Path(RUN_STATE_DIR) / "users.json")
        self._lock = RLock()

    def _load(self) -> list[UserRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
        if not isinstance(payload, list):
            return []
        records: list[UserRecord] = []
        for item in payload:
            try:
                record = UserRecord.model_validate(item)
            except Exception:
                continue
            if record.enabled:
                records.append(record)
        return records

    def find_by_username(self, username: str) -> UserRecord | None:
        normalized = (username or "").strip().casefold()
        return next((user for user in self._load() if user.username.casefold() == normalized), None)

    def find_by_user_id(self, user_id: str) -> UserRecord | None:
        normalized = (user_id or "").strip()
        return next((user for user in self._load() if user.user_id == normalized), None)

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        user = self.find_by_username(username)
        encoded = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        password_matches = verify_password(password, encoded)
        return user if user is not None and password_matches else None
