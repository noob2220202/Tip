from aiogram import Router

from bot.handlers import admin, calc, order, start

# 관리자 명령과 /start, /help를 먼저 처리하고, 상태가 없는 일반 텍스트는 order.fallback이 받는다
router = Router(name="boost")
router.include_router(admin.router)
router.include_router(start.router)
router.include_router(calc.router)
router.include_router(order.router)

__all__ = ["router"]
