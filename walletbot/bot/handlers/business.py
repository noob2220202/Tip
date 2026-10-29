"""텔레그램 비즈니스: 주인이 자기 DM 대화에 봇을 연결하면 그 대화창에서 /조회 /시세 /환율에 답한다.

- 연결한 주인이 직접 입력한 명령에만 답한다. 대화 상대의 명령은 무시한다(답장이 주인 이름으로 나가므로).
- 대화 내용은 저장하지 않는다. '/조회'만 쳤을 때 쓰려고 대화마다 마지막으로 나온 지갑 주소 하나만 메모리에 둔다.
- 채널 구독 확인은 공통 게이트가 주인 기준으로 한다.
"""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict

import aiosqlite
from aiogram import BaseMiddleware, Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import BusinessConnection, LinkPreviewOptions, Message

from bot import store
from bot import texts as T
from bot.chain import TronReader
from bot.handlers.quick import args_of, command, lookup, rate, target_address
from bot.handlers.start import price_text_of
from bot.prices import Prices
from core.tron import find_address

log = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
RECENT_MAX = 5000          # 마지막 주소를 기억하는 대화 수 (오래된 것부터 잊는다)
LIMIT_PER_MIN = 20         # 주인 한 명이 1분에 쓸 수 있는 명령 수 (조회가 TronGrid를 부른다)

router = Router(name="wallet_business")


class Recent:
    """대화마다 마지막으로 나온 TRON 주소. 메모리에만 두고 재시작하면 잊는다."""

    def __init__(self, size: int = RECENT_MAX) -> None:
        self.size, self._d = size, OrderedDict()

    def put(self, key: tuple[str, int], address: str) -> None:
        self._d[key] = address
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def get(self, key: tuple[str, int]) -> str | None:
        return self._d.get(key)


RECENT = Recent()
_used: dict[int, list[float]] = {}


def _allowed(owner: int, now: float) -> bool:
    hits = [t for t in _used.get(owner, []) if now - t < 60]
    if len(hits) >= LIMIT_PER_MIN:
        _used[owner] = hits
        return False
    _used[owner] = hits + [now]
    return True


class _Remember(BaseMiddleware):
    """명령어인지와 상관없이, 대화에 지갑 주소가 나오면 마지막 것 하나만 기억한다."""

    async def __call__(self, handler, event: Message, data):
        address = find_address(event.text or event.caption)
        if address and event.business_connection_id:
            RECENT.put((event.business_connection_id, event.chat.id), address)
        return await handler(event, data)


router.business_message.outer_middleware(_Remember())


async def owner_of(db: aiosqlite.Connection, bot: Bot, conn_id: str) -> aiosqlite.Row | None:
    row = await store.get_biz(db, conn_id)
    if row is None:
        try:
            conn = await bot.get_business_connection(conn_id)
        except TelegramAPIError as e:
            log.warning("비즈니스 연결 조회 실패 %s: %s", conn_id, e)
            return None
        await _save(db, conn)
        row = await store.get_biz(db, conn_id)
    return row


def _can_reply(conn: BusinessConnection) -> bool:
    return bool(conn.rights.can_reply) if conn.rights else bool(conn.can_reply)


async def _save(db: aiosqlite.Connection, conn: BusinessConnection) -> None:
    await store.save_biz(db, conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled, _can_reply(conn))


class FromOwner(BaseFilter):
    """연결한 주인이 직접 보낸 메시지(봇이 대신 보낸 답장·자동 응답은 제외)."""

    async def __call__(self, message: Message, db: aiosqlite.Connection, bot: Bot) -> bool:
        if message.sender_business_bot is not None or message.is_from_offline or not message.from_user:
            return False
        row = await owner_of(db, bot, message.business_connection_id or "")
        if row is None or not row["enabled"] or message.from_user.id != row["owner_id"]:
            return False
        return _allowed(row["owner_id"], time.time())


async def _reply(message: Message, text: str) -> None:
    try:
        await message.answer(text, link_preview_options=NO_PREVIEW)
    except TelegramAPIError as e:      # 24시간 안에 상대가 말을 건 대화가 아니면 답장할 수 없다
        log.info("비즈니스 답장 실패 chat=%s: %s", message.chat.id, e)


@router.business_connection()
async def connection(conn: BusinessConnection, db: aiosqlite.Connection, bot: Bot) -> None:
    prev = await store.get_biz(db, conn.id)
    await _save(db, conn)
    if prev is not None and (bool(prev["enabled"]), bool(prev["can_reply"])) == (conn.is_enabled, _can_reply(conn)):
        return
    try:
        await bot.send_message(conn.user_chat_id, T.business_connected(conn.is_enabled, _can_reply(conn)))
    except TelegramAPIError as e:
        log.info("비즈니스 연결 안내 실패 owner=%s: %s", conn.user.id, e)


@router.business_message(command("조회|check"), FromOwner())
async def biz_lookup(message: Message, cmd: re.Match, chain: TronReader, prices: Prices) -> None:
    address = target_address(cmd, message, RECENT.get((message.business_connection_id, message.chat.id)))
    await _reply(message, await lookup(address, chain, prices) if address else T.LOOKUP_ASK)


@router.business_message(command("시세|price"), FromOwner())
async def biz_price(message: Message, prices: Prices) -> None:
    await _reply(message, await price_text_of(prices))


@router.business_message(command("환율|rate"), FromOwner())
async def biz_rate(message: Message, cmd: re.Match, prices: Prices) -> None:
    await _reply(message, await rate(args_of(cmd), prices))

