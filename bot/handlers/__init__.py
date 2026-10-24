from aiogram import Router

from bot.handlers import convert, start

# /start, /help를 먼저 처리하고, 나머지 텍스트는 링크 변환 핸들러로 넘긴다
router = Router(name="kakao")
router.include_router(start.router)
router.include_router(convert.router)

__all__ = ["router"]
