"""SMM 패널 표준 API v2 (key, action, ...) 클라이언트."""
from __future__ import annotations

import aiohttp


class PanelRejected(Exception):
    """패널이 요청을 명확히 거절했다 (주문이 만들어지지 않았다). 다시 시도해도 된다."""


class PanelUncertain(Exception):
    """네트워크 오류·시간 초과·이상한 응답. 주문이 만들어졌는지 알 수 없다. 다시 보내면 중복될 수 있다."""


class SmmPanel:
    def __init__(self, url: str, key: str) -> None:
        self.url = url
        self.key = key
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def _call(self, **params: object) -> dict:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        data = {"key": self.key, **{k: str(v) for k, v in params.items()}}
        try:
            async with self._session.post(self.url, data=data) as r:
                body = await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise PanelUncertain(f"패널 요청 실패: {e}") from e
        if not isinstance(body, dict):
            raise PanelUncertain("패널 응답 형식이 올바르지 않아요")
        return body

    async def add(self, service: int, link: str, quantity: int) -> str:
        body = await self._call(action="add", service=service, link=link, quantity=quantity)
        if body.get("error"):
            raise PanelRejected(str(body["error"]))
        if body.get("order") in (None, ""):
            raise PanelUncertain("패널 응답에 주문 번호가 없어요")
        return str(body["order"])

    async def status(self, order_id: str) -> dict:
        body = await self._call(action="status", order=order_id)
        if body.get("error"):
            raise PanelRejected(str(body["error"]))
        return body

    async def balance(self) -> tuple[str, str]:
        body = await self._call(action="balance")
        if body.get("error"):
            raise PanelRejected(str(body["error"]))
        return str(body.get("balance", "?")), str(body.get("currency", ""))
