from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from bot.texts import SHOP_URL
from core.texts import Texts

# 안내문에 이모티콘샵 링크가 들어가므로 미리보기 카드는 끈다
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def shop_keyboard(texts: Texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.get("shop_button"), url=SHOP_URL)]])
