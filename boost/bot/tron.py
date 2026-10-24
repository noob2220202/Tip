"""TronGrid: 내 지갑으로 들어온 USDT(TRC20) 전송 조회."""
from __future__ import annotations

from dataclasses import dataclass

import aiohttp

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
PAGE_LIMIT = 200
MAX_PAGES = 5


class TronError(Exception):
    pass


@dataclass(frozen=True)
class Transfer:
    txid: str
    sender: str
    to: str
    value: int      # 마이크로 USDT
    ts: int         # 블록 시간 (ms)


class TronGrid:
    def __init__(self, base: str, api_key: str, wallet: str, contract: str = USDT_CONTRACT) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.wallet = wallet
        self.contract = contract
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            headers = {"TRON-PRO-API-KEY": self.api_key} if self.api_key else {}
            self._session = aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def incoming(self, since_ms: int) -> list[Transfer]:
        """since_ms 이후 블록에 확정된 입금(USDT)만 돌려준다."""
        url = f"{self.base}/v1/accounts/{self.wallet}/transactions/trc20"
        params: dict[str, str | int] = {
            "only_to": "true", "only_confirmed": "true", "contract_address": self.contract,
            "min_timestamp": since_ms, "limit": PAGE_LIMIT, "order_by": "block_timestamp,asc",
        }
        out: list[Transfer] = []
        for _ in range(MAX_PAGES):
            try:
                async with self._http().get(url, params=params) as r:
                    if r.status != 200:
                        raise TronError(f"TronGrid 응답 코드 {r.status}")
                    body = await r.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                raise TronError(f"TronGrid 요청 실패: {e}") from e
            if not isinstance(body, dict) or not body.get("success", True):
                raise TronError("TronGrid 응답이 올바르지 않아요")
            for row in body.get("data") or []:
                t = _parse(row, self.wallet, self.contract)
                if t:
                    out.append(t)
            fp = (body.get("meta") or {}).get("fingerprint")
            if not fp:
                break
            params["fingerprint"] = fp
        return out


def _parse(row: dict, wallet: str, contract: str) -> Transfer | None:
    try:
        if row.get("type") != "Transfer":
            return None
        if (row.get("token_info") or {}).get("address") != contract:
            return None
        if row["to"] != wallet:
            return None
        return Transfer(row["transaction_id"], row["from"], row["to"], int(row["value"]), int(row["block_timestamp"]))
    except (KeyError, ValueError, TypeError):
        return None
