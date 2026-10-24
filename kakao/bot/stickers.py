"""텔레그램 스티커 세트 만들기."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputSticker

log = logging.getLogger(__name__)

TITLE_SEPARATOR = "ㅣ"
MAX_TITLE = 64
MAX_NAME = 64
MAX_PER_SET = 120
FIRST_BATCH = 50


def make_title(name: str, bot_username: str) -> str:
    """세트 제목은 반드시 '@봇이름ㅣ이름'. 64자를 넘으면 이름을 자른다."""
    clean = re.sub(r"\s+", " ", name).strip() or "스티커"
    return (f"@{bot_username}{TITLE_SEPARATOR}{clean}")[:MAX_TITLE]


def make_set_name(user_id: int, key: str, bot_username: str) -> str:
    """URL용 이름: 영문·숫자·밑줄, 영문 시작, 연속 밑줄 금지, `_by_<봇>`으로 끝, 64자 이하."""
    suffix = f"_by_{bot_username}"
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    base = f"tip_{user_id}_{slug}".strip("_")
    base = re.sub(r"_+", "_", base)
    if len(base) + len(suffix) > MAX_NAME:
        h = hashlib.sha1(key.encode()).hexdigest()[:6]
        base = f"{base[: MAX_NAME - len(suffix) - 7]}_{h}"
        base = re.sub(r"_+", "_", base)
    return base + suffix


async def _retry(call: Callable[[], Awaitable], tries: int = 5):
    """429(retry_after)만큼 쉬고 다시 시도한다."""
    for attempt in range(tries):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == tries - 1:
                raise
            log.warning("429: %s초 대기", e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)


def _sticker(data: bytes, idx: int, fmt: str, emoji: str) -> InputSticker:
    ext = "webm" if fmt == "video" else "png" if data[:4] == b"\x89PNG" else "webp"
    return InputSticker(
        sticker=BufferedInputFile(data, filename=f"{idx}.{ext}"), format=fmt, emoji_list=[emoji]
    )


async def set_exists(bot: Bot, name: str) -> bool:
    try:
        await bot.get_sticker_set(name)
        return True
    except TelegramBadRequest:
        return False


async def create_set(
    bot: Bot, user_id: int, name: str, title: str, files: list[bytes], fmt: str, emoji: str
) -> int:
    """세트를 만들고 추가된 스티커 수를 돌려준다. 중간에 실패하면 예외가 나가고, 이미 만든 세트는 남는다."""
    files = files[:MAX_PER_SET]
    first = [_sticker(d, i, fmt, emoji) for i, d in enumerate(files[:FIRST_BATCH])]
    await _retry(lambda: bot.create_new_sticker_set(user_id=user_id, name=name, title=title, stickers=first))
    added = len(first)
    for i, d in enumerate(files[FIRST_BATCH:], start=FIRST_BATCH):
        st = _sticker(d, i, fmt, emoji)
        await _retry(lambda: bot.add_sticker_to_set(user_id=user_id, name=name, sticker=st))
        added += 1
    return added


async def delete_set(bot: Bot, name: str) -> None:
    try:
        await bot.delete_sticker_set(name)
    except TelegramAPIError as e:
        log.warning("스티커 세트 삭제 실패 %s: %s", name, e)
