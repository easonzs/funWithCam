import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .database import Database


PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True)
class WebSession:
    username: str
    csrf_token: str


class AuthRepository:
    def __init__(self, database: Database):
        self._database = database

    def has_user(self, username: str) -> bool:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM admin_users
                WHERE username = ?
                """,
                (username.strip(),),
            ).fetchone()
        return row is not None

    def set_password(
        self,
        username: str,
        password: str,
        updated_at: datetime,
    ) -> None:
        username = username.strip()
        if not username:
            raise ValueError("username cannot be empty")
        if len(password) < 8:
            raise ValueError("password must contain at least 8 characters")
        password_hash = _hash_password(password)
        timestamp = updated_at.astimezone(timezone.utc).isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO admin_users (
                    username,
                    password_hash,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    updated_at = excluded.updated_at
                """,
                (username, password_hash, timestamp, timestamp),
            )
            connection.commit()

    def create_session(
        self,
        username: str,
        password: str,
        now: datetime,
        session_hours: int,
    ) -> Optional[str]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, password_hash
                FROM admin_users
                WHERE username = ?
                """,
                (username.strip(),),
            ).fetchone()
            if row is None or not _verify_password(password, row["password_hash"]):
                return None

            token = secrets.token_urlsafe(32)
            token_hash = _token_hash(token)
            now_utc = now.astimezone(timezone.utc)
            expires_at = now_utc + timedelta(hours=session_hours)
            connection.execute(
                "DELETE FROM web_sessions WHERE expires_at <= ?",
                (now_utc.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO web_sessions (
                    token_hash,
                    admin_user_id,
                    expires_at,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    token_hash,
                    int(row["id"]),
                    expires_at.isoformat(),
                    now_utc.isoformat(),
                ),
            )
            connection.commit()
        return token

    def get_session(self, token: str, now: datetime) -> Optional[WebSession]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT admin_users.username, web_sessions.expires_at
                FROM web_sessions
                JOIN admin_users
                  ON admin_users.id = web_sessions.admin_user_id
                WHERE web_sessions.token_hash = ?
                """,
                (_token_hash(token),),
            ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now.astimezone(timezone.utc):
            self.delete_session(token)
            return None
        return WebSession(
            username=row["username"],
            csrf_token=_csrf_token(token),
        )

    def delete_session(self, token: str) -> None:
        with self._database.connection() as connection:
            connection.execute(
                "DELETE FROM web_sessions WHERE token_hash = ?",
                (_token_hash(token),),
            )
            connection.commit()


def csrf_matches(token: str, submitted: str) -> bool:
    return hmac.compare_digest(_csrf_token(token), submitted)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "{}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        iterations_text, salt_hex, expected_hex = encoded.split("$", 2)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _csrf_token(token: str) -> str:
    return hashlib.sha256(("csrf:" + token).encode("utf-8")).hexdigest()
