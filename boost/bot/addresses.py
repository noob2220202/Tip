"""TRON 주소 검증과 부스트 대상(그룹·채널) 링크 정리."""
from __future__ import annotations

import hashlib
import re

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_TRON_RE = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")


def is_tron_address(s: str) -> bool:
    """형식(T로 시작하는 34자) + base58check 체크섬까지 확인한다."""
    if not _TRON_RE.fullmatch(s):
        return False
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes(25, "big")
    if raw[0] != 0x41:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]


_NAME = r"[A-Za-z][A-Za-z0-9_]{4,31}"
_AT_RE = re.compile(rf"@({_NAME})")
_URL_RE = re.compile(
    rf"(?:https?://)?(?:t|telegram)\.me/(\+[\w-]{{6,}}|joinchat/[\w-]{{6,}}|{_NAME})/?(?:\?\S*)?", re.I
)


def parse_target(text: str) -> str | None:
    """@아이디, t.me/아이디, t.me/+초대코드 중 하나를 'https://t.me/…'로 정리한다. 아니면 None."""
    t = text.strip()
    m = _AT_RE.fullmatch(t)
    if m:
        return f"https://t.me/{m.group(1)}"
    m = _URL_RE.search(t)
    if m:
        return f"https://t.me/{m.group(1)}"
    return None
