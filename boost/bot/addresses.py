"""부스트 대상(그룹·채널) 링크 정리. 공개 아이디 링크만 받는다(초대 링크 불가)."""
from __future__ import annotations

import re

from core.tron import is_tron_address  # noqa: F401 - 다른 곳에서 bot.addresses로 불러 쓴다

_NAME = r"[A-Za-z][A-Za-z0-9_]{4,31}"
_AT_RE = re.compile(rf"@({_NAME})")
_URL_RE = re.compile(
    rf"(?:https?://)?(?:t|telegram)\.me/({_NAME})/?(?:\?\S*)?", re.I
)


# t.me/ 뒤에 올 수 있지만 그룹·채널 아이디가 아닌 경로
_RESERVED = {"joinchat", "addstickers", "addemoji", "addlist", "addtheme", "share", "proxy", "socks", "login", "iv",
             "setlanguage", "boost", "invoice", "giftcode"}


def parse_target(text: str) -> str | None:
    """@아이디 또는 t.me/아이디를 'https://t.me/아이디'로 정리한다. 아니면 None."""
    t = text.strip()
    m = _AT_RE.fullmatch(t)
    if m:
        return f"https://t.me/{m.group(1)}"
    m = _URL_RE.search(t)
    if m and m.group(1).lower() not in _RESERVED:
        return f"https://t.me/{m.group(1)}"
    return None
