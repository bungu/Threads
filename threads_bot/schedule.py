"""Japan time schedule calculations shared by the API and worker."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")


def validate_settings(value: dict) -> dict:
    prompt = value.get("prompt")
    days = value.get("days")
    times = value.get("times")
    enabled = value.get("enabled")
    if not isinstance(prompt, str) or len(prompt) > 2400:
        raise ValueError("プロンプトは2,400文字以内にしてください。")
    if not isinstance(enabled, bool):
        raise ValueError("定期投稿の設定が不正です。")
    if (not isinstance(days, list) or len(days) > 7 or
            any(type(day) is not int or day < 0 or day > 6 for day in days) or
            len(set(days)) != len(days)):
        raise ValueError("曜日の設定が不正です。")
    if (not isinstance(times, list) or len(times) > 6 or
            any(not isinstance(t, str) or not TIME_RE.fullmatch(t) for t in times) or
            len(set(times)) != len(times)):
        raise ValueError("時刻の設定が不正、または重複しています。")
    if enabled and (not prompt.strip() or not days or not times):
        raise ValueError("定期投稿をONにするにはプロンプト・曜日・時刻が必要です。")
    return {"prompt": prompt, "days": sorted(days), "times": sorted(times),
            "enabled": enabled, "timezone": "Asia/Tokyo"}


def slots_between(settings: dict, start: datetime, end: datetime) -> list[datetime]:
    """Return UTC slots in (start, end], including a JST date boundary."""
    if not settings.get("enabled"):
        return []
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    day = start.astimezone(JST).date()
    last = end.astimezone(JST).date()
    slots = []
    while day <= last:
        if day.weekday() in settings["days"]:
            for value in settings["times"]:
                hour, minute = map(int, value.split(":"))
                slot = datetime(day.year, day.month, day.day, hour, minute,
                                tzinfo=JST).astimezone(timezone.utc)
                if start < slot <= end:
                    slots.append(slot)
        day += timedelta(days=1)
    return sorted(slots)


def next_slot(settings: dict, now: datetime) -> datetime | None:
    matches = slots_between(settings, now, now + timedelta(days=8))
    return matches[0] if matches else None
