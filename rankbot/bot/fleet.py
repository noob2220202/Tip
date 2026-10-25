"""고객 봇들을 한 프로세스 안에서 같이 돌린다. 봇마다 Bot·Dispatcher·폴링 태스크가 하나씩이다."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import User

from bot import store
from bot.child import build_router
from bot.crypto import TokenBox

log = logging.getLogger(__name__)

Revoked = Callable[[int, str], Awaitable[None]]


class Fleet:
    def __init__(self, db: aiosqlite.Connection, box: TokenBox, api_base: str | None = None, polling: bool = True,
                 manager_username: str = "", on_revoked: Revoked | None = None):
        self.db, self.box, self.api_base, self.polling = db, box, api_base, polling
        self.manager_username, self.on_revoked = manager_username, on_revoked
        self.bots: dict[int, Bot] = {}
        self.dps: dict[int, Dispatcher] = {}
        self.tasks: dict[int, asyncio.Task] = {}

    def make_bot(self, token: str) -> Bot:
        session = AiohttpSession(api=TelegramAPIServer.from_base(self.api_base, is_local=False)) if self.api_base else None
        return Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def probe(self, token: str) -> User:
        """토큰이 살아 있는지 getMe로 확인한다. 잘못됐으면 TelegramUnauthorizedError 등이 그대로 올라간다."""
        bot = self.make_bot(token)
        try:
            return await bot.get_me()
        finally:
            await bot.session.close()

    async def start_all(self) -> None:
        for row in await store.active_bots(self.db):
            try:
                await self.start(row["bot_id"], self.box.open(row["token_enc"]))
            except Exception:  # noqa: BLE001 - 봇 하나가 못 떠도 나머지는 떠야 한다
                log.exception("고객 봇 %s를 시작하지 못했습니다", row["bot_id"])

    async def start(self, bot_id: int, token: str) -> None:
        await self.stop(bot_id)
        bot = self.make_bot(token)
        dp = Dispatcher()
        dp.workflow_data.update(db=self.db, fleet=self)
        dp.include_router(build_router(self.manager_username))
        self.bots[bot_id], self.dps[bot_id] = bot, dp
        if self.polling:
            self.tasks[bot_id] = asyncio.create_task(self._run(bot_id, bot, dp), name=f"rank-{bot_id}")

    async def _run(self, bot_id: int, bot: Bot, dp: Dispatcher) -> None:
        wait = 3
        while True:
            try:
                await dp.start_polling(bot, handle_signals=False, close_bot_session=False,
                                       allowed_updates=dp.resolve_used_update_types())
                return
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                await self.revoke(bot_id)
                return
            except Exception:  # noqa: BLE001 - 일시 오류는 기다렸다 다시 붙는다
                log.exception("고객 봇 %s 폴링 오류. %d초 뒤 다시 시도합니다", bot_id, wait)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60)

    async def stop(self, bot_id: int) -> None:
        task = self.tasks.pop(bot_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.dps.pop(bot_id, None)
        bot = self.bots.pop(bot_id, None)
        if bot:
            await bot.session.close()

    async def stop_all(self) -> None:
        for bot_id in list(self.bots):
            await self.stop(bot_id)

    async def revoke(self, bot_id: int) -> None:
        """토큰이 폐기됐다. 봇을 멈추고 주인에게 알린다."""
        row = await store.get_bot(self.db, bot_id)
        if not row or row["status"] == "revoked":
            return
        await store.update_bot(self.db, bot_id, status="revoked")
        # 폴링 태스크 안에서 불렸을 수 있어 자기 자신을 기다리지 않도록 따로 멈춘다
        task = self.tasks.pop(bot_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        self.dps.pop(bot_id, None)
        bot = self.bots.pop(bot_id, None)
        if bot:
            await bot.session.close()
        log.warning("고객 봇 %s의 토큰이 폐기돼 멈췄습니다", bot_id)
        if self.on_revoked:
            await self.on_revoked(row["owner_id"], row["username"])

    async def health(self) -> None:
        """토큰이 살아 있는지 주기적으로 확인한다. 폴링 중에는 폐기를 놓칠 수 있어서다."""
        for bot_id, bot in list(self.bots.items()):
            try:
                await bot.get_me()
            except TelegramUnauthorizedError:
                await self.revoke(bot_id)
            except Exception:  # noqa: BLE001 - 네트워크 문제는 다음 점검에 다시 본다
                log.warning("고객 봇 %s 점검 실패", bot_id)
