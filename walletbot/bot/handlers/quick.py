"""/조회(주소 잔액·에너지)와 /환율. 봇 대화와 비즈니스 대화가 같이 쓴다."""
from __future__ import annotations

import asyncio
import re

from aiogram import F, Router
from aiogram.types import Message

from bot import money
from bot import texts as T
from bot.chain import TronError, TronReader
from bot.handlers.start import NO_PREVIEW, PRIVATE
from bot.prices import PriceError, Prices
from core.tron import find_address

router = Router(name="wallet_quick")
router.message.filter(PRIVATE)


def command(words: str):
    """한글 명령어 + 뒤에 붙은 값. '/조회 T…', '/환율 10만원'. 값은 match.group('args')."""
    return F.text.regexp(re.compile(rf"^\s*[/／]\s*(?:{words})(?:@\S+)?(?:\s+(?P<args>.*?))?\s*$", re.S)).as_("cmd")


def args_of(cmd: re.Match) -> str:
    return (cmd.group("args") or "").strip()


def target_address(cmd: re.Match, message: Message, recent: str | None = None) -> str | None:
    """조회할 주소: 명령 뒤에 쓴 주소 → 답장한 메시지의 주소 → 대화에 나온 마지막 주소."""
    reply = message.reply_to_message
    return (find_address(args_of(cmd))
            or (find_address(reply.text or reply.caption) if reply else None)
            or recent)


async def lookup(address: str, chain: TronReader, prices: Prices) -> str:
    try:
        usdt, acct = await asyncio.gather(chain.usdt_of(address), chain.account(address))
    except TronError:
        return T.CHAIN_FAIL
    try:
        quote = await prices.get()
    except PriceError:
        quote = None
    return T.lookup_text(address, usdt, acct, quote)


async def rate(args: str, prices: Prices) -> str:
    amount = money.parse_amount(args) if args else None
    if args and amount is None:
        return T.RATE_BAD
    try:
        return T.rate_text(await prices.get(), amount)
    except PriceError:
        return T.PRICE_FAIL


@router.message(command("조회|check"))
async def lookup_cmd(message: Message, cmd: re.Match, chain: TronReader, prices: Prices) -> None:
    address = target_address(cmd, message)
    if not address:
        await message.answer(T.LOOKUP_ASK)
        return
    await message.answer(await lookup(address, chain, prices), link_preview_options=NO_PREVIEW)


@router.message(command("환율|rate"))
async def rate_cmd(message: Message, cmd: re.Match, prices: Prices) -> None:
    await message.answer(await rate(args_of(cmd), prices))
