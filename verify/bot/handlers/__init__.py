"""/start, /help와 관리자용 /channels, /check."""
from aiogram import Router
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import Message

from core import users
from core.config import BotConfig
from core.style import esc
from core.texts import Texts
from core.verifier import channel_problems, check_user

router = Router(name="verify")


@router.message(CommandStart())
async def start(message: Message, db, texts: Texts) -> None:
    u = message.from_user
    await users.register(db, u.id, u.username)
    await message.answer(texts.get("start", name=esc(u.first_name)))


@router.message(Command("help"))
async def help_(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("help"))


class IsAdmin(BaseFilter):
    """관리자만 통과한다. 관리자가 아니면 핸들러가 실행되지 않아 아무 응답도 하지 않는다."""

    async def __call__(self, message: Message, cfg: BotConfig) -> bool:
        return bool(message.from_user and message.from_user.id in cfg.admin_ids)


@router.message(Command("channels"), IsAdmin())
async def channels(message: Message, cfg: BotConfig, texts: Texts) -> None:
    chans = cfg.common.channels
    if not chans:
        await message.answer(texts.get("channels_none"))
        return
    problems = await channel_problems(message.bot, chans)
    if problems:
        await message.answer(texts.get("channels_problem", problems="\n".join(f"• {esc(p)}" for p in problems)))
    else:
        await message.answer(texts.get("channels_ok", channels="\n".join(f"• {esc(c.chat_id)}" for c in chans)))


@router.message(Command("check"), IsAdmin())
async def check(message: Message, cfg: BotConfig, texts: Texts) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer(texts.get("check_usage"))
        return
    uid = int(parts[1])
    res = await check_user(message.bot, cfg.common.channels, uid)
    lines = []
    for c in cfg.common.channels:
        cid = str(c.chat_id)
        mark = "❓ 확인 실패" if cid in res["errors"] else "❌ 미가입" if cid in res["missing"] else "✅ 가입"
        lines.append(f"{esc(cid)} — {mark}")
    await message.answer(texts.get("check_result", user_id=uid, lines="\n".join(lines) or "채널 없음"))
