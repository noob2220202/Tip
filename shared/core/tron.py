"""TRON 주소 처리: base58check 검증과 hex 주소 변환. 지갑을 다루는 봇들이 같이 쓴다."""
from __future__ import annotations

import hashlib
import re

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_TRON_RE = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


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
    return _checksum(raw[:21]) == raw[21:]


def hex_to_address(h: str) -> str | None:
    """TronGrid가 주는 hex 주소('41' + 40자)를 'T...' 주소로. 형식이 다르면 None."""
    h = h.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", h):
        h = "41" + h
    if not re.fullmatch(r"41[0-9a-f]{40}", h):
        return None
    payload = bytes.fromhex(h)
    n = int.from_bytes(payload + _checksum(payload), "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return out


def address_to_hex(addr: str) -> str | None:
    """'T...' 주소를 hex('41' + 40자, 소문자)로. 올바른 주소가 아니면 None. 블록 안의 거래와 대조할 때 쓴다."""
    if not is_tron_address(addr):
        return None
    n = 0
    for ch in addr:
        n = n * 58 + _B58.index(ch)
    return n.to_bytes(25, "big")[:21].hex()


def find_address(text: str | None) -> str | None:
    """글 안에서 체크섬이 맞는 TRON 주소를 찾아 마지막 것을 돌려준다. 없으면 None."""
    found = [m.group(0) for m in re.finditer(r"(?<![1-9A-HJ-NP-Za-km-z])" + _TRON_RE.pattern + r"(?![1-9A-HJ-NP-Za-km-z])",
                                             text or "")]
    return next((a for a in reversed(found) if is_tron_address(a)), None)
