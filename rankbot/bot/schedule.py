"""발표 일정 계산. 한국 시간(KST, UTC+9 고정)으로만 다룬다. 서버 시간대와 상관없다."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WEEKDAYS = "월화수목금토일"

_TIME = re.compile(r"^\s*(?:(오전|오후)\s*)?(\d{1,2})\s*(?:(?::|시)\s*(\d{1,2})?\s*분?)?\s*$")


def next_occurrence(weekday: int, hour: int, minute: int, after: float) -> int:
    """`after` 이후 처음 오는 (요일, 시각)의 유닉스 시간."""
    at = datetime.fromtimestamp(after, KST)
    cand = at.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=(weekday - at.weekday()) % 7)
    if cand <= at:
        cand += timedelta(days=7)
    return int(cand.timestamp())


def parse_time(text: str) -> tuple[int, int] | None:
    """'21:00', '9:30', '21시', '오후 9시 30분' 같은 입력을 (시, 분)으로. 못 읽으면 None."""
    m = _TIME.match(text or "")
    if not m:
        return None
    ampm, h, mi = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if mi > 59:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ampm == "오후" else 0)
    if h > 23:
        return None
    return h, mi


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
