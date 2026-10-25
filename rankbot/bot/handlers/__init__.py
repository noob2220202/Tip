from aiogram import Router

from bot.handlers import mybots, start

router = Router(name="rankbot")
router.include_router(start.router)
router.include_router(mybots.router)

__all__ = ["router"]
