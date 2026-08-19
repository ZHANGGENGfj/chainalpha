from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class AuthError(ValueError):
    """Expected authentication or authorization error."""


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "role": self.role, "is_admin": self.is_admin}


class AuthService:
    """SQLite-backed user, invite-code and signed-cookie service.

    It deliberately has no external dependency: this keeps local deployments
    inexpensive while still avoiding plain-text passwords or invite codes.
    """

    cookie_name = "chainalpha_session"

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        secret: str | None = None,
        bootstrap_invite: str | None = None,
        bootstrap_invite_max_uses: int | None = None,
        session_days: int | None = None,
    ) -> None:
        configured_path = database_path or os.getenv("AUTH_DATABASE_PATH") or "data/auth/chainalpha_auth.sqlite3"
        self.database_path = self._resolve_path(configured_path)
        self.secret = (secret or os.getenv("AUTH_SECRET") or secrets.token_urlsafe(48)).encode("utf-8")
        self.bootstrap_invite = (bootstrap_invite if bootstrap_invite is not None else os.getenv("AUTH_BOOTSTRAP_INVITE", "")).strip()
        self.bootstrap_invite_max_uses = max(1, bootstrap_invite_max_uses or _env_int("AUTH_BOOTSTRAP_INVITE_MAX_USES", 10))
        self.session_days = max(1, session_days or _env_int("AUTH_SESSION_DAYS", 7))

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS invite_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    created_by INTEGER
                );
                """
            )
        if self.bootstrap_invite:
            self.ensure_invite(self.bootstrap_invite, label="bootstrap", max_uses=self.bootstrap_invite_max_uses)

    def register(self, *, username: str, password: str, invite_code: str) -> CurrentUser:
        username = _normalize_username(username)
        _validate_password(password)
        invite_hash = _invite_hash(invite_code)
        now = _now()
        with self._connect() as connection:
            invite = connection.execute("SELECT * FROM invite_codes WHERE code_hash = ?", (invite_hash,)).fetchone()
            if invite is None or not invite["enabled"]:
                raise AuthError("邀请码无效或已停用")
            if invite["expires_at"] and str(invite["expires_at"]) < now:
                raise AuthError("邀请码已过期")
            if int(invite["used_count"]) >= int(invite["max_uses"]):
                raise AuthError("邀请码使用次数已耗尽")
            existing = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing is not None:
                raise AuthError("用户名已被使用")
            total_users = int(connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"])
            role = "admin" if total_users == 0 else "user"
            cursor = connection.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, _hash_password(password), role, now),
            )
            connection.execute("UPDATE invite_codes SET used_count = used_count + 1 WHERE id = ?", (invite["id"],))
            user_id = int(cursor.lastrowid)
        return CurrentUser(id=user_id, username=username, role=role)

    def authenticate(self, *, username: str, password: str) -> CurrentUser:
        username = _normalize_username(username)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row is None or not bool(row["active"]) or not _verify_password(password, str(row["password_hash"])):
                raise AuthError("用户名或密码错误")
            connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        return CurrentUser(id=int(row["id"]), username=str(row["username"]), role=str(row["role"]))

    def get_user(self, user_id: int) -> CurrentUser | None:
        with self._connect() as connection:
            row = connection.execute("SELECT id, username, role, active FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not bool(row["active"]):
            return None
        return CurrentUser(id=int(row["id"]), username=str(row["username"]), role=str(row["role"]))

    def create_invite(
        self,
        *,
        created_by: CurrentUser,
        code: str | None = None,
        label: str = "",
        max_uses: int = 1,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if not created_by.is_admin:
            raise AuthError("仅管理员可以创建邀请码")
        raw_code = (code or _generate_invite_code()).strip()
        if not raw_code:
            raise AuthError("邀请码不能为空")
        max_uses = max(1, min(int(max_uses), 10000))
        self._insert_invite(raw_code, label=label, max_uses=max_uses, expires_at=expires_at, created_by=created_by.id)
        return {"code": raw_code, "label": label, "max_uses": max_uses, "expires_at": expires_at}

    def ensure_invite(self, code: str, *, label: str, max_uses: int) -> None:
        normalized = _normalize_invite(code)
        if not normalized:
            return
        with self._connect() as connection:
            exists = connection.execute("SELECT id FROM invite_codes WHERE code_hash = ?", (_invite_hash(normalized),)).fetchone()
        if exists is None:
            self._insert_invite(normalized, label=label, max_uses=max_uses, expires_at=None, created_by=None)

    def list_invites(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, label, max_uses, used_count, expires_at, enabled, created_at FROM invite_codes ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def issue_session(self, user: CurrentUser) -> str:
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.session_days)
        payload = {"sub": user.id, "username": user.username, "role": user.role, "exp": int(expires_at.timestamp())}
        encoded = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_b64(signature)}"

    def read_session(self, token: str | None) -> CurrentUser | None:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        expected = _b64(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            payload = json.loads(_unb64(encoded).decode("utf-8"))
            if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
                return None
            return self.get_user(int(payload["sub"]))
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _insert_invite(self, code: str, *, label: str, max_uses: int, expires_at: str | None, created_by: int | None) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO invite_codes(code_hash, label, max_uses, expires_at, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                    (_invite_hash(code), label.strip()[:100], max_uses, expires_at, _now(), created_by),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthError("邀请码已存在") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _resolve_path(value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(_b64(digest), expected)
    except (ValueError, TypeError):
        return False


def _normalize_username(value: str) -> str:
    username = value.strip()
    if not 3 <= len(username) <= 40:
        raise AuthError("用户名长度应为 3 到 40 个字符")
    if any(char.isspace() for char in username):
        raise AuthError("用户名不能包含空格")
    return username


def _validate_password(value: str) -> None:
    if len(value) < 8:
        raise AuthError("密码至少需要 8 个字符")


def _normalize_invite(value: str) -> str:
    return value.strip().upper()


def _invite_hash(value: str) -> str:
    return hashlib.sha256(_normalize_invite(value).encode("utf-8")).hexdigest()


def _generate_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    chunks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(chunks)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
