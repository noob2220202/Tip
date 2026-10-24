"""관리자 전용 기능: AdminFilter, /stats, /campaigns."""
from __future__ import annotations

import time

import aiosqlite
from aiogram import Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from core import users
from core.broadcast import models
from core.style import b, esc
from core.texts import Texts


class AdminFilter(BaseFilter):
    def __init__(self, admin_ids: frozenset[int]):
        self.admin_ids = admin_ids

    async def __call__(self, event: Message) -> bool:
        return bool(event.from_user and event.from_user.id in self.admin_ids)


def build_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="core_admin")
    router.message.filter(AdminFilter(admin_ids))

    @router.message(Command("stats"))
    async def stats(message: Message, db: aiosqlite.Connection, texts: Texts) -> None:
        await message.answer(texts.get("stats", **await users.stats(db)))

    @router.message(Command("campaigns"))
    async def campaigns(message: Message, db: aiosqlite.Connection) -> None:
        rows = await models.list_campaigns(db, limit=15)
        if not rows:
            await message.answer("아직 만든 캠페인이 없어요.")
            return
        lines = []
        for r in rows:
            when = time.strftime("%m-%d %H:%M", time.gmtime(r["created_at"] + 9 * 3600))
            lines.append(f"#{r['id']} {esc(r['title'])} · {b(r['status'])} · 대상 {r['target_count']} · {when}")
        await message.answer("\n".join(lines))

    return router
