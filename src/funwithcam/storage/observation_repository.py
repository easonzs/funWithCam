import json
from datetime import datetime
from typing import Optional

from .database import Database


class ObservationRepository:
    def __init__(self, database: Database):
        self._database = database

    def record(
        self,
        study_session_id: int,
        event_type: str,
        candidate_started_at: datetime,
        confirmed_at: datetime,
        confidence: Optional[float],
        deviation: Optional[float],
        reminded: bool,
        suppression_reason: Optional[str],
        payload: Optional[dict] = None,
    ) -> int:
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO observation_events (
                    study_session_id,
                    event_type,
                    candidate_started_at,
                    confirmed_at,
                    average_confidence,
                    average_deviation,
                    maximum_deviation,
                    reminded,
                    suppression_reason,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    study_session_id,
                    event_type,
                    candidate_started_at.isoformat(),
                    confirmed_at.isoformat(),
                    confidence,
                    deviation,
                    deviation,
                    int(reminded),
                    suppression_reason,
                    json.dumps(payload or {}, ensure_ascii=False),
                    confirmed_at.isoformat(),
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def recent_count(
        self,
        study_session_id: int,
        event_type: str,
        since: datetime,
    ) -> int:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count
                FROM observation_events
                WHERE study_session_id = ?
                  AND event_type = ?
                  AND confirmed_at >= ?
                """,
                (
                    study_session_id,
                    event_type,
                    since.isoformat(),
                ),
            ).fetchone()
        return int(row["event_count"])

    def last_reminded_at(
        self,
        study_session_id: int,
        event_type: str,
    ) -> Optional[datetime]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT confirmed_at
                FROM observation_events
                WHERE study_session_id = ?
                  AND event_type = ?
                  AND reminded = 1
                ORDER BY confirmed_at DESC, id DESC
                LIMIT 1
                """,
                (study_session_id, event_type),
            ).fetchone()
        if row is None or row["confirmed_at"] is None:
            return None
        return datetime.fromisoformat(row["confirmed_at"])
