from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReminderPlan:
    day_type: str
    first_briefing_at: datetime
    second_reminder_at: Optional[datetime]


def build_reminder_plan(
    target_date: date,
    timezone: ZoneInfo,
    preferences: dict,
    settings: dict,
) -> ReminderPlan:
    day_type = preferences["day_type"]
    if day_type == "auto":
        day_type = "school_day" if target_date.isoweekday() <= 5 else "rest_day"

    suffix = "weekday" if day_type == "school_day" else "rest_day"
    first_at = _at_time(
        target_date,
        settings["first_briefing_{}_time".format(suffix)],
        timezone,
    )

    second_at = None
    if (
        preferences["homework_reminders_enabled"]
        and preferences["second_reminder_mode"] != "disabled"
    ):
        if preferences["second_reminder_mode"] == "custom":
            second_time = preferences["second_reminder_time"]
        else:
            second_time = settings[
                "second_reminder_{}_time".format(suffix)
            ]
        second_at = _at_time(target_date, second_time, timezone)

    return ReminderPlan(day_type, first_at, second_at)


def build_briefing_message(
    items: Sequence[dict],
    weekly_courses: Sequence[dict],
    plan: ReminderPlan,
    now: datetime,
    child_name: str,
    template: Optional[str] = None,
) -> str:
    schedule_summary = build_schedule_summary(items, weekly_courses)
    next_reminder = (
        _spoken_time(plan.second_reminder_at.strftime("%H:%M"))
        if plan.second_reminder_at is not None and plan.second_reminder_at > now
        else "今天不再安排第二次提醒"
    )
    if template is not None:
        return render_speech_template(
            template,
            {
                "孩子称呼": child_name,
                "今天安排简报": schedule_summary,
                "下一次提醒时间": next_reminder,
            },
        )

    parts = []
    courses = [
        {
            "title": item["title"],
            "scheduled_time": item["scheduled_time"],
            "preparation_minutes": item["preparation_minutes"],
        }
        for item in items
        if item["item_type"] == "course" and item["status"] == "pending"
    ]
    courses.extend(
        {
            "title": course["name"],
            "scheduled_time": course["start_time"],
            "preparation_minutes": course["preparation_minutes"],
        }
        for course in weekly_courses
    )
    courses.sort(key=lambda item: item["scheduled_time"] or "99:99")
    if courses:
        first = courses[0]
        text = "今天{}有{}".format(
            _spoken_time(first["scheduled_time"]),
            first["title"],
        )
        if first["preparation_minutes"]:
            text += "，需要提前{}分钟准备".format(first["preparation_minutes"])
        parts.append(text + "。")

    homework = [
        item["title"]
        for item in items
        if item["item_type"] == "homework" and item["status"] == "pending"
    ]
    if homework:
        parts.append("今天有{}。".format(_summarize_names(homework, "项作业")))

    temporary = [
        item["title"]
        for item in items
        if item["item_type"] == "temporary" and item["status"] == "pending"
    ]
    if temporary:
        parts.append("另外还有{}。".format(_summarize_names(temporary, "项安排")))

    if not parts:
        parts.append("今天还没有录入作业或课程。")

    if plan.second_reminder_at is not None and plan.second_reminder_at > now:
        parts.append(
            "你可以先安排吃饭和休息，我会在{}再提醒你。".format(
                _spoken_time(plan.second_reminder_at.strftime("%H:%M"))
            )
        )
    elif plan.day_type == "rest_day":
        parts.append("今天按休息日安排，不用着急开始。")
    return "{}，{}".format(child_name, "".join(parts))


def build_second_reminder_message(
    items: Sequence[dict],
    scheduled_at: datetime,
    child_name: str,
    template: Optional[str] = None,
) -> str:
    pending_tasks = build_pending_task_list(items)
    if template is not None:
        return render_speech_template(
            template,
            {
                "孩子称呼": child_name,
                "当前时间": _spoken_time(scheduled_at.strftime("%H:%M")),
                "待完成任务列表": pending_tasks,
            },
        )
    homework = [
        item["title"]
        for item in items
        if item["item_type"] == "homework" and item["status"] == "pending"
    ]
    prefix = "现在是{}。".format(_spoken_time(scheduled_at.strftime("%H:%M")))
    if homework:
        message = prefix + "今天还有{}，可以开始准备了。".format(
            _summarize_names(homework, "项作业")
        )
    else:
        message = prefix + "可以看一下今天还没有完成的安排。"
    return "{}，{}".format(child_name, message)


def build_schedule_summary(
    items: Sequence[dict],
    weekly_courses: Sequence[dict],
) -> str:
    parts = []
    courses = [
        {
            "title": item["title"],
            "scheduled_time": item["scheduled_time"],
            "preparation_minutes": item["preparation_minutes"],
        }
        for item in items
        if item["item_type"] == "course" and item["status"] == "pending"
    ]
    courses.extend(
        {
            "title": course["name"],
            "scheduled_time": course["start_time"],
            "preparation_minutes": course["preparation_minutes"],
        }
        for course in weekly_courses
    )
    courses.sort(key=lambda item: item["scheduled_time"] or "99:99")
    if courses:
        first = courses[0]
        text = "今天{}有{}".format(
            _spoken_time(first["scheduled_time"]),
            first["title"],
        )
        if first["preparation_minutes"]:
            text += "，需要提前{}分钟准备".format(
                first["preparation_minutes"]
            )
        parts.append(text + "。")

    homework = [
        item["title"]
        for item in items
        if item["item_type"] == "homework" and item["status"] == "pending"
    ]
    if homework:
        parts.append("今天有{}。".format(_summarize_names(homework, "项作业")))

    temporary = [
        item["title"]
        for item in items
        if item["item_type"] == "temporary" and item["status"] == "pending"
    ]
    if temporary:
        parts.append("另外还有{}。".format(_summarize_names(temporary, "项安排")))

    return "".join(parts) if parts else "今天还没有录入作业或课程。"


def build_pending_task_list(items: Sequence[dict]) -> str:
    names = [
        item["title"]
        for item in items
        if item["item_type"] != "course" and item["status"] == "pending"
    ]
    return _summarize_names(names, "项任务") if names else "需要完成的安排"


def render_speech_template(template: str, variables: dict[str, str]) -> str:
    rendered = template
    for name, value in variables.items():
        rendered = rendered.replace("{" + name + "}", value)
    return rendered


def _at_time(target_date: date, value: str, timezone: ZoneInfo) -> datetime:
    parsed = time.fromisoformat(value)
    return datetime.combine(target_date, parsed, timezone)


def _spoken_time(value: Optional[str]) -> str:
    if not value:
        return ""
    hours, minutes = value.split(":", 1)
    if minutes == "00":
        return "{}点".format(int(hours))
    return "{}点{}分".format(int(hours), int(minutes))


def _summarize_names(names: Sequence[str], remainder_label: str) -> str:
    if len(names) <= 3:
        return "、".join(names)
    return "{}等{}{}".format("、".join(names[:3]), len(names), remainder_label)
