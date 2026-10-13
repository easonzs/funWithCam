from datetime import datetime
import re
from typing import Optional

from .database import Database


DEFAULT_TEMPLATE_VARIABLES = {
    "孩子称呼": ("家庭与成员", "读取家庭设置中的 child_name"),
    "今天安排简报": ("日程", "根据当天课程、学校作业和打卡任务自动生成"),
    "下一次提醒时间": ("提醒规则", "根据当天提醒计划和当前状态计算"),
    "当前时间": ("运行时状态", "使用实际触发时间生成"),
    "待完成任务列表": ("日程", "根据当天仍未完成的任务自动生成"),
}


class SpeechTemplateRepository:
    def __init__(self, database: Database):
        self._database = database

    def list_all(self) -> list[dict]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM speech_templates
                ORDER BY rowid
                """
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, event_key: str) -> Optional[dict]:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM speech_templates
                WHERE event_key = ?
                """,
                (event_key,),
            ).fetchone()
        return None if row is None else self._decode(row)

    def save(
        self,
        event_key: str,
        template_text: str,
        enabled: bool,
        now: datetime,
    ) -> None:
        if not template_text.strip():
            raise ValueError("话术模板不能为空")
        variables = set(
            re.findall(r"\{([^{}]+)\}", template_text)
        )
        unknown = variables - set(DEFAULT_TEMPLATE_VARIABLES)
        if unknown:
            raise ValueError(
                "不支持的话术变量：{}".format("、".join(sorted(unknown)))
            )
        with self._database.connection() as connection:
            updated = connection.execute(
                """
                UPDATE speech_templates
                SET template_text = ?, enabled = ?, updated_at = ?
                WHERE event_key = ?
                """,
                (
                    template_text.strip(),
                    int(enabled),
                    now.isoformat(),
                    event_key,
                ),
            )
            if not updated.rowcount:
                raise ValueError("未知话术事件")
            connection.commit()

    @staticmethod
    def _decode(row) -> dict:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result
