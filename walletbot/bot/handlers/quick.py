"""/조회(주소 잔액·에너지)와 /환율. 봇 대화와 비즈니스 대화가 같이 쓴다."""
from __future__ import annotations

import asyncio
import re
import time

from aiogram import F, Router
from aiogram.types import Message

from bot import money
from bot import texts as T
from bot.chain import TronError, TronReader
from bot.handlers.start import NO_PREVIEW, PRIVATE
from bot.prices import PriceError, Prices
from core.tron import find_address

LIMIT_PER_MIN = 20         # 한 사람이 1분에 쓸 수 있는 조회·환율 명령 수 (조회는 TronGrid를 부르고 키를 입금 감시와 같이 쓴다)

router = Router(name="wallet_quick")
router.message.filter(PRIVATE)


class Limiter:
    """사람마다 최근 1분 동안 쓴 횟수를 센다."""

    def __init__(self, per_min: int = LIMIT_PER_MIN) -> None:
        self.per_min, self._hits = per_min, {}

    def allow(self, key: int, now: float | None = None) -> bool:
        now = now or time.time()
        hits = [t for t in self._hits.get(key, ()) if now - t < 60]
        if len(hits) >= self.per_min:
            self._hits[key] = hits
            return False
        self._hits[key] = hits + [now]
        return True

    def clear(self) -> None:
        self._hits.clear()


LIMIT = Limiter()


def command(words: str):
    """한글 명령어 + 뒤에 붙은 값. '/조회 T…', '/환율 10만원'. 값은 match.group('args')."""
    return F.text.regexp(re.compile(rf"^\s*[/／]\s*(?:{words})(?:@\S+)?(?:\s+(?P<args>.*?))?\s*$", re.S)).as_("cmd")


def args_of(cmd: re.Match) -> str:
    return (cmd.group("args") or "").strip()


def target_address(cmd: re.Match, message: Message, recent: str | None = None) -> tuple[str | None, bool]:
    """(조회할 주소, 잘못 친 주소인지). 명령 뒤에 무언가 썼으면 그것만 본다(틀렸으면 다른 주소로 대신 조회하지 않는다).
    아무것도 안 썼으면 답장한 메시지의 주소 → 대화에 나온 마지막 주소."""
    args = args_of(cmd)
    if args:
        address = find_address(args)
        return address, address is None
    reply = message.reply_to_message
    return (find_address(reply.text or reply.caption) if reply else None) or recent, False


async def lookup(address: str, chain: TronReader, prices: Prices) -> tuple[str, bool]:
    """(보낼 글, 조회 성공 여부)"""
    try:
        usdt, acct = await asyncio.gather(chain.usdt_of(address), chain.account(address))
    except TronError:
        return T.CHAIN_FAIL, False
    try:
        quote = await prices.get()
    except PriceError:
        quote = None
    return T.lookup_text(address, usdt, acct, quote), True


async def rate(args: str, prices: Prices, tip: bool = True) -> tuple[str, bool]:
    """(보낼 글, 계산 성공 여부)"""
    amount = money.parse_amount(args) if args else None
    if args and amount is None:
        return T.RATE_BAD, False
    try:
        text = T.rate_text(await prices.get(), amount, tip=tip)
    except PriceError:
        return T.PRICE_FAIL, False
    return text, text != T.FX_FAIL


@router.message(command("조회|check"))
async def lookup_cmd(message: Message, cmd: re.Match, chain: TronReader, prices: Prices) -> None:
    address, bad = target_address(cmd, message)
    if bad:
        await message.answer(T.BAD_ADDRESS)
    elif not address:
        await message.answer(T.LOOKUP_ASK)
    elif not LIMIT.allow(message.from_user.id):
        await message.answer(T.SLOW_DOWN)
    else:
        await message.answer((await lookup(address, chain, prices))[0], link_preview_options=NO_PREVIEW)


@router.message(command("환율|rate"))
async def rate_cmd(message: Message, cmd: re.Match, prices: Prices) -> None:
    if not LIMIT.allow(message.from_user.id):
        await message.answer(T.SLOW_DOWN)
        return
    await message.answer((await rate(args_of(cmd), prices))[0])
