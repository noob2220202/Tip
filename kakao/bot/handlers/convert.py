"""이모티콘샵 링크 → 텔레그램 스티커 세트."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import aiosqlite
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from bot import media, stickers
from bot.handlers.common import NO_PREVIEW, shop_keyboard
from bot.kakao import KakaoClient, KakaoError, Pack, Session, parse_link
from core.config import BotConfig
from core.style import esc
from core.texts import Texts

log = logging.getLogger(__name__)
router = Router(name="kakao_sticker")

ALERT_INTERVAL = 3600  # 세션 만료 알림은 관리자에게 시간당 한 번만


class Progress:
    """진행 메시지 하나를 갱신한다. 편집 한도에 걸리지 않게 간격을 둔다."""

    def __init__(self, msg: Message):
        self.msg = msg
        self.last = 0.0

    async def set(self, text: str, force: bool = False) -> None:
        if not force and time.monotonic() - self.last < 1.5:
            return
        self.last = time.monotonic()
        try:
            await self.msg.edit_text(text)
        except TelegramAPIError:
            pass  # 같은 내용 재편집·한도 초과는 무시해도 된다


async def _alert_admins(message: Message, cfg: BotConfig, texts: Texts, state: dict) -> None:
    if time.time() - state["last"] < ALERT_INTERVAL:
        return
    state["last"] = time.time()
    for admin_id in cfg.admin_ids:
        try:
            await message.bot.send_message(admin_id, texts.get("admin_session_alert"))
        except TelegramAPIError:
            pass


def _url(name: str) -> str:
    return f"https://t.me/addstickers/{name}"


async def _find_existing(bot, db, user_id: int, *, key: str | None = None, fingerprint: str | None = None):
    """이미 만들어 둔 세트를 찾는다. 다른 사용자가 만든 것도 재사용하고, 텔레그램에 실제로 남아 있는 것만 인정한다."""
    column, value = ("key", key) if key is not None else ("fingerprint", fingerprint)
    async with db.execute(
        f"SELECT * FROM sticker_sets WHERE status='complete' AND {column}=? "
        "ORDER BY (user_id=?) DESC, created_at DESC LIMIT 5",
        (value, user_id),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        if await stickers.set_exists(bot, row["set_name"]):
            return row
    return None


def _reuse_text(texts: Texts, row) -> str:
    return texts.get("reuse", title=esc(row["title"]), count=row["total"], url=_url(row["set_name"]))


async def _convert_all(
    kakao: KakaoClient, urls: list[str], fmt: str, sem: asyncio.Semaphore, progress: Progress, texts: Texts
) -> list[bytes | None]:
    done = 0
    fn = media.to_video_sticker if fmt == "video" else media.to_static_sticker

    async def one(url: str) -> bytes | None:
        nonlocal done
        try:
            raw = await kakao.download(url)
            async with sem:
                return await asyncio.to_thread(fn, raw)
        except (KakaoError, media.ConvertError) as e:
            log.warning("항목 변환 실패 %s: %s", url, e)
            return None
        finally:
            done += 1
            await progress.set(texts.get("progress", done=done, total=len(urls)))

    return await asyncio.gather(*(one(u) for u in urls))


@router.message(F.text.func(lambda t: parse_link(t) is not None))
async def on_link(
    message: Message,
    db: aiosqlite.Connection,
    cfg: BotConfig,
    texts: Texts,
    kakao: KakaoClient,
    kakao_session: Session,
    convert_sem: asyncio.Semaphore,
    inflight: set[int],
    emoji: str,
    alert_state: dict,
) -> None:
    user_id = message.from_user.id
    if user_id in inflight:
        await message.answer(texts.get("busy"))
        return
    inflight.add(user_id)
    try:
        await _handle(message, db, cfg, texts, kakao, convert_sem, emoji, alert_state)
    finally:
        inflight.discard(user_id)


async def _handle(message, db, cfg, texts, kakao, sem, emoji, alert_state) -> None:
    user_id = message.from_user.id
    key = parse_link(message.text)
    bot = message.bot

    # 이미 만든 이모티콘이면 카카오에 접속하지 않고 바로 링크를 준다
    row = await _find_existing(bot, db, user_id, key=key)
    if row:
        await message.answer(_reuse_text(texts, row))
        return

    progress = Progress(await message.answer(texts.get("fetching")))

    try:
        pack: Pack = await kakao.fetch_pack(key)
    except KakaoError as e:
        await progress.set(str(e), force=True)
        return

    # 움직이는 건 로그인 세션이 있어야 받을 수 있다. 세션이 없으면 정지 이미지로 대신 만들지 않는다.
    if not pack.animated and not pack.logged_in:
        await progress.set(texts.get("no_session"), force=True)
        await _alert_admins(message, cfg, texts, alert_state)
        return

    # 한 세트에는 같은 형식만 넣을 수 있다. 움직이는 게 있으면 그쪽을 쓴다.
    if pack.animated:
        fmt, urls = "video", [u for u, k in pack.items if k == "animated"]
        dropped_still = len(pack.stills)
    else:
        fmt, urls = "static", [u for u, k in pack.items if k == "still"]
        dropped_still = 0

    me = await bot.me()
    set_name = stickers.make_set_name(user_id, key, me.username)
    title = stickers.make_title(pack.title)

    # 링크 형태가 달라도(e.kakao.com/t/… 와 emoticon.kakao.com/items/…) 같은 이모티콘이면 이미지 목록이 같다
    fingerprint = hashlib.sha1((fmt + "\n" + "\n".join(urls)).encode()).hexdigest()
    row = await _find_existing(bot, db, user_id, fingerprint=fingerprint)
    if row:
        await progress.set(_reuse_text(texts, row), force=True)
        return
    # 중간에 끊겼거나 사용자가 지운 흔적은 정리하고 처음부터 다시 만든다
    if await stickers.set_exists(bot, set_name):
        await stickers.delete_set(bot, set_name)

    truncated = len(urls) > stickers.MAX_PER_SET
    urls = urls[: stickers.MAX_PER_SET]
    results = await _convert_all(kakao, urls, fmt, sem, progress, texts)
    files = [r for r in results if r]
    skipped = len(results) - len(files)
    if not files:
        await progress.set(texts.get("convert_failed"), force=True)
        return

    await progress.set(texts.get("uploading"), force=True)
    await db.execute(
        """INSERT OR REPLACE INTO sticker_sets (user_id, key, set_name, title, fmt, fingerprint, total, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', ?)""",
        (user_id, key, set_name, title, fmt, fingerprint, len(files), int(time.time())),
    )
    await db.commit()
    try:
        await stickers.create_set(bot, user_id, set_name, title, files, fmt, emoji)
    except TelegramAPIError as e:
        log.error("스티커 세트 생성 실패 user=%s key=%s: %s", user_id, key, e)
        await progress.set(texts.get("upload_failed"), force=True)
        return
    await db.execute("UPDATE sticker_sets SET status='complete' WHERE user_id=? AND key=?", (user_id, key))
    await db.commit()

    text = texts.get("done", title=esc(title), count=len(files), url=_url(set_name))
    if skipped:
        text += texts.get("partial", skipped=skipped)
    if dropped_still:
        text += texts.get("mixed", still=dropped_still)
    if truncated:
        text += texts.get("truncated", max=stickers.MAX_PER_SET)
    await progress.set(text, force=True)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("need_link"), link_preview_options=NO_PREVIEW, reply_markup=shop_keyboard(texts))
