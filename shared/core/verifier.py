"""채널 가입 검수 서비스.

검수 봇(채널 관리자)이 서버에서 `getChatMember`로 가입 여부를 확인하고, 같은 서버의 다른 봇들은
127.0.0.1로 "이 유저가 가입했나?"만 묻는다. 그러면 채널에는 검수 봇 하나만 관리자로 두면 되고,
새 봇을 만들어도 채널 설정이 필요 없다.

텔레그램 Bot API는 채널 전체 멤버 목록을 주지 않으므로, 목록 비교가 아니라 유저 한 명씩 확인한다.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Sequence

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from core.config import Channel

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Verify-Secret"
JOINED = {"creator", "administrator", "member"}


class VerifierError(Exception):
    """검수 서비스에 연결하지 못했거나 응답이 올바르지 않을 때."""


def is_joined(member) -> bool:
    return member.status in JOINED or (member.status == "restricted" and getattr(member, "is_member", False))


# ---- 서버 쪽 (검수 봇 프로세스) ----------------------------------------------

async def check_user(bot: Bot, channels: Sequence[Channel], user_id: int) -> dict[str, list[str]]:
    """{"missing": [가입 안 한 채널], "errors": [확인 자체가 실패한 채널]} (chat_id 문자열)"""
    missing: list[str] = []
    errors: list[str] = []
    for ch in channels:
        try:
            m = await bot.get_chat_member(ch.chat_id, user_id)
        except TelegramAPIError as e:
            log.warning("getChatMember 실패 chat=%s: %s", ch.chat_id, e)
            errors.append(str(ch.chat_id))
            continue
        if not is_joined(m):
            missing.append(str(ch.chat_id))
    return {"missing": missing, "errors": errors}


async def channel_problems(bot: Bot, channels: Sequence[Channel]) -> list[str]:
    """검수 봇이 각 채널의 관리자인지 점검한다. 관리자가 아니면 가입 여부를 알 수 없다."""
    problems: list[str] = []
    if not channels:
        return problems
    me = await bot.me()
    for ch in channels:
        try:
            m = await bot.get_chat_member(ch.chat_id, me.id)
        except TelegramAPIError as e:
            problems.append(f"{ch.chat_id}: 채널을 조회할 수 없습니다 ({e}). 채널 주소와 봇 초대 여부를 확인해 주십시오.")
            continue
        if m.status not in ("administrator", "creator"):
            problems.append(f"{ch.chat_id}: 봇이 관리자가 아닙니다(현재 {m.status}). 채널 관리자로 추가해 주십시오.")
    return problems


def build_app(bot: Bot, channels: Sequence[Channel], secret: str) -> web.Application:
    if not secret:
        raise RuntimeError("VERIFY_SECRET이 비어 있어요. .env에 길고 무작위인 값을 적어 주세요.")

    @web.middleware
    async def auth(request: web.Request, handler):
        if not hmac.compare_digest(request.headers.get(SECRET_HEADER, ""), secret):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def check(request: web.Request) -> web.Response:
        try:
            user_id = int(request.query["user_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "user_id required"}, status=400)
        return web.json_response(await check_user(bot, channels, user_id))

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"problems": await channel_problems(bot, channels)})

    app = web.Application(middlewares=[auth])
    app.router.add_get("/check", check)
    app.router.add_get("/health", health)
    return app


async def start_server(bot: Bot, channels: Sequence[Channel], secret: str, port: int) -> web.AppRunner:
    """127.0.0.1에만 바인딩한다. 외부에서는 접근할 수 없다."""
    runner = web.AppRunner(build_app(bot, channels, secret))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    log.info("검수 서비스 시작 127.0.0.1:%s", port)
    return runner


# ---- 클라이언트 쪽 (다른 봇의 게이트) ----------------------------------------

class VerifierClient:
    def __init__(self, url: str, secret: str, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.secret = secret
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, **params) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as http:
                async with http.get(self.url + path, params=params, headers={SECRET_HEADER: self.secret}) as r:
                    if r.status != 200:
                        raise VerifierError(f"검수 서비스 응답 오류 {r.status}")
                    return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise VerifierError(f"검수 서비스에 연결할 수 없습니다: {e}") from e

    async def check(self, user_id: int) -> dict[str, list[str]]:
        return await self._get("/check", user_id=user_id)

    async def health(self) -> list[str]:
        return (await self._get("/health")).get("problems", [])
