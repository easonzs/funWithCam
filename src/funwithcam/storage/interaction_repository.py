import json
import re
from datetime import datetime, timedelta
from typing import Optional

from .database import Database


DEFAULT_SPEECH_PHRASES = {
    "start": (
        "开始写作业",
        "开始学习",
        "开始记录",
        "开始作业",
        "我要做作业",
        "我准备学习了",
    ),
    "pause": ("我要休息", "休息一下", "暂停学习"),
    "resume": ("继续学习", "继续写作业", "继续记录"),
    "finish": (
        "结束学习",
        "结束记录",
        "作业写完了",
        "我写完了",
        "写完作业了",
    ),
    "query_homework": (
        "今天有什么作业",
        "今天作业是什么",
        "今天的作业是什么",
    ),
    "query_courses": (
        "今天有什么课",
        "今天的课是什么",
        "今天几点上课",
    ),
    "start_checkin": (
        "英语打卡",
        "英语单词打卡",
        "英语读书打卡",
        "古诗打卡",
        "古诗词背诵",
    ),
    "finish_checkin": ("打卡结束", "结束录制"),
    "ptz_left": ("摄像头向左", "往左转", "镜头向左", "向左看"),
    "ptz_right": ("摄像头向右", "往右转", "镜头向右", "向右看"),
    "ptz_up": ("摄像头向上", "往上看", "镜头向上", "抬头"),
    "ptz_down": ("摄像头向下", "往下看", "镜头向下", "低头"),
    "ptz_arm": ("摄像头调整", "调整摄像头", "开始调整摄像头"),
}


def complete_confirmation_prompt(
    prompt: str,
    confirm_phrases: list[str],
    cancel_phrases: list[str],
) -> str:
    prompt = prompt.strip()
    if not confirm_phrases or not cancel_phrases:
        return prompt
    confirm_phrase = confirm_phrases[0]
    cancel_phrase = cancel_phrases[0]
    if confirm_phrase in prompt and cancel_phrase in prompt:
        return prompt.replace("请回答", "请说", 1)
    base = prompt.rstrip()
    for trailing in ("请回答", "请说"):
        if base.endswith(trailing):
            base = base.removesuffix(trailing).rstrip("，, ")
            break
    return '{}请说“{}”，或者说“{}”。'.format(
        base,
        confirm_phrase,
        cancel_phrase,
    )


class SpeechIntentRepository:
    def __init__(self, database: Database):
        self._database = database

    def list_enabled(self) -> list[dict]:
        return [
            self._decode(row)
            for row in self._rows("WHERE enabled = 1 ORDER BY rowid")
        ]

    def list_all(self) -> list[dict]:
        return [self._decode(row) for row in self._rows("ORDER BY rowid")]

    def get(self, command: str) -> Optional[dict]:
        rows = self._rows("WHERE command = ?", (command,))
        return None if not rows else self._decode(rows[0])

    def save(
        self,
        command: str,
        custom_phrases: list[str],
        keyword_groups: list[list[str]],
        confirmation_mode: str,
        confirmation_prompt: str,
        confirm_phrases: list[str],
        cancel_phrases: list[str],
        enabled: bool,
        now: datetime,
    ) -> None:
        if confirmation_mode not in ("never", "ambiguous", "always"):
            raise ValueError("invalid confirmation mode")
        default_phrases = DEFAULT_SPEECH_PHRASES.get(command)
        if default_phrases is None:
            raise ValueError("unknown speech command")
        if command == "finish_checkin":
            enabled = True
        custom_phrases = [
            phrase
            for phrase in _clean_values(custom_phrases)
            if phrase not in default_phrases
        ]
        phrases = [*default_phrases, *custom_phrases]
        keyword_groups = [
            _clean_values(group) for group in keyword_groups if _clean_values(group)
        ]
        if confirmation_mode != "never" and not confirmation_prompt.strip():
            raise ValueError("需要二次确认的口令必须填写确认话术")
        confirm_phrases = _clean_values(confirm_phrases)
        cancel_phrases = _clean_values(cancel_phrases)
        if confirmation_mode != "never" and (
            not confirm_phrases or not cancel_phrases
        ):
            raise ValueError("需要二次确认的口令必须配置确认和取消回复")
        if confirmation_mode != "never":
            confirmation_prompt = complete_confirmation_prompt(
                confirmation_prompt,
                confirm_phrases,
                cancel_phrases,
            )
        with self._database.connection() as connection:
            updated = connection.execute(
                """
                UPDATE speech_intents
                SET phrases_json = ?, custom_phrases_json = ?,
                    keyword_groups_json = ?,
                    confirmation_mode = ?, confirmation_prompt = ?,
                    confirm_phrases_json = ?, cancel_phrases_json = ?,
                    enabled = ?, updated_at = ?
                WHERE command = ?
                """,
                (
                    json.dumps(phrases, ensure_ascii=False),
                    json.dumps(custom_phrases, ensure_ascii=False),
                    json.dumps(keyword_groups, ensure_ascii=False),
                    confirmation_mode,
                    confirmation_prompt.strip(),
                    json.dumps(confirm_phrases, ensure_ascii=False),
                    json.dumps(cancel_phrases, ensure_ascii=False),
                    int(enabled),
                    now.isoformat(),
                    command,
                ),
            )
            if not updated.rowcount:
                raise ValueError("unknown speech command")
            connection.commit()

    def _rows(self, suffix: str, parameters=()):
        with self._database.connection() as connection:
            return connection.execute(
                "SELECT * FROM speech_intents " + suffix,
                parameters,
            ).fetchall()

    @staticmethod
    def _decode(row) -> dict:
        result = dict(row)
        stored_phrases = json.loads(result.pop("phrases_json"))
        default_phrases = list(
            DEFAULT_SPEECH_PHRASES.get(result["command"], ())
        )
        stored_custom = json.loads(result.pop("custom_phrases_json"))
        custom_phrases = _clean_values(
            phrase
            for phrase in stored_custom
            if phrase not in default_phrases
        )
        if not custom_phrases:
            custom_phrases = _clean_values(
                phrase
                for phrase in stored_phrases
                if phrase not in default_phrases
            )
        result["default_phrases"] = default_phrases
        result["custom_phrases"] = custom_phrases
        result["phrases"] = [*default_phrases, *custom_phrases]
        for key in (
            "keyword_groups_json",
            "confirm_phrases_json",
            "cancel_phrases_json",
        ):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        result["enabled"] = bool(result["enabled"])
        return result


class TaskTypeRepository:
    def __init__(self, database: Database):
        self._database = database

    def list_all(self, enabled_only: bool = False) -> list[dict]:
        query = "SELECT * FROM task_types"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY built_in DESC, rowid"
        with self._database.connection() as connection:
            return [dict(row) for row in connection.execute(query).fetchall()]

    def get(self, task_key: str) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_types WHERE task_key = ?", (task_key,)
            ).fetchone()
            return dict(row) if row is not None else None

    def save(
        self,
        task_key: str,
        label: str,
        category: str,
        completion_mode: str,
        finish_prompt: str,
        include_in_finish_check: bool,
        max_recording_seconds: Optional[int],
        enabled: bool,
        now: datetime,
    ) -> None:
        task_key = task_key.strip()
        label = label.strip()
        if not re.fullmatch(r"[a-z0-9_]{2,40}", task_key):
            raise ValueError("任务标识只能使用小写字母、数字和下划线")
        if not label:
            raise ValueError("任务名称不能为空")
        if category not in ("school_homework", "checkin", "other"):
            raise ValueError("invalid task category")
        if completion_mode not in ("verbal", "video", "parent"):
            raise ValueError("invalid completion mode")
        if completion_mode == "video":
            if max_recording_seconds is None or max_recording_seconds <= 0:
                raise ValueError("视频打卡必须设置最大录制时长")
        else:
            max_recording_seconds = None
        if not finish_prompt.strip():
            raise ValueError("结束检查话术不能为空")
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO task_types (
                    task_key, label, category, completion_mode, finish_prompt,
                    include_in_finish_check, max_recording_seconds, enabled,
                    built_in, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    label = excluded.label,
                    category = excluded.category,
                    completion_mode = excluded.completion_mode,
                    finish_prompt = excluded.finish_prompt,
                    include_in_finish_check = excluded.include_in_finish_check,
                    max_recording_seconds = excluded.max_recording_seconds,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    task_key,
                    label,
                    category,
                    completion_mode,
                    finish_prompt.strip(),
                    int(include_in_finish_check),
                    max_recording_seconds,
                    int(enabled),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()


class ScheduleRepository:
    """模板 × 日期的生效集合与例外/覆盖。

    「任意日期集合打勾」= template_schedule_dates 里该模板的 target_date 集合；
    「某天不生效 / 当天补充内容」= template_schedule_overrides。
    """

    def __init__(self, database: Database):
        self._database = database

    def list_dates(self, task_type_key: str) -> list[str]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT target_date FROM template_schedule_dates
                WHERE task_type_key = ?
                ORDER BY target_date
                """,
                (task_type_key,),
            ).fetchall()
        return [row["target_date"] for row in rows]

    def set_dates(
        self,
        task_type_key: str,
        dates: list[str],
        now: datetime,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                "DELETE FROM template_schedule_dates WHERE task_type_key = ?",
                (task_type_key,),
            )
            for target_date in sorted(set(dates)):
                connection.execute(
                    """
                    INSERT INTO template_schedule_dates (
                        task_type_key, target_date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (task_type_key, target_date, timestamp, timestamp),
                )
            connection.commit()

    def add_date(self, task_type_key: str, target_date: str, now: datetime) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO template_schedule_dates (
                    task_type_key, target_date, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_type_key, target_date) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (task_type_key, target_date, timestamp, timestamp),
            )
            connection.commit()

    def remove_date(self, task_type_key: str, target_date: str) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                DELETE FROM template_schedule_dates
                WHERE task_type_key = ? AND target_date = ?
                """,
                (task_type_key, target_date),
            )
            connection.commit()

    def list_overrides(self, task_type_key: str) -> list[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM template_schedule_overrides
                WHERE task_type_key = ?
                ORDER BY target_date
                """,
                (task_type_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_override(
        self,
        task_type_key: str,
        target_date: str,
        *,
        disabled: bool,
        override_title: Optional[str],
        notes: str,
        now: datetime,
    ) -> None:
        timestamp = now.isoformat()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT INTO template_schedule_overrides (
                    task_type_key, target_date, disabled, override_title,
                    notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_type_key, target_date) DO UPDATE SET
                    disabled = excluded.disabled,
                    override_title = excluded.override_title,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    task_type_key,
                    target_date,
                    int(disabled),
                    override_title or None,
                    notes,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()

    def delete_override(self, task_type_key: str, target_date: str) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                DELETE FROM template_schedule_overrides
                WHERE task_type_key = ? AND target_date = ?
                """,
                (task_type_key, target_date),
            )
            connection.commit()

    def active_for_date(self, target_date: str) -> list[dict]:
        """返回某天应生效的模板清单（含该天覆盖信息）。

        规则：日期集合命中 → 生效；若该天有 override 且 disabled → 不生效；
        override 提供标题/备注时使用覆盖值。返回字段与 task_types 行合并。
        """
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    task_types.*,
                    template_schedule_overrides.disabled
                        AS override_disabled,
                    template_schedule_overrides.override_title,
                    template_schedule_overrides.notes
                        AS override_notes
                FROM task_types
                JOIN template_schedule_dates
                    ON task_types.task_key = template_schedule_dates.task_type_key
                LEFT JOIN template_schedule_overrides
                    ON template_schedule_dates.task_type_key
                        = template_schedule_overrides.task_type_key
                    AND template_schedule_dates.target_date
                        = template_schedule_overrides.target_date
                WHERE template_schedule_dates.target_date = ?
                    AND task_types.enabled = 1
                ORDER BY task_types.rowid
                """,
                (target_date,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item.pop("override_disabled"):
                continue
            item["item_type"] = (
                "homework" if item["category"] == "school_homework" else "checkin"
            )
            item["title"] = item.pop("override_title") or item["label"]
            item["notes"] = item.pop("override_notes") or ""
            item["source"] = "template"
            result.append(item)
        return result


class DialogueRepository:
    def __init__(self, database: Database):
        self._database = database

    def get_active(self, session_date: str) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM dialogue_sessions
                WHERE session_date = ? AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (session_date,),
            ).fetchone()
        return None if row is None else self._decode(row)

    def start(
        self,
        session_date: str,
        dialogue_type: str,
        state: str,
        context: dict,
        prompt: str,
        allowed_replies: list[str],
        now: datetime,
        first_retry_seconds: int,
    ) -> dict:
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE dialogue_sessions
                SET status = 'cancelled', updated_at = ?
                WHERE session_date = ? AND status = 'active'
                """,
                (now.isoformat(), session_date),
            )
            cursor = connection.execute(
                """
                INSERT INTO dialogue_sessions (
                    session_date, dialogue_type, state, context_json,
                    prompt, allowed_replies_json, ask_count, next_prompt_at,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'active', ?, ?)
                """,
                (
                    session_date,
                    dialogue_type,
                    state,
                    json.dumps(context, ensure_ascii=False),
                    prompt,
                    json.dumps(allowed_replies, ensure_ascii=False),
                    (now + timedelta(seconds=first_retry_seconds)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
            dialogue_id = int(cursor.lastrowid)
        return self.get(dialogue_id)

    def get(self, dialogue_id: int) -> dict:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM dialogue_sessions WHERE id = ?",
                (dialogue_id,),
            ).fetchone()
        if row is None:
            raise ValueError("dialogue not found")
        return self._decode(row)

    def update(
        self,
        dialogue_id: int,
        state: str,
        context: dict,
        prompt: str,
        allowed_replies: list[str],
        now: datetime,
        next_retry_seconds: Optional[int],
    ) -> None:
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE dialogue_sessions
                SET state = ?, context_json = ?, prompt = ?,
                    allowed_replies_json = ?, ask_count = 1,
                    next_prompt_at = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (
                    state,
                    json.dumps(context, ensure_ascii=False),
                    prompt,
                    json.dumps(allowed_replies, ensure_ascii=False),
                    (
                        None
                        if next_retry_seconds is None
                        else (
                            now + timedelta(seconds=next_retry_seconds)
                        ).isoformat()
                    ),
                    now.isoformat(),
                    dialogue_id,
                ),
            )
            connection.commit()

    def complete(self, dialogue_id: int, status: str, now: datetime) -> None:
        if status not in ("completed", "cancelled", "expired"):
            raise ValueError("invalid dialogue status")
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE dialogue_sessions
                SET status = ?, next_prompt_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, now.isoformat(), dialogue_id),
            )
            connection.commit()

    def due(self, now: datetime) -> list[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM dialogue_sessions
                WHERE status = 'active' AND next_prompt_at IS NOT NULL
                    AND next_prompt_at <= ?
                ORDER BY next_prompt_at, id
                """,
                (now.isoformat(),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def mark_prompted(
        self,
        dialogue_id: int,
        now: datetime,
        next_retry_seconds: int,
        max_prompts: int,
    ) -> bool:
        dialogue = self.get(dialogue_id)
        next_count = dialogue["ask_count"] + 1
        if next_count > max_prompts:
            self.complete(dialogue_id, "expired", now)
            return False
        with self._database.connection() as connection:
            connection.execute(
                """
                UPDATE dialogue_sessions
                SET ask_count = ?, next_prompt_at = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (
                    next_count,
                    (now + timedelta(seconds=next_retry_seconds)).isoformat(),
                    now.isoformat(),
                    dialogue_id,
                ),
            )
            connection.commit()
        return True

    @staticmethod
    def _decode(row) -> dict:
        result = dict(row)
        result["context"] = json.loads(result.pop("context_json"))
        result["allowed_replies"] = json.loads(
            result.pop("allowed_replies_json")
        )
        return result


class CheckinRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(
        self,
        agenda_item_id: int,
        session_date: str,
        task_type_key: str,
        now: datetime,
    ) -> int:
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO checkin_recordings (
                    agenda_item_id, session_date, task_type_key, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'preparing', ?, ?)
                """,
                (
                    agenda_item_id,
                    session_date,
                    task_type_key,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
        return int(cursor.lastrowid)

    def mark_recording(
        self,
        recording_id: int,
        video_path: str,
        now: datetime,
    ) -> None:
        self._update(
            recording_id,
            "recording",
            now,
            video_path=video_path,
            started_at=now.isoformat(),
        )

    def finish(
        self,
        recording_id: int,
        status: str,
        now: datetime,
        duration_seconds: Optional[float] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if status not in ("saved", "cancelled", "failed"):
            raise ValueError("invalid checkin status")
        self._update(
            recording_id,
            status,
            now,
            ended_at=now.isoformat(),
            duration_seconds=duration_seconds,
            error_message=error_message,
        )

    def _update(
        self,
        recording_id: int,
        status: str,
        now: datetime,
        **values,
    ) -> None:
        columns = ["status = ?", "updated_at = ?"]
        parameters = [status, now.isoformat()]
        for key, value in values.items():
            columns.append("{} = ?".format(key))
            parameters.append(value)
        parameters.append(recording_id)
        with self._database.connection() as connection:
            connection.execute(
                "UPDATE checkin_recordings SET {} WHERE id = ?".format(
                    ", ".join(columns)
                ),
                parameters,
            )
            connection.commit()


def _clean_values(values: list[str]) -> list[str]:
    result = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result
