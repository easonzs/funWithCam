import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from funwithcam.calibration_steps import (
    CALIBRATION_STEP_BY_KEY,
    CALIBRATION_STEP_KEYS,
)

from .database import Database


ACTIVE_STATUSES = ("ready", "capturing", "review", "error")


class CalibrationRepository:
    def __init__(self, database: Database):
        self._database = database

    def create_draft(self, now: datetime) -> dict:
        timestamp = now.isoformat()
        draft_id = uuid.uuid4().hex
        steps = {
            key: {
                "status": "pending",
                "label": CALIBRATION_STEP_BY_KEY[key]["label"],
                "attempts": [],
            }
            for key in CALIBRATION_STEP_KEYS
        }
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._active_study_session(connection) is not None:
                raise ValueError("学习记录进行中，请结束学习后再录入基准")
            if self._active_draft(connection) is not None:
                raise ValueError("已有正在进行的基准录入")
            connection.execute(
                """
                INSERT INTO calibration_drafts (
                    id, status, current_step, steps_json,
                    error_message, created_at, updated_at
                ) VALUES (?, 'ready', ?, ?, NULL, ?, ?)
                """,
                (
                    draft_id,
                    CALIBRATION_STEP_KEYS[0],
                    json.dumps(steps, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM calibration_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
        return self._decode_draft(row)

    def get_active(self) -> Optional[dict]:
        with self._database.connection() as connection:
            row = self._active_draft(connection)
        return self._decode_draft(row)

    def get_latest_completed_draft(self) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM calibration_drafts
                WHERE status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._decode_draft(row)

    def get_latest_version(self) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM calibration_versions
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["calibration"] = json.loads(result.pop("calibration_json"))
        result["quality"] = json.loads(result.pop("quality_json"))
        return result

    def get_version(self, version_id: Optional[int]) -> Optional[dict]:
        if version_id is None:
            return None
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM calibration_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["calibration"] = json.loads(result.pop("calibration_json"))
        result["quality"] = json.loads(result.pop("quality_json"))
        return result

    def has_active_study_session(self) -> bool:
        with self._database.connection() as connection:
            return self._active_study_session(connection) is not None

    def mark_capturing(
        self,
        draft_id: str,
        step: str,
        now: datetime,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_draft(connection, draft_id)
            if self._active_study_session(connection) is not None:
                raise ValueError("学习记录进行中，请结束学习后再录入基准")
            if row["status"] not in ("ready", "error"):
                raise ValueError("当前步骤不能开始采集")
            if row["current_step"] != step or step not in CALIBRATION_STEP_KEYS:
                raise ValueError("请按顺序录入基准动作")
            steps = json.loads(row["steps_json"])
            steps[step]["status"] = "capturing"
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'capturing', steps_json = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False),
                    timestamp,
                    draft_id,
                ),
            )
            connection.commit()

    def save_capture(
        self,
        draft_id: str,
        step: str,
        result: dict,
        now: datetime,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_draft(connection, draft_id)
            if row["status"] != "capturing" or row["current_step"] != step:
                raise ValueError("当前步骤不在采集中")
            steps = json.loads(row["steps_json"])
            step_data = steps[step]
            attempt = {
                "original_filename": result["original_filename"],
                "annotated_filename": result["annotated_filename"],
                "features": result["features"],
                "quality": result["quality"],
                "camera_fingerprint": result["camera_fingerprint"],
                "captured_at": timestamp,
            }
            step_data.setdefault("attempts", []).append(attempt)
            step_data.update(attempt)
            step_data["status"] = "review"
            steps[step] = step_data
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'review', steps_json = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False),
                    timestamp,
                    draft_id,
                ),
            )
            connection.commit()

    def fail_capture(
        self,
        draft_id: str,
        step: str,
        message: str,
        now: datetime,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_draft(connection, draft_id)
            if row["status"] != "capturing" or row["current_step"] != step:
                return
            steps = json.loads(row["steps_json"])
            steps[step]["status"] = "error"
            steps[step]["error_message"] = message
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'error', steps_json = ?,
                    error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False),
                    message,
                    timestamp,
                    draft_id,
                ),
            )
            connection.commit()

    def retry_step(self, draft_id: str, now: datetime) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_draft(connection, draft_id)
            if row["status"] not in ("review", "error"):
                raise ValueError("当前步骤不能重新采集")
            steps = json.loads(row["steps_json"])
            step = row["current_step"]
            steps[step]["status"] = "pending"
            steps[step].pop("error_message", None)
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'ready', steps_json = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False),
                    timestamp,
                    draft_id,
                ),
            )
            connection.commit()

    def confirm_step(self, draft_id: str, now: datetime) -> Optional[int]:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_draft(connection, draft_id)
            if row["status"] != "review":
                raise ValueError("当前步骤还没有可确认的采集结果")
            steps = json.loads(row["steps_json"])
            step = row["current_step"]
            steps[step]["status"] = "confirmed"
            step_index = CALIBRATION_STEP_KEYS.index(step)
            if step_index + 1 < len(CALIBRATION_STEP_KEYS):
                next_step = CALIBRATION_STEP_KEYS[step_index + 1]
                connection.execute(
                    """
                    UPDATE calibration_drafts
                    SET status = 'ready', current_step = ?, steps_json = ?,
                        error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_step,
                        json.dumps(steps, ensure_ascii=False),
                        timestamp,
                        draft_id,
                    ),
                )
                connection.commit()
                return None

            calibration = {
                "draft_id": draft_id,
                "steps": {
                    key: {
                        "features": steps[key]["features"],
                        "original_filename": steps[key]["original_filename"],
                        "annotated_filename": steps[key]["annotated_filename"],
                    }
                    for key in CALIBRATION_STEP_KEYS
                },
            }
            quality = {
                key: steps[key]["quality"] for key in CALIBRATION_STEP_KEYS
            }
            fingerprints = [
                steps[key].get("camera_fingerprint")
                for key in CALIBRATION_STEP_KEYS
                if steps[key].get("camera_fingerprint")
            ]
            cursor = connection.execute(
                """
                INSERT INTO calibration_versions (
                    calibration_json, quality_json,
                    camera_fingerprint, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    json.dumps(calibration, ensure_ascii=False),
                    json.dumps(quality, ensure_ascii=False),
                    fingerprints[-1] if fingerprints else None,
                    timestamp,
                ),
            )
            version_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'completed', steps_json = ?,
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(steps, ensure_ascii=False),
                    timestamp,
                    draft_id,
                ),
            )
            connection.commit()
        return version_id

    def cancel(self, draft_id: str, now: datetime) -> None:
        with self._database.connection() as connection:
            row = self._require_draft(connection, draft_id)
            if row["status"] not in ACTIVE_STATUSES:
                raise ValueError("当前基准录入不能取消")
            connection.execute(
                """
                UPDATE calibration_drafts
                SET status = 'cancelled', updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), draft_id),
            )
            connection.commit()

    def recover_interrupted(self, now: datetime) -> int:
        message = "服务在采集过程中重新启动，请重新采集当前步骤"
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM calibration_drafts
                WHERE status = 'capturing'
                """
            ).fetchall()
            for row in rows:
                steps = json.loads(row["steps_json"])
                step = row["current_step"]
                steps[step]["status"] = "error"
                steps[step]["error_message"] = message
                connection.execute(
                    """
                    UPDATE calibration_drafts
                    SET status = 'error', steps_json = ?,
                        error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(steps, ensure_ascii=False),
                        message,
                        now.isoformat(),
                        row["id"],
                    ),
                )
            connection.commit()
        return len(rows)

    def image_filename_allowed(self, draft_id: str, filename: str) -> bool:
        if Path(filename).name != filename:
            return False
        draft = self.get_draft(draft_id)
        if draft is None:
            return False
        for step in draft["steps"].values():
            candidates = (
                step.get("original_filename"),
                step.get("annotated_filename"),
            )
            if filename in candidates:
                return True
            for attempt in step.get("attempts", []):
                if filename in (
                    attempt.get("original_filename"),
                    attempt.get("annotated_filename"),
                ):
                    return True
        return False

    def _decode_draft(self, row) -> Optional[dict]:
        if row is None:
            return None
        result = dict(row)
        result["steps"] = json.loads(result.pop("steps_json"))
        return result

    def _require_draft(self, connection, draft_id: str):
        row = connection.execute(
            "SELECT * FROM calibration_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise ValueError("找不到这次基准录入")
        return row

    def _active_draft(self, connection):
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        return connection.execute(
            """
            SELECT *
            FROM calibration_drafts
            WHERE status IN ({})
            ORDER BY created_at DESC
            LIMIT 1
            """.format(placeholders),
            ACTIVE_STATUSES,
        ).fetchone()

    def _active_study_session(self, connection):
        return connection.execute(
            """
            SELECT id
            FROM study_sessions
            WHERE finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
