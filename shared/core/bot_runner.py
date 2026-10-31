"""run_bot(): Bot/Dispatcher/미들웨어/라우터 조립 + 롱폴링 + 에러 핸들러."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
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

# 종료(SIGINT/SIGTERM) 때 진행 중인 요청이 끝나기를 기다리는 최대 시간(초). pm2 kill_timeout보다 짧게 둔다.
DRAIN_SECONDS = 120


class InflightTracker(BaseMiddleware):
    """처리 중인 update 작업을 기억한다. aiogram은 종료할 때 이 작업들을 기다리지 않고 취소하기 때문에,
    스티커를 만들던 중에 봇이 재시작되면 작업이 그대로 죽는다. 종료 전에 끝나기를 기다리는 데 쓴다."""

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task] = set()

    async def __call__(self, handler, event, data):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            return await handler(event, data)
        finally:
            self.tasks.discard(task)


async def drain(tracker: InflightTracker, timeout: float = DRAIN_SECONDS) -> int:
    """진행 중인 작업이 끝나기를 기다린다. 시간 안에 못 끝난 작업 수를 돌려준다."""
    current = asyncio.current_task()
    pending = {t for t in tracker.tasks if t is not current}
    if not pending:
        return 0
    log.info("진행 중인 작업 %d개가 끝나기를 기다립니다 (최대 %d초)", len(pending), timeout)
    _, left = await asyncio.wait(pending, timeout=timeout)
    if left:
        log.warning("시간 안에 끝나지 못한 작업 %d개를 취소합니다", len(left))
    return len(left)


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
    gate_enabled: bool = True,
    pre_gate: Sequence[BaseMiddleware] = (),
) -> tuple[Dispatcher, CampaignSender]:
    """Dispatcher를 조립한다. 테스트에서도 같은 경로를 쓴다."""
    dp = Dispatcher(storage=MemoryStorage())
    sender = CampaignSender(bot, db, texts, cfg.common.rate_per_sec)
    dp.workflow_data.update(db=db, cfg=cfg, texts=texts, sender=sender, **(data or {}))

    tracker = InflightTracker()
    dp.update.outer_middleware(tracker)    # 가장 바깥에서 모든 update를 추적
    dp["tracker"] = tracker
    for mw in pre_gate:                    # 구독 확인과 상관없이 모든 update에 먼저 돌아야 하는 봇 전용 미들웨어
        dp.update.outer_middleware(mw)
    if gate_enabled:
        gate = SubscriptionGate(cfg, db, texts)
        gate.dp = dp
        dp.update.outer_middleware(gate)
        dp["gate"] = gate                  # 비즈니스 연결 안내처럼 핸들러가 직접 가입 여부를 볼 때 쓴다

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


async def _warn_gate_problems(bot: Bot, cfg: BotConfig, texts: Texts, direct: bool) -> None:
    """필수 채널 점검에서 문제가 나오면 로그에 남기고 관리자에게 알린다."""
    problems = await self_check(bot, cfg, direct=direct)
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
    gate: bool,
    on_shutdown: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None,
    pre_gate: Sequence[BaseMiddleware] = (),
) -> None:
    cfg = load_bot_config(bot_dir)
    db = await open_db(cfg.db_path, schema)
    bot = build_bot(cfg)
    texts = Texts(text_overrides)
    extra = dict(data or {})
    if on_startup:
        extra.update(await on_startup(bot, db, cfg) or {})
    dp, sender = await assemble(cfg, bot, db, routers, texts, extra, gate_enabled=gate, pre_gate=pre_gate)
    check_task = None
    try:
        me = await bot.get_me()
        log.info("%s 시작 (@%s)", cfg.name, me.username)
        # 검수 봇이 아직 안 떠 있을 수 있어 재시도하므로 폴링을 막지 않게 백그라운드로 돌린다
        check_task = asyncio.create_task(_warn_gate_problems(bot, cfg, texts, direct=not gate))
        await sender.resume_all()
        # 세션은 진행 중인 작업이 끝난 뒤에 직접 닫는다 (close_bot_session=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), close_bot_session=False)
    finally:
        if check_task:
            check_task.cancel()
        await drain(dp["tracker"])
        if on_shutdown:
            await on_shutdown(extra)
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
    gate: bool = True,
    on_shutdown: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None,
    pre_gate: Sequence[BaseMiddleware] = (),
) -> None:
    """봇 폴더의 main.py에서 이 한 줄로 공통 기능을 붙인다.

    texts: 공통 안내문을 덮어쓸 {키: [문구...]}
    schema: 봇 전용 테이블 SQL (매 시작마다 실행되므로 IF NOT EXISTS로 작성)
    data: 핸들러에 키워드 인자로 주입할 값
    on_startup: (bot, db, cfg)를 받아 추가 주입값 dict를 돌려주는 async 훅
    gate: False면 채널 가입 게이트를 붙이지 않는다 (검수 봇 자신용)
    on_shutdown: 종료할 때 DB를 닫기 전에 불린다. on_startup이 돌려준 주입값 dict를 받는다 (백그라운드 작업 정리용)
    pre_gate: 구독 게이트보다 먼저 모든 update에 붙일 미들웨어 (게이트에 막히는 update도 봐야 하는 기록용)
    """
    setup_logging()
    try:
        asyncio.run(_main(Path(bot_dir), routers, texts, schema, data, on_startup, gate, on_shutdown, pre_gate))
    except (KeyboardInterrupt, SystemExit):
        pass
