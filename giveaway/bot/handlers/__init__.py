from aiogram import Router

from bot.handlers import admin, start

# 관리자 흐름을 먼저 붙여야 /start 같은 명령으로 입력 중인 작업을 취소할 수 있다
router = Router(name="giveaway")
router.include_router(admin.router)
router.include_router(start.router)

__all__ = ["router"]
