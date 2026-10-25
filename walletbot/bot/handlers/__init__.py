from aiogram import Router

from bot.handlers import alerts, start, wallets

# /start·/cancel 같은 명령을 먼저 처리해야 입력 대기 중에도 취소할 수 있다
router = Router(name="walletbot")
router.include_router(start.router)
router.include_router(wallets.router)
router.include_router(alerts.router)
router.include_router(start.fallback_router)

__all__ = ["router"]
