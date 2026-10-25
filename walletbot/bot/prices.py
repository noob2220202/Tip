"""시세: 빗썸 USDT/KRW·TRX/KRW와 기준 환율(USD/KRW), 김프 계산."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiohttp

CACHE_TTL = 10        # 같은 시세를 이 시간(초) 안에는 다시 요청하지 않는다
STALE_LIMIT = 600     # 조회가 실패하면 이 시간(초) 안의 옛 값은 '조회 지연'으로 표시하며 쓴다


class PriceError(Exception):
    pass


@dataclass(frozen=True)
class Quote:
    usdt_krw: float
    trx_krw: float
    fx: float | None        # USD/KRW 기준 환율. 환율 조회가 실패하면 None
    ts: float
    stale: bool = False

    @property
    def kimp(self) -> float | None:
        """김치 프리미엄(%). USDT 1개를 1달러로 보고 (빗썸 USDT 가격 ÷ 환율 - 1)로 계산한다."""
        return None if not self.fx else (self.usdt_krw / self.fx - 1) * 100


class Prices:
    def __init__(self, bithumb_base: str = "https://api.bithumb.com", fx_base: str = "https://open.er-api.com") -> None:
        self.bithumb, self.fx_base = bithumb_base.rstrip("/"), fx_base.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._last: Quote | None = None
        self._last_fx: float | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def _json(self, url: str):
        try:
            async with self._http().get(url) as r:
                if r.status != 200:
                    raise PriceError(f"응답 코드 {r.status}")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise PriceError(str(e)) from e

    async def _bithumb(self) -> tuple[float, float]:
        body = await self._json(f"{self.bithumb}/v1/ticker?markets=KRW-USDT,KRW-TRX")
        try:
            by = {row["market"]: float(row["trade_price"]) for row in body}
            return by["KRW-USDT"], by["KRW-TRX"]
        except (KeyError, TypeError, ValueError) as e:
            raise PriceError("빗썸 응답을 읽지 못했어요") from e

    async def _fx(self) -> float:
        body = await self._json(f"{self.fx_base}/v6/latest/USD")
        try:
            fx = float(body["rates"]["KRW"])
        except (KeyError, TypeError, ValueError) as e:
            raise PriceError("환율 응답을 읽지 못했어요") from e
        if fx <= 0:
            raise PriceError("환율이 올바르지 않아요")
        return fx

    async def get(self, now: float | None = None) -> Quote:
        now = now or time.time()
        async with self._lock:        # 동시에 여러 명이 불러도 외부 요청은 한 번만 나간다
            if self._last and now - self._last.ts < CACHE_TTL and not self._last.stale:
                return self._last
            bithumb, fx = await asyncio.gather(self._bithumb(), self._fx(), return_exceptions=True)
            if isinstance(bithumb, Exception):
                if self._last and now - self._last.ts < STALE_LIMIT:
                    q = self._last
                    self._last = Quote(q.usdt_krw, q.trx_krw, q.fx, q.ts, stale=True)
                    return self._last
                raise PriceError(f"빗썸 시세를 가져오지 못했어요: {bithumb}")
            if isinstance(fx, Exception):
                fx = self._last_fx if self._last_fx else None      # 환율만 실패하면 마지막으로 안 값을 쓴다
            else:
                self._last_fx = fx
            self._last = Quote(bithumb[0], bithumb[1], fx, now)
            return self._last
