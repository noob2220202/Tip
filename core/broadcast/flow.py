"""/broadcast FSM: 작성 → 버튼 → 미리보기 → 확인 → 백그라운드 발송."""
from __future__ import annotations

import re

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.admin import AdminFilter
from core.broadcast import models
from core.broadcast.sender import CampaignSender, send_campaign_message
from core.texts import Texts

URL_RE = re.compile(r"^(https?://|tg://)\S+$", re.I)


class BC(StatesGroup):
    content = State()
    buttons = State()


def parse_buttons(raw: str) -> list[dict] | None:
    """'글자 | URL' 한 줄에 하나, 최대 2개. 형식이 틀리면 None."""
    out: list[dict] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        text, sep, url = line.partition("|")
        text, url = text.strip(), url.strip()
        if not sep or not text or not URL_RE.match(url):
            return None
        out.append({"text": text[:64], "url": url})
    if not 1 <= len(out) <= 2:
        return None
    return out


def _confirm_kb(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="발송", callback_data=f"bc:send:{campaign_id}"),
            InlineKeyboardButton(text="취소", callback_data=f"bc:cancel:{campaign_id}"),
        ]]
    )


def build_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="core_broadcast")
    flt = AdminFilter(admin_ids)
    router.message.filter(flt)
    router.callback_query.filter(flt)

    @router.message(Command("broadcast"))
    async def start(message: Message, state: FSMContext, texts: Texts) -> None:
        await state.clear()
        await state.set_state(BC.content)
        await message.answer(texts.get("bc_ask_content"))

    @router.message(Command("cancel"), StateFilter(BC))
    async def cancel(message: Message, state: FSMContext, texts: Texts) -> None:
        await state.clear()
        await message.answer(texts.get("bc_cancelled"))

    @router.message(BC.content)
    async def got_content(message: Message, state: FSMContext, texts: Texts) -> None:
        if message.photo:
            kind, file_id = "photo", message.photo[-1].file_id
        elif message.video:
            kind, file_id = "video", message.video.file_id
        elif message.text and not message.text.startswith("/"):
            kind, file_id = "text", None
        else:
            await message.answer(texts.get("bc_bad_content"))
            return
        plain = (message.text or message.caption or "").strip().replace("\n", " ")
        await state.update_data(
            kind=kind, file_id=file_id, html=message.html_text if (message.text or message.caption) else None,
            title=plain[:30] or kind,
        )
        await state.set_state(BC.buttons)
        await message.answer(texts.get("bc_ask_buttons"))

    async def _preview(message: Message, state: FSMContext, db: aiosqlite.Connection, texts: Texts, buttons: list[dict]) -> None:
        d = await state.get_data()
        cid = await models.create_campaign(
            db, title=d["title"], content_type=d["kind"], text=d["html"], file_id=d["file_id"],
            buttons=buttons, created_by=message.from_user.id,
        )
        await state.clear()
        c = await models.get_campaign(db, cid)
        await send_campaign_message(message.bot, message.chat.id, c)
        target = await models.count_active_users(db)
        await message.answer(texts.get("bc_preview_head", id=cid, target=target), reply_markup=_confirm_kb(cid))

    @router.message(Command("skip"), StateFilter(BC.buttons))
    async def skip(message: Message, state: FSMContext, db: aiosqlite.Connection, texts: Texts) -> None:
        await _preview(message, state, db, texts, [])

    @router.message(BC.buttons, F.text)
    async def got_buttons(message: Message, state: FSMContext, db: aiosqlite.Connection, texts: Texts) -> None:
        buttons = parse_buttons(message.text)
        if buttons is None:
            await message.answer(texts.get("bc_bad_buttons"))
            return
        await _preview(message, state, db, texts, buttons)

    @router.callback_query(F.data.startswith("bc:"))
    async def confirm(cb: CallbackQuery, db: aiosqlite.Connection, texts: Texts, sender: CampaignSender) -> None:
        _, action, raw_id = cb.data.split(":")
        cid = int(raw_id)
        c = await models.get_campaign(db, cid)
        if c is None:
            await cb.answer(texts.get("bc_not_found"), show_alert=True)
            return
        if action == "cancel":
            if c["status"] == "draft":
                await models.set_status(db, cid, "cancelled")
            await cb.message.edit_text(texts.get("bc_cancelled"))
        elif action == "send":
            # draft일 때만 시작된다. 버튼을 두 번 눌러도 중복 발송되지 않는다.
            n = await models.start_campaign(db, cid)
            if n >= 0:
                await cb.message.edit_text(texts.get("bc_started"))
                sender.start(cid)
        await cb.answer()

    @router.message(Command("bcancel"))
    async def stop_campaign(message: Message, db: aiosqlite.Connection, texts: Texts) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("사용법: /bcancel 캠페인ID")
            return
        c = await models.get_campaign(db, int(parts[1]))
        if c is None:
            await message.answer(texts.get("bc_not_found"))
            return
        if c["status"] in ("sending", "paused", "draft"):
            await models.set_status(db, c["id"], "cancelled")
        await message.answer(f"캠페인 #{c['id']} 발송을 멈췄어요.")

    return router
