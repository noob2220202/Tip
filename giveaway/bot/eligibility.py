"""참가 자격: 필수 채널을 지금 구독 중인가. 실행할 때마다 한 명씩 확인한다."""
from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from core.config import BotConfig
from core.subscription import make_verifier
from core.verifier import VerifierError, is_joined

OK, MISSING, ERROR = "ok", "missing", "error"


class Checker:
    def __init__(self, cfg: BotConfig, bot: Bot):
        self.channels, self.bot, self.verifier = cfg.common.channels, bot, make_verifier(cfg)

    async def check(self, user_id: int) -> str:
        """ok(구독 중) · missing(구독 안 함) · error(확인 자체가 실패). 확인이 안 되면 당첨시키지 않고 error로 돌려준다."""
        if not self.channels:
            return OK
        if self.verifier is not None:
            try:
                res = await self.verifier.check(user_id)
            except VerifierError:
                return ERROR
            if res.get("errors"):
                return ERROR
            return MISSING if res.get("missing") else OK
        for ch in self.channels:
            try:
                member = await self.bot.get_chat_member(ch.chat_id, user_id)
            except TelegramAPIError:
                return ERROR
            if not is_joined(member):
                return MISSING
        return OK
