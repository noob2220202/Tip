"""봇 만들기(토큰 등록)와 내 봇 설정."""
from __future__ import annotations

import logging
import re
import time

import aiosqlite
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter, TelegramUnauthorizedError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from bot import schedule, store
from bot import texts as T
from bot.crypto import TokenBox
from bot.fleet import Fleet
from core.config import BotConfig

log = logging.getLogger(__name__)

router = Router(name="rank_mybots")
router.message.filter(F.chat.type == "private")

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
MAX_PRIZE_LINES, MAX_PRIZE_LEN = 10, 60


class Add(StatesGroup):
    token = State()


class Edit(StatesGroup):
    value = State()


async def _warn(fleet: Fleet, bot_id: int) -> bool:
    """프라이버시 모드가 켜져 있으면(=그룹 일반 채팅을 못 받으면) True."""
    tg = fleet.bots.get(bot_id)
    if tg is None:
        return False
    try:
        return (await tg.get_me()).can_read_all_group_messages is False
    except Exception:  # noqa: BLE001 - 확인 못 하면 경고하지 않는다
        return False


async def _panel(db, fleet: Fleet, bot_id: int) -> tuple[str, object]:
    bot = await store.get_bot(db, bot_id)
    return T.panel(bot, await store.chat_count(db, bot_id), await _warn(fleet, bot_id)), T.panel_kb(bot)


async def _show(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb, link_preview_options=_NO_PREVIEW)
    except TelegramAPIError:
        await cb.message.answer(text, reply_markup=kb, link_preview_options=_NO_PREVIEW)


async def _owned(cb: CallbackQuery, db, bot_id: int):
    bot = await store.get_owned(db, bot_id, cb.from_user.id)
    if not bot:
        await cb.answer(T.NOT_FOUND, show_alert=True)
    return bot


def _limit(cfg: BotConfig, max_bots: int, user_id: int) -> int | None:
    return None if user_id in cfg.admin_ids else max_bots


# ---- 목록 ----

@router.callback_query(F.data == "my")
async def my(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    bots = await store.bots_of(db, cb.from_user.id)
    await _show(cb, "🤖 <b>내 봇</b>\n\n설정할 봇을 선택해 주십시오." if bots else T.NO_BOTS, T.list_kb(bots))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^b:\d+$"))
async def panel(cb: CallbackQuery, db: aiosqlite.Connection, fleet: Fleet, state: FSMContext) -> None:
    await state.clear()
    bot_id = int(cb.data.split(":")[1])
    if not await _owned(cb, db, bot_id):
        return
    text, kb = await _panel(db, fleet, bot_id)
    await _show(cb, text, kb)
    await cb.answer()


# ---- 봇 만들기 ----

@router.callback_query(F.data == "add")
async def add(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext, cfg: BotConfig, max_bots: int) -> None:
    limit = _limit(cfg, max_bots, cb.from_user.id)
    if limit is not None and len(await store.bots_of(db, cb.from_user.id)) >= limit:
        await cb.message.answer(T.LIMIT.format(limit=limit))
        await cb.answer()
        return
    await state.set_state(Add.token)
    await cb.message.answer(T.ADD_GUIDE, link_preview_options=_NO_PREVIEW)
    await cb.answer()


@router.message(Add.token, F.text)
async def add_token(message: Message, db: aiosqlite.Connection, state: FSMContext, fleet: Fleet, box: TokenBox,
                    cfg: BotConfig, max_bots: int) -> None:
    token = message.text.strip()
    try:
        await message.delete()   # 토큰이 대화에 남지 않게 한다
    except TelegramAPIError:
        pass
    if not TOKEN_RE.match(token):
        await message.answer(T.BAD_TOKEN)
        return
    limit = _limit(cfg, max_bots, message.from_user.id)
    if limit is not None and len(await store.bots_of(db, message.from_user.id)) >= limit:
        await state.clear()
        await message.answer(T.LIMIT.format(limit=limit))
        return
    try:
        me = await fleet.probe(token)
    except (TelegramUnauthorizedError, TelegramAPIError):
        await message.answer(T.DEAD_TOKEN)
        return
    if not await store.add_bot(db, me.id, message.from_user.id, me.username or str(me.id), box.seal(token),
                               me.first_name or ""):
        await message.answer(T.DUP_TOKEN)
        return
    await state.clear()
    await fleet.start(me.id, token)
    await message.answer(T.created(me.username or str(me.id), me.can_read_all_group_messages is False),
                         reply_markup=T.list_kb(await store.bots_of(db, message.from_user.id)))


# ---- 설정 ----

@router.callback_query(F.data.regexp(r"^t:\d+$"))
async def toggle(cb: CallbackQuery, db: aiosqlite.Connection, fleet: Fleet) -> None:
    bot = await _owned(cb, db, int(cb.data.split(":")[1]))
    if not bot:
        return
    await store.set_auto(db, bot["bot_id"], not bot["auto_on"])
    text, kb = await _panel(db, fleet, bot["bot_id"])
    await _show(cb, text, kb)
    await cb.answer("자동 발표를 켰습니다." if not bot["auto_on"] else "자동 발표를 껐습니다.")


@router.callback_query(F.data.regexp(r"^wd:\d+$"))
async def weekday_menu(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    bot = await _owned(cb, db, int(cb.data.split(":")[1]))
    if not bot:
        return
    await _show(cb, "🗓 발표할 <b>요일</b>을 선택해 주십시오.", T.weekday_kb(bot))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^d:\d+:[0-6]$"))
async def weekday_set(cb: CallbackQuery, db: aiosqlite.Connection, fleet: Fleet) -> None:
    _, bot_id, day = cb.data.split(":")
    if not await _owned(cb, db, int(bot_id)):
        return
    await store.set_schedule(db, int(bot_id), weekday=int(day))
    text, kb = await _panel(db, fleet, int(bot_id))
    await _show(cb, text, kb)
    await cb.answer("발표 요일을 바꿨습니다.")


@router.callback_query(F.data.regexp(r"^e:\d+:(min|time|name|prizes|qualify|token)$"))
async def ask(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    _, bot_id, field = cb.data.split(":")
    bot = await _owned(cb, db, int(bot_id))
    if not bot:
        return
    cur = {"min": f"{bot['min_chars']}", "qualify": f"{bot['qualify']}",
           "time": schedule.clock(bot["hour"], bot["minute"])}.get(field, "")
    await state.set_state(Edit.value)
    await state.update_data(bot_id=int(bot_id), field=field)
    await cb.message.answer(T.ASK[field].format(cur=cur, username=bot["username"]), reply_markup=T.back_kb(int(bot_id)))
    await cb.answer()


@router.message(Edit.value, F.text)
async def apply(message: Message, db: aiosqlite.Connection, state: FSMContext, fleet: Fleet, box: TokenBox) -> None:
    data = await state.get_data()
    bot_id, field = data["bot_id"], data["field"]
    bot = await store.get_owned(db, bot_id, message.from_user.id)
    if not bot:
        await state.clear()
        await message.answer(T.NOT_FOUND)
        return
    text = message.text.strip()

    if field == "min":
        if not text.isdigit() or not 1 <= int(text) <= 100:
            await message.answer(T.BAD["min"])
            return
        await store.update_bot(db, bot_id, min_chars=int(text))
    elif field == "qualify":
        if not text.isdigit() or int(text) > 1_000_000:
            await message.answer(T.BAD["qualify"])
            return
        await store.update_bot(db, bot_id, qualify=int(text))
    elif field == "time":
        parsed = schedule.parse_time(text)
        if not parsed:
            await message.answer(T.BAD["time"])
            return
        await store.set_schedule(db, bot_id, hour=parsed[0], minute=parsed[1])
    elif field == "prizes":
        lines = [] if text in ("없음", "-", "0") else [x.strip() for x in text.splitlines() if x.strip()]
        if len(lines) > MAX_PRIZE_LINES or any(len(x) > MAX_PRIZE_LEN for x in lines):
            await message.answer(T.BAD["prizes"])
            return
        await store.update_bot(db, bot_id, prizes="\n".join(lines))
    elif field == "name":
        if not 1 <= len(text) <= 64:
            await message.answer(T.BAD["name"])
            return
        tg = fleet.bots.get(bot_id)
        if tg is None:
            await message.answer("⚠️ 멈춘 봇은 이름을 바꿀 수 없습니다. 먼저 토큰을 교체해 주십시오.")
            return
        try:
            await tg.set_my_name(name=text)
        except TelegramRetryAfter as e:
            await message.answer(f"⏳ 텔레그램이 이름 변경을 잠시 제한하고 있습니다. <b>{schedule.remaining(e.retry_after)}</b> 뒤에 다시 시도해 주십시오.")
            return
        except TelegramAPIError as e:
            await message.answer(f"❌ 이름을 바꾸지 못했습니다. ({e.message})")
            return
        await store.update_bot(db, bot_id, name=text)
    elif field == "token":
        if not (await _replace_token(message, db, fleet, box, bot, text)):
            return

    await state.clear()
    panel_text, kb = await _panel(db, fleet, bot_id)
    await message.answer("✅ 바꿨습니다.\n\n" + panel_text, reply_markup=kb)


async def _replace_token(message: Message, db, fleet: Fleet, box: TokenBox, bot, token: str) -> bool:
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    if not TOKEN_RE.match(token):
        await message.answer(T.BAD_TOKEN)
        return False
    try:
        me = await fleet.probe(token)
    except TelegramAPIError:
        await message.answer(T.DEAD_TOKEN)
        return False
    if me.id != bot["bot_id"]:
        await message.answer(T.WRONG_BOT.format(username=bot["username"]))
        return False
    await store.update_bot(db, bot["bot_id"], token_enc=box.seal(token), status="active")
    await fleet.start(bot["bot_id"], token)
    return True


# ---- 삭제 ----

@router.callback_query(F.data.regexp(r"^del:\d+$"))
async def delete_ask(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    bot = await _owned(cb, db, int(cb.data.split(":")[1]))
    if not bot:
        return
    await _show(cb, f"🗑 <b>@{bot['username']}</b> 을(를) 삭제할까요?\n\n"
                    "<blockquote>집계 기록과 발표 내역이 모두 지워지며 되돌릴 수 없습니다.\n"
                    "텔레그램의 봇 자체는 BotFather에서 따로 삭제하셔야 합니다.</blockquote>",
                T.confirm_kb(bot["bot_id"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^delok:\d+$"))
async def delete_do(cb: CallbackQuery, db: aiosqlite.Connection, fleet: Fleet) -> None:
    bot = await _owned(cb, db, int(cb.data.split(":")[1]))
    if not bot:
        return
    await fleet.stop(bot["bot_id"])
    await store.delete_bot(db, bot["bot_id"])
    bots = await store.bots_of(db, cb.from_user.id)
    await _show(cb, "🗑 삭제했습니다.\n\n" + ("🤖 <b>내 봇</b>" if bots else T.NO_BOTS), T.list_kb(bots))
    await cb.answer()


# ---- 상태 없이 온 텍스트 ----

@router.message(StateFilter(None), F.text)
async def fallback(message: Message) -> None:
    await message.answer(T.UNKNOWN, reply_markup=T.menu_kb())
