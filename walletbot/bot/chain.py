"""TronGrid 조회: 입금 내역(USDT TRC20 + TRX)과 잔액."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp

from core.tron import address_to_hex, hex_to_address

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_HEX = address_to_hex(USDT_CONTRACT)
PAGE_LIMIT = 200
MAX_PAGES = 5
BALANCE_TTL = 15   # 같은 주소를 연달아 조회할 때 TronGrid를 아끼려고 잠깐 기억해 둔다(초)


class TronError(Exception):
    pass


class RateLimited(TronError):
    """TronGrid가 호출 한도를 넘었다고 응답했다(429)."""


@dataclass(frozen=True)
class Deposit:
    txid: str
    token: str      # "USDT" 또는 "TRX"
    sender: str
    value: int      # 마이크로 단위
    ts_ms: int      # 블록 시간
    to: str = ""    # 받은 지갑 주소 (블록을 훑을 때 채운다)


@dataclass(frozen=True)
class Block:
    number: int
    ts_ms: int
    txs: list


@dataclass(frozen=True)
class Balance:
    usdt: int
    trx: int


class TronReader:
    def __init__(self, base: str, api_key: str = "") -> None:
        self.base, self.api_key = base.rstrip("/"), api_key
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Balance]] = {}

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            headers = {"TRON-PRO-API-KEY": self.api_key} if self.api_key else {}
            self._session = aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            async with self._http().get(f"{self.base}{path}", params=params) as r:
                if r.status == 429:
                    raise RateLimited("TronGrid 호출 한도 초과")
                if r.status != 200:
                    raise TronError(f"TronGrid 응답 코드 {r.status}")
                body = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise TronError(f"TronGrid 요청 실패: {e}") from e
        if not isinstance(body, dict) or not body.get("success", True):
            raise TronError("TronGrid 응답이 올바르지 않아요")
        return body

    async def _post(self, path: str, body: dict) -> dict:
        try:
            async with self._http().post(f"{self.base}{path}", json=body) as r:
                if r.status == 429:
                    raise RateLimited("TronGrid 호출 한도 초과")
                if r.status != 200:
                    raise TronError(f"TronGrid 응답 코드 {r.status}")
                out = await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise TronError(f"TronGrid 요청 실패: {e}") from e
        if not isinstance(out, dict) or "Error" in out:
            raise TronError(f"TronGrid 응답이 올바르지 않아요: {str(out)[:100]}")
        return out

    @staticmethod
    def _block(body: dict) -> Block | None:
        """블록 응답을 읽는다. 아직 생기지 않은 블록은 빈 객체({})로 오므로 None."""
        try:
            h = body["block_header"]["raw_data"]
            return Block(int(h["number"]), int(h["timestamp"]), body.get("transactions") or [])
        except (KeyError, ValueError, TypeError):
            if body:
                raise TronError("블록 응답을 읽지 못했어요") from None
            return None

    async def head_block(self) -> int:
        b = self._block(await self._post("/wallet/getnowblock", {}))
        if b is None:
            raise TronError("최신 블록을 가져오지 못했어요")
        return b.number

    async def block(self, number: int) -> Block | None:
        return self._block(await self._post("/wallet/getblockbynum", {"num": number}))

    async def _pages(self, path: str, params: dict) -> list[dict]:
        rows: list[dict] = []
        params = {**params, "limit": PAGE_LIMIT, "order_by": "block_timestamp,asc", "only_to": "true",
                  "only_confirmed": "true"}
        for _ in range(MAX_PAGES):
            body = await self._get(path, params)
            rows += body.get("data") or []
            fp = (body.get("meta") or {}).get("fingerprint")
            if not fp:
                break
            params["fingerprint"] = fp
        return rows

    async def deposits(self, address: str, since_ms: int) -> list[Deposit]:
        """since_ms 이후 블록에 확정된 입금. 둘 중 하나라도 실패하면 예외(일부만 믿고 커서를 넘기지 않기 위해)."""
        usdt_rows, trx_rows = await asyncio.gather(
            self._pages(f"/v1/accounts/{address}/transactions/trc20",
                        {"contract_address": USDT_CONTRACT, "min_timestamp": since_ms}),
            self._pages(f"/v1/accounts/{address}/transactions", {"min_timestamp": since_ms}),
            return_exceptions=True,
        )
        for rows in (usdt_rows, trx_rows):          # 한쪽만 실패해도 전체를 실패로 본다 (두 번째 예외를 흘리지 않도록 직접 던진다)
            if isinstance(rows, BaseException):
                raise rows
        out = [d for d in (_parse_usdt(r, address) for r in usdt_rows) if d]
        out += [d for d in (_parse_trx(r, address) for r in trx_rows) if d]
        return sorted(out, key=lambda d: d.ts_ms)

    async def balance(self, address: str, now: float = 0.0, fresh: bool = False) -> Balance:
        import time

        now = now or time.time()
        hit = self._cache.get(address)
        if hit and now - hit[0] < BALANCE_TTL and not fresh:
            return hit[1]
        body = await self._get(f"/v1/accounts/{address}")
        data = (body.get("data") or [{}])[0]     # 아직 활성화되지 않은 주소는 data가 비어 있다 → 잔액 0
        usdt = 0
        for entry in data.get("trc20") or []:
            if USDT_CONTRACT in entry:
                try:
                    usdt = int(entry[USDT_CONTRACT])
                except (ValueError, TypeError):
                    raise TronError("USDT 잔액을 읽지 못했어요") from None
        try:
            bal = Balance(usdt=usdt, trx=int(data.get("balance") or 0))
        except (ValueError, TypeError):
            raise TronError("TRX 잔액을 읽지 못했어요") from None
        self._cache[address] = (now, bal)
        return bal


def _parse_usdt(row: dict, address: str) -> Deposit | None:
    try:
        if row.get("type") != "Transfer" or (row.get("token_info") or {}).get("address") != USDT_CONTRACT:
            return None
        if row["to"] != address or int(row["value"]) <= 0:
            return None
        return Deposit(row["transaction_id"], "USDT", row["from"], int(row["value"]), int(row["block_timestamp"]))
    except (KeyError, ValueError, TypeError):
        return None


def _parse_trx(row: dict, address: str) -> Deposit | None:
    """TRX 일반 전송만. 컨트랙트 호출이나 실패한 거래는 입금으로 보지 않는다."""
    try:
        if ((row.get("ret") or [{}])[0]).get("contractRet") != "SUCCESS":
            return None
        c = row["raw_data"]["contract"][0]
        if c.get("type") != "TransferContract":
            return None
        v = c["parameter"]["value"]
        if hex_to_address(v["to_address"]) != address or int(v["amount"]) <= 0:
            return None
        sender = hex_to_address(v["owner_address"]) or str(v["owner_address"])
        return Deposit(row["txID"], "TRX", sender, int(v["amount"]), int(row["block_timestamp"]))
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def scan_block(block: Block, watch: dict[str, str]) -> list[Deposit]:
    """블록 안의 거래에서 감시 중인 주소로 들어온 입금을 찾는다. watch는 {hex 주소: 'T...' 주소}.

    TRX는 일반 전송, USDT는 컨트랙트의 transfer / transferFrom 호출 데이터에서 읽는다.
    실패한 거래는 건너뛴다. (일괄 전송 컨트랙트처럼 호출 데이터에 안 드러나는 입금은 못 찾는다 → 잔액 대조가 보충)
    받은 지갑은 Deposit.to에 담는다.
    """
    out: list[Deposit] = []
    for tx in block.txs:
        try:
            if ((tx.get("ret") or [{}])[0]).get("contractRet") != "SUCCESS":
                continue
            c = tx["raw_data"]["contract"][0]
            v = c["parameter"]["value"]
            kind = c.get("type")
            if kind == "TransferContract":
                to, amount, sender, token = v["to_address"].lower(), int(v["amount"]), v["owner_address"], "TRX"
            elif kind == "TriggerSmartContract" and (v.get("contract_address") or "").lower() == USDT_HEX:
                data = (v.get("data") or "").lower()
                if data.startswith("a9059cbb") and len(data) >= 136:           # transfer(to, amount)
                    to, amount, sender = "41" + data[32:72], int(data[72:136], 16), v["owner_address"]
                elif data.startswith("23b872dd") and len(data) >= 200:         # transferFrom(from, to, amount)
                    sender, to, amount = "41" + data[32:72], "41" + data[96:136], int(data[136:200], 16)
                else:
                    continue
                token = "USDT"
            else:
                continue
            if to in watch and amount > 0:
                out.append(Deposit(tx["txID"], token, hex_to_address(sender) or str(sender), amount, block.ts_ms, watch[to]))
        except (KeyError, ValueError, TypeError, IndexError, AttributeError):
            continue
    return out
