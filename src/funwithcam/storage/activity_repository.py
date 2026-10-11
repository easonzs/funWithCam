import json
from datetime import datetime
from typing import Optional

from .database import Database


class ActivityRepository:
    def __init__(self, database: Database):
        self._database = database

    def record(
        self,
        occurred_at: datetime,
        category: str,
        event_type: str,
        source: str,
        status: str,
        title: str,
        summary: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> int:
        timestamp = occurred_at.isoformat()
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO activity_events (
                    event_date,
                    occurred_at,
                    category,
                    event_type,
                    source,
                    status,
                    title,
                    summary,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at.date().isoformat(),
                    timestamp,
                    category,
                    event_type,
                    source,
                    status,
                    title,
                    summary,
                    json.dumps(payload or {}, ensure_ascii=False),
                    timestamp,
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def list_for_date(
        self,
        event_date: str,
        category: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        query = """
            SELECT *
            FROM activity_events
            WHERE event_date = ?
        """
        parameters: list[object] = [event_date]
        if category is not None:
            query += " AND category = ?"
            parameters.append(category)
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self._database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events
