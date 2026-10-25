"""고객 봇 핸들러. 그룹 채팅을 세고, 순위·내 기록 명령과 방장 명령을 처리한다."""
from __future__ import annotations

import logging
import time

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, or_f
from aiogram.types import CallbackQuery, ChatMemberUpdated, LinkPreviewOptions, Message

from bot import child_texts as T
from bot import store

log = logging.getLogger(__name__)

GROUP = F.chat.type.in_({"group", "supergroup"})
PRIVATE = F.chat.type == "private"
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def korean(words: str):
    """텔레그램은 /뒤에 영문·숫자만 명령어로 쳐주므로 한글 명령어는 일반 텍스트를 정규식으로 잡는다."""
    return F.text.regexp(rf"^\s*[/／]\s*(?:{words})(?:@\S+)?\s*$")


def display_name(user) -> str:
    """이름이 비었거나 눈에 안 보이는 글자뿐이면 아이디로 대신한다."""
    for cand in (user.full_name, user.username):
        name = (cand or "").strip()
        if any(ch.isprintable() and not ch.isspace() and ch not in "️︎​‍" for ch in name):
            return name[:64]
    return f"익명{user.id % 10000}"


async def _is_admin(bot: Bot, chat_id: int, user_id: int, owner_id: int) -> bool:
    if user_id == owner_id:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except Exception:  # noqa: BLE001 - 확인 못 하면 권한 없음으로 본다
        return False
    return m.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


async def _cfg(db, bot: Bot):
    return await store.get_bot(db, bot.id)


def build_router(manager_username: str = "") -> Router:
    r = Router(name="rank_child")

    @r.my_chat_member()
    async def membership(ev: ChatMemberUpdated, bot: Bot, db: aiosqlite.Connection) -> None:
        if ev.chat.type not in ("group", "supergroup"):
            return
        gone = ev.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
        await store.upsert_chat(db, bot.id, ev.chat.id, ev.chat.title or str(ev.chat.id), active=not gone)

    async def touch(db, bot: Bot, message: Message) -> None:
        await store.upsert_chat(db, bot.id, message.chat.id, message.chat.title or str(message.chat.id))

    # ---- 집계 (명령어 핸들러보다 뒤에 등록한다) ----

    async def count(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        user = message.from_user
        if not user or user.is_bot:
            return
        body = (message.text or message.caption or "").strip()
        if body.startswith(("/", "／")):
            return
        cfg = await _cfg(db, bot)
        if not cfg or len(body) < cfg["min_chars"]:
            return
        await touch(db, bot, message)
        await store.record(db, bot.id, message.chat.id, cfg["round"], user.id, display_name(user))

    # ---- 명령어 ----

    async def rank(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        await touch(db, bot, message)
        await message.answer(await _rank_payload(db, cfg, message.chat.id, message.from_user.id),
                             reply_markup=T.kb_rank(), link_preview_options=_NO_PREVIEW)

    async def me(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        await touch(db, bot, message)
        n, pos, people = await store.my_stats(db, bot.id, message.chat.id, cfg["round"], message.from_user.id)
        await message.answer(T.my_text(cfg, display_name(message.from_user), n, pos, people), reply_markup=T.kb_back())

    async def rules(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        await message.answer(T.rule_text(await _cfg(db, bot)), reply_markup=T.kb_back())

    async def help_(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        admin = (message.chat.type != "private"
                 and await _is_admin(bot, message.chat.id, message.from_user.id, cfg["owner_id"]))
        await message.answer(T.help_text(admin))

    async def start(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        await message.answer(T.start_text(await _cfg(db, bot), manager_username))

    async def need_group(message: Message) -> None:
        await message.answer(T.group_only())

    async def preview(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        if not await _is_admin(bot, message.chat.id, message.from_user.id, cfg["owner_id"]):
            return   # 방장이 아니면 없는 명령어처럼 조용히 둔다
        from bot.settler import announcement
        text = await announcement(db, cfg, message.chat.id, cfg["round"], preview=True)
        await message.answer(text, link_preview_options=_NO_PREVIEW)

    async def status(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        if not await _is_admin(bot, message.chat.id, message.from_user.id, cfg["owner_id"]):
            return
        rows = await store.ranking(db, bot.id, message.chat.id, cfg["round"])
        qualified = sum(1 for x in rows if x["messages"] >= cfg["qualify"])
        await message.answer(T.status_text(cfg, message.chat.title or "", len(rows), sum(x["messages"] for x in rows),
                                           qualified, cfg["next_settle_at"] - time.time()))

    cmds = [
        (rank, or_f(Command("rank"), korean("순위|랭킹"))),
        (me, or_f(Command("me"), korean("내기록|내 기록|기록"))),
        (rules, or_f(Command("rules"), korean("규칙"))),
        (help_, or_f(Command("help"), korean("명령어|도움말"))),
        (preview, korean("집계")),
        (status, korean("상태")),
    ]
    for fn, flt in cmds:
        if fn in (rules, help_):
            r.message.register(fn, flt)
        else:
            r.message.register(fn, GROUP, flt)
    # 그룹 전용 명령이 개인 대화에서 불리면 안내만 한다
    r.message.register(need_group, PRIVATE, or_f(Command("rank"), korean("순위|랭킹"), Command("me"),
                                                korean("내기록|내 기록|기록")))
    r.message.register(start, PRIVATE, Command("start"))
    r.message.register(count, GROUP, or_f(F.text, F.caption))

    # ---- 버튼 ----

    @r.callback_query(F.message.chat.type.in_({"group", "supergroup"}))
    async def button(cb: CallbackQuery, bot: Bot, db: aiosqlite.Connection) -> None:
        cfg = await _cfg(db, bot)
        chat_id, user = cb.message.chat.id, cb.from_user
        if cb.data == "my":
            n, pos, people = await store.my_stats(db, bot.id, chat_id, cfg["round"], user.id)
            await cb.answer(T.my_alert(display_name(user), n, pos, people), show_alert=True)
            return
        if cb.data == "rank":
            text, kb = await _rank_payload(db, cfg, chat_id, user.id), T.kb_rank()
        elif cb.data == "rule":
            text, kb = T.rule_text(cfg), T.kb_back()
        elif cb.data == "help":
            text, kb = T.help_text(await _is_admin(bot, chat_id, user.id, cfg["owner_id"])), T.kb_back()
        else:
            await cb.answer()
            return
        await cb.answer()
        try:
            await cb.message.edit_text(text, reply_markup=kb, link_preview_options=_NO_PREVIEW)
        except Exception as e:  # noqa: BLE001 - 내용이 같아 수정이 거절되는 경우가 대부분이다
            if "not modified" not in str(e).lower():
                log.warning("버튼 응답 실패: %s", e)

    return r


async def _rank_payload(db, cfg, chat_id: int, user_id: int) -> str:
    rows = await store.ranking(db, cfg["bot_id"], chat_id, cfg["round"], limit=10)
    n, pos, people = await store.my_stats(db, cfg["bot_id"], chat_id, cfg["round"], user_id)
    return T.rank_text(cfg, rows, T.me_line(n, pos, people))


