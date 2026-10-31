"""텔레그램 비즈니스: 주인이 자기 DM 대화에 봇을 연결하면 그 대화창에서 /조회 /시세 /환율에 답한다.

- 연결한 주인이 직접 입력한 명령에만 답한다. 대화 상대의 명령은 무시한다(답장이 주인 이름으로 나가므로).
- 대화창에는 결과만 올린다. 사용법 안내·오류·잘못 친 주소 안내는 주인에게 봇 대화로만 보낸다.
- 대화 내용은 저장하지 않는다. '/조회'만 쳤을 때 쓰려고 대화마다 마지막으로 나온 지갑 주소 하나만(메시지 번호와 함께)
  메모리에 두고, 그 메시지가 지워지거나 고쳐지면 같이 지우거나 바꾼다. 재시작하면 모두 잊는다.
  이 기록은 구독 확인보다 먼저 한다(main.py의 pre_gate). 주인이 잠깐 미가입이어도 대화에 나온 주소를 놓치지 않게.
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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, LinkPreviewOptions, Message, Update

from bot import store
from bot import texts as T
from bot.chain import TronReader
from bot.handlers.quick import LIMIT, args_of, command, lookup, rate, target_address
from bot.handlers.start import price_text_of
from bot.prices import Prices
from core.subscription import SubscriptionGate
from core.tron import find_address

log = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
RECENT_MAX = 5000          # 마지막 주소를 기억하는 대화 수 (오래된 것부터 잊는다)
FAIL_NOTICE_EVERY = 600    # 대화창 답장이 막혔다고 주인에게 알리는 최소 간격(초)

router = Router(name="wallet_business")


class Recent:
    """대화마다 마지막으로 나온 TRON 주소와 그 메시지 번호. 메모리에만 두고 재시작하면 잊는다."""

    def __init__(self, size: int = RECENT_MAX) -> None:
        self.size, self._d = size, OrderedDict()

    def put(self, key: tuple[str, int], message_id: int, address: str) -> None:
        self._d[key] = (message_id, address)
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def get(self, key: tuple[str, int]) -> str | None:
        hit = self._d.get(key)
        return hit[1] if hit else None

    def edited(self, key: tuple[str, int], message_id: int, address: str | None) -> None:
        """고쳐진 메시지: 기억한 것보다 새(같은) 메시지에 주소가 생겼으면 그것을 기억하고,
        기억한 메시지에서 주소가 없어졌으면 지운다."""
        hit = self._d.get(key)
        if address and (hit is None or message_id >= hit[0]):
            self.put(key, message_id, address)
        elif hit and hit[0] == message_id and not address:
            del self._d[key]

    def deleted(self, key: tuple[str, int], message_ids) -> None:
        hit = self._d.get(key)
        if hit and hit[0] in set(message_ids):
            del self._d[key]

    def clear(self) -> None:
        self._d.clear()


RECENT = Recent()
_fail_notice: dict[int, float] = {}


def _human(m: Message) -> bool:
    """사람이 쓴 메시지(봇이 주인 대신 보낸 답장·자동 응답이 아닌 것)."""
    return m.sender_business_bot is None and not m.is_from_offline


class AddressMemory(BaseMiddleware):
    """구독 확인보다 먼저 모든 update에 붙는다(dp.update). 비즈니스 대화에 나온 지갑 주소를 기억하고,
    그 메시지가 고쳐지거나 지워지면 같이 바꾸거나 지운다. 명령어인지와 상관없이 마지막 주소 하나만."""

    async def __call__(self, handler, event: Update, data):
        m = event.business_message
        if m is not None and m.business_connection_id and _human(m):
            address = find_address(m.text or m.caption)
            if address:
                RECENT.put((m.business_connection_id, m.chat.id), m.message_id, address)
        e = event.edited_business_message
        if e is not None and e.business_connection_id and _human(e):
            RECENT.edited((e.business_connection_id, e.chat.id), e.message_id, find_address(e.text or e.caption))
        d = event.deleted_business_messages
        if d is not None:
            RECENT.deleted((d.business_connection_id, d.chat.id), d.message_ids)
        return await handler(event, data)


MEMORY = AddressMemory()


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
    """연결한 주인이 직접 보낸 메시지(봇이 대신 보낸 답장·자동 응답은 제외). 통과하면 연결 정보를 biz로 넘긴다."""

    async def __call__(self, message: Message, db: aiosqlite.Connection, bot: Bot) -> bool | dict:
        if message.sender_business_bot is not None or message.is_from_offline or not message.from_user:
            return False
        row = await owner_of(db, bot, message.business_connection_id or "")
        if row is None or not row["enabled"] or message.from_user.id != row["owner_id"]:
            return False
        return {"biz": row}


async def _tell_owner(bot: Bot, biz: aiosqlite.Row, text: str) -> None:
    """주인에게만 봇 대화로 (대화 상대는 보지 못한다)."""
    try:
        await bot.send_message(biz["chat_id"], text, link_preview_options=NO_PREVIEW)
    except TelegramAPIError as e:
        log.info("비즈니스 주인 %s에게 안내 실패: %s", biz["owner_id"], e)


async def _reply(message: Message, bot: Bot, biz: aiosqlite.Row, text: str) -> None:
    """대화창에 결과를 올린다. 막히면(24시간 규칙·권한) 주인에게 이유를 알린다."""
    try:
        await message.answer(text, link_preview_options=NO_PREVIEW)
    except (TelegramBadRequest, TelegramForbiddenError) as e:     # 24시간 규칙·권한 같은 거절
        log.info("비즈니스 답장 실패 chat=%s: %s", message.chat.id, e)
        now = time.time()
        if now - _fail_notice.get(biz["owner_id"], 0) >= FAIL_NOTICE_EVERY:
            _fail_notice[biz["owner_id"]] = now
            await _tell_owner(bot, biz, T.BIZ_REPLY_FAIL)
    except TelegramAPIError as e:                                 # 일시적인 오류(호출 한도·네트워크·서버)
        log.info("비즈니스 답장 일시 실패 chat=%s: %s", message.chat.id, e)
        await _tell_owner(bot, biz, T.BIZ_REPLY_RETRY)


async def _answer(message: Message, bot: Bot, biz: aiosqlite.Row, text: str, ok: bool) -> None:
    await (_reply(message, bot, biz, text) if ok else _tell_owner(bot, biz, text))


@router.business_connection()
async def connection(conn: BusinessConnection, db: aiosqlite.Connection, bot: Bot,
                     gate: SubscriptionGate | None = None) -> None:
    prev = await store.get_biz(db, conn.id)
    await _save(db, conn)
    if prev is not None and (bool(prev["enabled"]), bool(prev["can_reply"])) == (conn.is_enabled, _can_reply(conn)):
        return
    missing, kb = [], None
    if conn.is_enabled and _can_reply(conn) and gate is not None and gate.cfg.common.channels \
            and not (gate.cfg.common.exempt_admins and conn.user.id in gate.cfg.admin_ids):
        missing = await gate.missing(bot, conn.user.id)
        kb = gate.keyboard(missing) if missing else None
    try:
        await bot.send_message(conn.user_chat_id, T.business_connected(conn.is_enabled, _can_reply(conn), bool(missing)),
                               reply_markup=kb)
    except TelegramAPIError as e:
        log.info("비즈니스 연결 안내 실패 owner=%s: %s", conn.user.id, e)


@router.business_message(command("조회|check"), FromOwner())
async def biz_lookup(message: Message, cmd: re.Match, biz: aiosqlite.Row, bot: Bot, chain: TronReader,
                     prices: Prices) -> None:
    address, bad = target_address(cmd, message, RECENT.get((message.business_connection_id, message.chat.id)))
    if bad:
        await _tell_owner(bot, biz, T.BAD_ADDRESS)
    elif not address:
        await _tell_owner(bot, biz, T.LOOKUP_ASK_BIZ)
    elif not LIMIT.allow(biz["owner_id"]):
        await _tell_owner(bot, biz, T.SLOW_DOWN)
    else:
        await _answer(message, bot, biz, *await lookup(address, chain, prices))


@router.business_message(command("시세|price"), FromOwner())
async def biz_price(message: Message, biz: aiosqlite.Row, bot: Bot, prices: Prices) -> None:
    if not LIMIT.allow(biz["owner_id"]):
        await _tell_owner(bot, biz, T.SLOW_DOWN)
        return
    text = await price_text_of(prices)
    await _answer(message, bot, biz, text, text != T.PRICE_FAIL)


@router.business_message(command("환율|rate"), FromOwner())
async def biz_rate(message: Message, cmd: re.Match, biz: aiosqlite.Row, bot: Bot, prices: Prices) -> None:
    if not LIMIT.allow(biz["owner_id"]):
        await _tell_owner(bot, biz, T.SLOW_DOWN)
        return
    await _answer(message, bot, biz, *await rate(args_of(cmd), prices, tip=False))


# 고친·지운 메시지는 AddressMemory가 처리한다. 여기서는 텔레그램이 이 업데이트들을 보내도록(allowed_updates) 받아만 둔다.
@router.edited_business_message()
async def biz_edited(message: Message) -> None:
    return None


@router.deleted_business_messages()
async def biz_deleted(event: BusinessMessagesDeleted) -> None:
    return None
