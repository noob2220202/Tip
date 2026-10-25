"""관리자: 이벤트 만들기·실행·목록. 이 파일의 모든 핸들러는 ADMIN_IDS에 있는 계정만 쓸 수 있다."""
from __future__ import annotations

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from bot import store
from bot import texts as T
from bot.engine import Engine
from core.config import BotConfig

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, cfg: BotConfig) -> bool:
        return bool(event.from_user and event.from_user.id in cfg.admin_ids)


router = Router(name="giveaway_admin")
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(IsAdmin())


class Create(StatesGroup):
    title = State()
    count = State()
    items = State()
    note = State()


async def show(target: Message | CallbackQuery, text: str, kb=None) -> None:
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
        except TelegramAPIError:
            await target.message.answer(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, link_preview_options=NO_PREVIEW)


# ---- 메뉴 · 목록 · 상세 ----

@router.message(Command("giveaway"))
async def menu_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🎁 <b>이벤트 관리</b>", reply_markup=T.admin_menu_kb())


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(T.CANCELLED, reply_markup=T.admin_menu_kb())


@router.message(StateFilter(Create), F.text.startswith("/"))
async def command_while_creating(message: Message) -> None:
    await message.answer("이벤트를 만드는 중입니다. 그만두시려면 /cancel 을 보내 주십시오.")


@router.callback_query(F.data == "ga:menu")
async def menu_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "🎁 <b>이벤트 관리</b>", T.admin_menu_kb())


@router.message(Command("giveaways"))
async def list_cmd(message: Message, db: aiosqlite.Connection) -> None:
    events = await store.list_events(db)
    await show(message, T.events_text(events), T.list_kb(events))


@router.callback_query(F.data == "ga:list")
async def list_cb(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    events = await store.list_events(db)
    await show(cb, T.events_text(events), T.list_kb(events))


async def _detail(db: aiosqlite.Connection, cfg: BotConfig, event_id: int) -> tuple[str, object] | None:
    ev = await store.get_event(db, event_id)
    if ev is None:
        return None
    items = await store.item_counts(db, event_id)
    tries = await store.try_counts(db, event_id)
    pool = await store.pool_size(db, event_id, cfg.admin_ids) if ev["status"] in ("draft", "paused") else None
    text = T.event_text(ev, items, pool=pool, tries=tries if ev["started_at"] else None)
    if ev["status"] in ("done", "paused", "running"):
        text += T.winners_text(await store.winners_of(db, event_id))
    return text, T.event_kb(ev)


@router.callback_query(F.data.regexp(r"^ge:\d+$"))
async def detail_cb(cb: CallbackQuery, db: aiosqlite.Connection, cfg: BotConfig, state: FSMContext) -> None:
    await state.clear()
    out = await _detail(db, cfg, int(cb.data.split(":")[1]))
    if out is None:
        await cb.answer(T.NOT_FOUND, show_alert=True)
        return
    await show(cb, *out)


# ---- 만들기 ----

@router.callback_query(F.data == "ga:new")
async def new_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "🎁 <b>새 이벤트</b>\n\n방식을 선택해 주십시오.", T.kind_kb())


@router.callback_query(F.data.regexp(r"^gk:(draw|all)$"))
async def kind_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Create.title)
    await state.update_data(kind=cb.data.split(":")[1], items=[])
    await cb.message.answer(T.ASK_TITLE)
    await cb.answer()


@router.message(Create.title, F.text)
async def title_msg(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not 1 <= len(title) <= T.TITLE_MAX:
        await message.answer(T.BAD_TITLE)
        return
    await state.update_data(title=title)
    kind = (await state.get_data())["kind"]
    if kind == "draw":
        await state.set_state(Create.count)
        await message.answer(T.ASK_COUNT)
    else:
        await state.update_data(winners=0)
        await state.set_state(Create.items)
        await message.answer(T.ask_items("all", 0), reply_markup=T.items_kb())


@router.message(Create.count, F.text)
async def count_msg(message: Message, state: FSMContext) -> None:
    n = message.text.strip()
    if not n.isdigit() or not 1 <= int(n) <= 500:
        await message.answer(T.BAD_COUNT)
        return
    await state.update_data(winners=int(n))
    await state.set_state(Create.items)
    await message.answer(T.ask_items("draw", int(n)), reply_markup=T.items_kb())


@router.message(Create.items, F.photo | F.text)
async def items_msg(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    items: list = list(data["items"])
    if data["kind"] == "all":
        items = []                                              # 전원 동일은 하나만 둔다. 새로 보내면 바꾼다
    if message.photo:
        items.append(["photo", message.photo[-1].file_id])
    else:
        text = message.text.strip()
        if data["kind"] == "all":
            items.append(["text", text])
        else:
            items += [["text", line.strip()] for line in text.splitlines() if line.strip()]
    if len(items) > store.MAX_ITEMS:
        await message.answer(f"기프트콘은 최대 {store.MAX_ITEMS}개까지 등록할 수 있습니다.")
        return
    await state.update_data(items=items)
    await message.answer(T.items_progress(data["kind"], len(items), data["winners"]), reply_markup=T.items_kb())


@router.callback_query(Create.items, F.data == "gi:done")
async def items_done(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    n = len(data["items"])
    if n == 0 or (data["kind"] == "draw" and n < data["winners"]):
        await cb.message.answer(T.items_short(n, data["winners"]) if n else "기프트콘을 먼저 등록해 주십시오.")
        await cb.answer()
        return
    await state.set_state(Create.note)
    await cb.message.answer(T.ASK_NOTE, reply_markup=T.note_kb())
    await cb.answer()


@router.callback_query(F.data == "gi:cancel")
async def items_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, T.CANCELLED, T.admin_menu_kb())


async def _create(target: Message, db: aiosqlite.Connection, cfg: BotConfig, state: FSMContext, note: str) -> None:
    data = await state.get_data()
    await state.clear()
    eid = await store.create_event(db, data["kind"], data["title"], data["winners"], note, target.chat.id)
    await store.add_items(db, eid, [(k, c) for k, c in data["items"]])
    text, kb = await _detail(db, cfg, eid)
    await target.answer("✅ 이벤트를 만들었습니다. 내용을 확인하고 <b>지금 실행</b>을 눌러 주십시오.\n\n" + text, reply_markup=kb)


@router.message(Create.note, F.text)
async def note_msg(message: Message, db: aiosqlite.Connection, cfg: BotConfig, state: FSMContext) -> None:
    note = message.text.strip()
    if len(note) > T.NOTE_MAX:
        await message.answer(T.BAD_NOTE)
        return
    await _create(message, db, cfg, state, note)


@router.callback_query(Create.note, F.data == "gn:skip")
async def note_skip(cb: CallbackQuery, db: aiosqlite.Connection, cfg: BotConfig, state: FSMContext) -> None:
    await _create(cb.message, db, cfg, state, "")
    await cb.answer()


# ---- 실행 · 취소 · 미리보기 ----

async def _event_or_alert(cb: CallbackQuery, db: aiosqlite.Connection):
    ev = await store.get_event(db, int(cb.data.split(":")[2]))
    if ev is None:
        await cb.answer(T.NOT_FOUND, show_alert=True)
    return ev


@router.callback_query(F.data.regexp(r"^gx:run:\d+$"))
async def run_cb(cb: CallbackQuery, db: aiosqlite.Connection, cfg: BotConfig, engine: Engine) -> None:
    ev = await _event_or_alert(cb, db)
    if not ev:
        return
    # draft/paused에서 running으로 바뀌는 건 한 번뿐이라, 버튼을 두 번 눌러도 이벤트는 한 번만 실행된다
    if not await store.transition(db, ev["id"], ("draft", "paused"), "running"):
        await cb.answer("이미 실행 중이거나 끝난 이벤트입니다.", show_alert=True)
        return
    engine.start(ev["id"])
    await show(cb, "🚀 <b>실행을 시작했습니다.</b> 진행 상황을 이 대화로 알려 드립니다.\n\n" + (await _detail(db, cfg, ev["id"]))[0],
               T.event_kb(await store.get_event(db, ev["id"])))


@router.callback_query(F.data.regexp(r"^gx:cancel:\d+$"))
async def cancel_cb(cb: CallbackQuery, db: aiosqlite.Connection, cfg: BotConfig) -> None:
    ev = await _event_or_alert(cb, db)
    if not ev:
        return
    if not await store.transition(db, ev["id"], ("draft", "paused"), "cancelled"):
        await cb.answer("지금은 취소할 수 없습니다.", show_alert=True)
        return
    await show(cb, *await _detail(db, cfg, ev["id"]))


@router.callback_query(F.data.regexp(r"^gx:wipe:\d+$"))
async def wipe_cb(cb: CallbackQuery, db: aiosqlite.Connection, cfg: BotConfig) -> None:
    ev = await _event_or_alert(cb, db)
    if not ev:
        return
    if ev["status"] not in ("done", "cancelled"):
        await cb.answer("끝난 이벤트만 지울 수 있습니다.", show_alert=True)
        return
    n = await store.wipe_codes(db, ev["id"])
    await cb.answer(f"{n}개를 지웠습니다.", show_alert=True)


@router.callback_query(F.data.regexp(r"^gx:view:\d+$"))
async def view_cb(cb: CallbackQuery, db: aiosqlite.Connection, bot: Bot) -> None:
    """실제 코드를 소진하지 않고, 받는 사람에게 보이는 모습을 나에게만 보여준다."""
    ev = await _event_or_alert(cb, db)
    if not ev:
        return
    item = await store.first_item(db, ev["id"])
    if item is None:
        await cb.answer("등록된 기프트콘이 없습니다.", show_alert=True)
        return
    text = "👀 <b>미리보기</b> (실제로 발송되지 않았습니다)\n\n" + T.delivery(ev, item["kind"], item["content"])
    if item["kind"] == "photo":
        await bot.send_photo(cb.from_user.id, photo=item["content"], caption=text)
    else:
        await bot.send_message(cb.from_user.id, text)
    await cb.answer()


# ---- 입력 대기 중이 아닌 상태에서 온 사진 ----

@router.message(StateFilter(None), F.photo)
async def stray_photo(message: Message) -> None:
    await message.answer(T.UNKNOWN, reply_markup=T.admin_menu_kb())
