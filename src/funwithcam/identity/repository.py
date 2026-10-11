import json
from datetime import datetime
from typing import List, Sequence

from funwithcam.storage.database import Database

from .matcher import FaceTemplate, normalize_embedding


VALID_ROLES = ("child", "parent")
VALID_PROFILE_TYPES = ("test", "formal")


class FaceProfileRepository:
    def __init__(self, database: Database):
        self._database = database

    def replace(
        self,
        role: str,
        name: str,
        profile_type: str,
        embedding: Sequence[float],
        updated_at: datetime,
    ) -> int:
        if role not in VALID_ROLES:
            raise ValueError("role must be child or parent")
        if profile_type not in VALID_PROFILE_TYPES:
            raise ValueError("profile_type must be test or formal")
        if not name.strip():
            raise ValueError("name cannot be empty")

        timestamp = updated_at.isoformat()
        payload = json.dumps(normalize_embedding(embedding))
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO face_profiles (
                    role,
                    name,
                    profile_type,
                    embedding_json,
                    active,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(role, name, profile_type) DO UPDATE SET
                    embedding_json = excluded.embedding_json,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (role, name.strip(), profile_type, payload, timestamp, timestamp),
            )
            row = connection.execute(
                """
                SELECT id
                FROM face_profiles
                WHERE role = ? AND name = ? AND profile_type = ?
                """,
                (role, name.strip(), profile_type),
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_active(self, profile_type: str) -> List[FaceTemplate]:
        if profile_type not in VALID_PROFILE_TYPES:
            raise ValueError("profile_type must be test or formal")
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, role, name, embedding_json
                FROM face_profiles
                WHERE profile_type = ? AND active = 1
                ORDER BY id
                """,
                (profile_type,),
            ).fetchall()
        return [
            FaceTemplate(
                profile_id=int(row["id"]),
                role=row["role"],
                name=row["name"],
                embedding=tuple(json.loads(row["embedding_json"])),
            )
            for row in rows
        ]
