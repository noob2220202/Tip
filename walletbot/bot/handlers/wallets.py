"""지갑 등록·별명·삭제."""
from __future__ import annotations

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import store
from bot import texts as T
from bot.handlers.start import PRIVATE, korean, show
from core import users
from core.tron import is_tron_address

LABEL_MAX = 20

router = Router(name="wallet_wallets")
router.message.filter(PRIVATE)


class Add(StatesGroup):
    address = State()
    label = State()


class Rename(StatesGroup):
    label = State()


async def _list(user_id: int, db: aiosqlite.Connection) -> tuple[str, object]:
    ws = await store.wallets_of(db, user_id)
    return T.wallets_text(ws, store.MAX_WALLETS_PER_USER), T.wallets_kb(ws, len(ws) < store.MAX_WALLETS_PER_USER)


@router.message(Command("wallet"))
@router.message(korean("지갑"))
async def wallets_cmd(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    await show(message, *await _list(message.from_user.id, db))


@router.callback_query(F.data == "w")
async def wallets_cb(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    await show(cb, *await _list(cb.from_user.id, db))


@router.callback_query(F.data.regexp(r"^w:\d+$"))
async def wallet_cb(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    w = await store.get_wallet(db, int(cb.data.split(":")[1]), cb.from_user.id)
    if not w:
        await cb.answer(T.NOT_FOUND, show_alert=True)
        return
    await show(cb, T.wallet_text(w), T.wallet_kb(w["id"]))


# ---- 등록 ----

@router.callback_query(F.data == "wa")
async def add_start(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    if len(await store.wallets_of(db, cb.from_user.id)) >= store.MAX_WALLETS_PER_USER:
        await cb.answer(T.WALLET_LIMIT.format(n=store.MAX_WALLETS_PER_USER).replace("<b>", "").replace("</b>", ""),
                        show_alert=True)
        return
    await state.set_state(Add.address)
    await cb.message.answer(T.ADD_ASK)
    await cb.answer()


@router.message(Add.address, F.text)
async def add_address(message: Message, db: aiosqlite.Connection, state: FSMContext, max_wallets_total: int) -> None:
    address = message.text.strip()
    if not is_tron_address(address):
        await message.answer(T.BAD_ADDRESS)
        return
    uid = message.from_user.id
    ws = await store.wallets_of(db, uid)
    if any(w["address"] == address for w in ws):
        await message.answer(T.DUP_WALLET)
        return
    if len(ws) >= store.MAX_WALLETS_PER_USER:
        await state.clear()
        await message.answer(T.WALLET_LIMIT.format(n=store.MAX_WALLETS_PER_USER))
        return
    # 이미 누군가 지켜보는 주소는 조회를 더 늘리지 않으니 자리를 쓰지 않는다
    if not await store.address_is_watched(db, address) and await store.watched_total(db) >= max_wallets_total:
        await state.clear()
        await message.answer(T.SERVER_FULL)
        return
    await state.set_state(Add.label)
    await state.update_data(address=address)
    await message.answer(T.LABEL_ASK, reply_markup=T.skip_kb())


async def _finish_add(target: Message, db, state: FSMContext, user_id: int, label: str) -> None:
    address = (await state.get_data())["address"]
    await state.clear()
    await users.register(db, user_id, target.chat.username)     # 감시 대상은 활성 사용자로 한정하므로 반드시 등록돼 있게 한다
    wid = await store.add_wallet(db, user_id, address, label)
    if wid is None:
        await target.answer(T.DUP_WALLET)
        return
    w = await store.get_wallet(db, wid, user_id)
    await target.answer(T.added(w), reply_markup=T.menu_kb())


@router.message(Add.label, F.text)
async def add_label(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    label = message.text.strip()
    if len(label) > LABEL_MAX:
        await message.answer(f"별명은 {LABEL_MAX}자 이내로 보내 주십시오.")
        return
    await _finish_add(message, db, state, message.from_user.id, label)


@router.callback_query(Add.label, F.data == "wskip")
async def add_skip(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await _finish_add(cb.message, db, state, cb.from_user.id, "")
    await cb.answer()


# ---- 별명 변경 · 삭제 ----

@router.callback_query(F.data.regexp(r"^wr:\d+$"))
async def rename_start(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    w = await store.get_wallet(db, int(cb.data.split(":")[1]), cb.from_user.id)
    if not w:
        await cb.answer(T.NOT_FOUND, show_alert=True)
        return
    await state.set_state(Rename.label)
    await state.update_data(wid=w["id"])
    await cb.message.answer(T.RENAME_ASK)
    await cb.answer()


@router.message(Rename.label, F.text)
async def rename_do(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    label = "" if message.text.strip() == "-" else message.text.strip()
    if len(label) > LABEL_MAX:
        await message.answer(f"별명은 {LABEL_MAX}자 이내로 보내 주십시오.")
        return
    wid = (await state.get_data())["wid"]
    await state.clear()
    w = await store.get_wallet(db, wid, message.from_user.id)
    if not w:
        await message.answer(T.NOT_FOUND)
        return
    await store.rename_wallet(db, wid, label)
    await show(message, T.wallet_text(await store.get_wallet(db, wid, message.from_user.id)), T.wallet_kb(wid))


@router.callback_query(F.data.regexp(r"^wd:\d+$"))
async def delete_ask(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    w = await store.get_wallet(db, int(cb.data.split(":")[1]), cb.from_user.id)
    if not w:
        await cb.answer(T.NOT_FOUND, show_alert=True)
        return
    await show(cb, f"🗑 <b>{T.name_of(w)}</b> 지갑을 삭제할까요?\n\n<blockquote>이 지갑의 입금 알림이 중단됩니다.</blockquote>",
               T.confirm_kb(w["id"]))


@router.callback_query(F.data.regexp(r"^wdok:\d+$"))
async def delete_do(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    w = await store.get_wallet(db, int(cb.data.split(":")[1]), cb.from_user.id)
    if not w:
        await cb.answer(T.NOT_FOUND, show_alert=True)
        return
    await store.delete_wallet(db, w["id"])
    text, kb = await _list(cb.from_user.id, db)
    await show(cb, "🗑 삭제했습니다.\n\n" + text, kb)
