from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.style import esc
from core.texts import Texts

router = Router(name="example_bot")


@router.message(Command("ping"))
async def ping(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("pong"))


@router.message()
async def echo(message: Message) -> None:
    if message.text:
        await message.answer(esc(message.text))
