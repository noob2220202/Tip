"""카카오 이모티콘샵 링크 파싱과 이미지 수집 (kakaot/server.js의 Python 포팅)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

LINK_RE = re.compile(r"(?:e|emoticon)\.kakao\.com/(?:t|items)/([A-Za-z0-9_-]+)")
API_URL = "https://e.kakao.com/api/items/{key}"
HOST_RE = re.compile(r"(^|\.)(kakao\.com|kakaocdn\.net|daumcdn\.net)$", re.I)
UA = {"User-Agent": "Mozilla/5.0"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class KakaoError(Exception):
    """사용자에게 그대로 보여줄 수 있는 메시지를 담는다."""


@dataclass
class Pack:
    key: str
    title: str
    animated: list[str] = field(default_factory=list)
    stills: list[str] = field(default_factory=list)
    items: list[tuple[str, str]] = field(default_factory=list)  # (url, "animated"|"still") 원래 순서
    logged_in: bool = False


def parse_link(text: str) -> str | None:
    m = LINK_RE.search(text or "")
    return m.group(1) if m else None


def is_kakao_host(url: str) -> bool:
    try:
        return bool(HOST_RE.search(urlparse(url).hostname or "")) and urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def detect_kind(buf: bytes) -> str | None:
    """이미지 바이트로 정지/움직임을 판별한다. WEBP는 ANIM 청크가 있으면 움직이는 것."""
    if buf[:8] == b"\x89PNG\r\n\x1a\n" or buf[:2] == b"\xff\xd8":
        return "still"
    if buf[:3] == b"GIF":
        return "animated"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "animated" if b"ANIM" in buf else "still"
    return None


class Session:
    """session.json(Playwright storage state)에서 kakao.com 쿠키를 읽는다."""

    def __init__(self, path: Path | None):
        self.path = path

    def _cookies(self) -> list[dict]:
        if not self.path or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text("utf8"))
        except (OSError, ValueError):
            return []
        return [c for c in data.get("cookies", []) if re.search(r"\.?kakao\.com$", c.get("domain", ""), re.I)]

    def cookie_header(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}" for c in self._cookies())

    def logged_in(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return any(c.get("expires", -1) == -1 or c["expires"] > now for c in self._cookies())


class KakaoClient:
    def __init__(self, session: Session, api_url: str = API_URL):
        self.session = session
        self.api_url = api_url

    async def _sniff(self, http: aiohttp.ClientSession, url: str) -> str | None:
        try:
            async with http.get(url, headers={"Range": "bytes=0-127"}) as r:
                if r.status not in (200, 206):
                    return None
                return detect_kind(await r.content.read(128))
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def fetch_pack(self, key: str) -> Pack:
        timeout = aiohttp.ClientTimeout(total=30)
        cookie = self.session.cookie_header()
        async with aiohttp.ClientSession(timeout=timeout) as http:
            try:
                async with http.get(
                    self.api_url.format(key=key),
                    headers={**UA, **({"Cookie": cookie} if cookie else {})},
                ) as r:
                    if r.status != 200:
                        log.warning("카카오 API 실패 key=%s status=%s", key, r.status)
                        raise KakaoError("이모티콘 정보를 못 찾았어요. 링크를 다시 확인해 줘요.")
                    js = await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as e:
                log.warning("카카오 연결 실패 key=%s: %s", key, e)
                raise KakaoError("카카오 쪽에 연결이 안 돼요. 조금 있다가 다시 보내줘요.") from e

            items = ((js or {}).get("contents") or {}).get("items") or []
            urls = [it["animatedUrl"] for it in items if isinstance(it, dict) and isinstance(it.get("animatedUrl"), str)]
            urls = [u for u in urls if is_kakao_host(u)]
            if not urls:
                raise KakaoError("이모티콘 이미지를 못 찾았어요. 링크를 다시 확인해 줘요.")

            kinds = await asyncio.gather(*(self._sniff(http, u) for u in urls))

        pack = Pack(key=key, title=((js.get("hero") or {}).get("title") or "").strip() or key,
                    logged_in=self.session.logged_in())
        for u, k in zip(urls, kinds):
            if k == "animated":
                pack.animated.append(u)
            elif k == "still":
                pack.stills.append(u)
            else:
                continue
            pack.items.append((u, k))
        return pack

    async def download(self, url: str) -> bytes:
        if not is_kakao_host(url):
            raise KakaoError("허용되지 않은 주소예요.")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(url) as r:
                if r.status != 200:
                    raise KakaoError("이미지를 받다가 실패했어요.")
                buf = await r.content.read(MAX_IMAGE_BYTES + 1)
                if len(buf) > MAX_IMAGE_BYTES:
                    raise KakaoError("이미지가 너무 커요.")
                return buf
