"""run_bot(): Bot/Dispatcher/미들웨어/라우터 조립 + 롱폴링 + 에러 핸들러."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ChatMemberUpdated, ErrorEvent, Message

from core import admin, users
from core.broadcast import flow, report
from core.broadcast.sender import CampaignSender
from core.config import BotConfig, load_bot_config
from core.db import open_db
from core.logging_setup import setup_logging
from core.style import esc
from core.subscription import SubscriptionGate, self_check
from core.texts import Texts

log = logging.getLogger(__name__)


def _core_router() -> Router:
    """/start, /help와 차단/해제 감지. 봇 라우터보다 뒤에 붙어서 봇이 덮어쓸 수 있다."""
    router = Router(name="core_basic")

    @router.message(CommandStart())
    async def start(message: Message, db: Any, texts: Texts) -> None:
        u = message.from_user
        await users.register(db, u.id, u.username)
        await message.answer(texts.get("start", name=esc(u.first_name)))

    @router.message(Command("help"))
    async def help_(message: Message, texts: Texts) -> None:
        await message.answer(texts.get("help"))

    @router.my_chat_member()
    async def blocked(event: ChatMemberUpdated, db: Any) -> None:
        if event.chat.type != "private":
            return
        gone = event.new_chat_member.status in (ChatMemberStatus.KICKED, ChatMemberStatus.LEFT)
        await users.set_active(db, event.from_user.id, not gone)

    return router


def build_bot(cfg: BotConfig) -> Bot:
    session = None
    if cfg.api_base:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.api_base, is_local=False))
    return Bot(cfg.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def assemble(
    cfg: BotConfig,
    bot: Bot,
    db: Any,
    routers: Iterable[Router],
    texts: Texts,
    data: Mapping[str, Any] | None = None,
) -> tuple[Dispatcher, CampaignSender]:
    """Dispatcher를 조립한다. 테스트에서도 같은 경로를 쓴다."""
    dp = Dispatcher(storage=MemoryStorage())
    sender = CampaignSender(bot, db, texts, cfg.common.rate_per_sec)
    dp.workflow_data.update(db=db, cfg=cfg, texts=texts, sender=sender, **(data or {}))

    gate = SubscriptionGate(cfg, db, texts)
    gate.dp = dp
    dp.update.outer_middleware(gate)

    dp.include_router(admin.build_router(cfg.admin_ids))
    dp.include_router(flow.build_router(cfg.admin_ids))
    dp.include_router(report.build_router(cfg.admin_ids))
    for r in routers:
        dp.include_router(r)
    dp.include_router(_core_router())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.error("핸들러 오류", exc_info=event.exception)
        msg = event.update.message or (event.update.callback_query.message if event.update.callback_query else None)
        if isinstance(msg, Message):
            try:
                await msg.answer(texts.get("error"))
            except Exception:  # noqa: BLE001 - 에러 안내가 또 실패해도 봇은 계속 돌아야 한다
                pass
        return True

    return dp, sender


async def _warn_gate_problems(bot: Bot, cfg: BotConfig, texts: Texts) -> None:
    """필수 채널 점검에서 문제가 나오면 로그에 남기고 관리자에게 알린다."""
    problems = await self_check(bot, cfg)
    if not problems:
        return
    for p in problems:
        log.error("필수 채널 점검 실패: %s", p)
    text = texts.get("gate_selfcheck_failed", problems="\n".join(f"• {esc(p)}" for p in problems), mode=cfg.common.fail_mode)
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 - 알림이 실패해도 봇은 떠야 한다
            log.warning("관리자 %s에게 점검 결과를 보내지 못했습니다", admin_id)


async def _main(
    bot_dir: Path,
    routers: Sequence[Router],
    text_overrides: Mapping[str, Any] | None,
    schema: str,
    data: Mapping[str, Any] | None,
    on_startup: Callable[[Bot, Any, BotConfig], Awaitable[Mapping[str, Any] | None]] | None,
) -> None:
    cfg = load_bot_config(bot_dir)
    db = await open_db(cfg.db_path, schema)
    bot = build_bot(cfg)
    texts = Texts(text_overrides)
    extra = dict(data or {})
    if on_startup:
        extra.update(await on_startup(bot, db, cfg) or {})
    dp, sender = await assemble(cfg, bot, db, routers, texts, extra)
    try:
        me = await bot.get_me()
        log.info("%s 시작 (@%s)", cfg.name, me.username)
        await _warn_gate_problems(bot, cfg, texts)
        await sender.resume_all()
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await sender.stop()
        await bot.session.close()
        await db.close()


def run_bot(
    bot_dir: Path | str,
    routers: Sequence[Router] = (),
    texts: Mapping[str, Any] | None = None,
    schema: str = "",
    data: Mapping[str, Any] | None = None,
    on_startup: Callable[[Bot, Any, BotConfig], Awaitable[Mapping[str, Any] | None]] | None = None,
) -> None:
    """봇 폴더의 main.py에서 이 한 줄로 공통 기능을 붙인다.

    texts: 공통 안내문을 덮어쓸 {키: [문구...]}
    schema: 봇 전용 테이블 SQL (매 시작마다 실행되므로 IF NOT EXISTS로 작성)
    data: 핸들러에 키워드 인자로 주입할 값
    on_startup: (bot, db, cfg)를 받아 추가 주입값 dict를 돌려주는 async 훅
    """
    setup_logging()
    try:
        asyncio.run(_main(Path(bot_dir), routers, texts, schema, data, on_startup))
    except (KeyboardInterrupt, SystemExit):
        pass
