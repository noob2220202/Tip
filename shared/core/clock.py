"""시각 입력 해석. 여러 봇이 같이 쓴다."""
from __future__ import annotations

import re

_TIME = re.compile(r"^\s*(?:(오전|오후)\s*)?(\d{1,2})\s*(?:(?::|시)\s*(\d{1,2})?\s*분?)?\s*$")


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
