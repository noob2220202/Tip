"""로깅 설정 + 토큰 마스킹."""
from __future__ import annotations

import logging
import re

TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{30,}")


class TokenMaskFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = TOKEN_RE.sub("<token>", str(record.getMessage()))
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            # traceback 안의 토큰도 가린다
            record.exc_text = TOKEN_RE.sub("<token>", logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(TokenMaskFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # aiogram/aiohttp 요청 URL(토큰 포함)이 찍히지 않게 한다
    for noisy in ("aiohttp.client", "aiohttp.access", "aiogram.client.session"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
