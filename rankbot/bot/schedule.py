"""발표 일정 계산. 한국 시간(KST, UTC+9 고정)으로만 다룬다. 서버 시간대와 상관없다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.clock import parse_time  # noqa: F401 - 이 모듈에서 불러 쓴다

KST = timezone(timedelta(hours=9))
WEEKDAYS = "월화수목금토일"


def next_occurrence(weekday: int, hour: int, minute: int, after: float) -> int:
    """`after` 이후 처음 오는 (요일, 시각)의 유닉스 시간."""
    at = datetime.fromtimestamp(after, KST)
    cand = at.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=(weekday - at.weekday()) % 7)
    if cand <= at:
        cand += timedelta(days=7)
    return int(cand.timestamp())


def clock(hour: int, minute: int) -> str:
    ampm = "오후" if hour >= 12 else "오전"
    return f"{ampm} {hour % 12 or 12}시" + (f" {minute}분" if minute else "")


def schedule_text(weekday: int, hour: int, minute: int) -> str:
    return f"매주 {WEEKDAYS[weekday]}요일 {clock(hour, minute)}"


def stamp(ts: float) -> str:
    """'10/26(월) 21:00'"""
    d = datetime.fromtimestamp(ts, KST)
    return f"{d:%m/%d}({WEEKDAYS[d.weekday()]}) {d:%H:%M}"


def remaining(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = ([f"{days}일"] if days else []) + ([f"{hours}시간"] if hours else []) + [f"{mins}분"]
    return " ".join(parts)
