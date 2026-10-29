"""에너지 공급처 API 클라이언트. 1시간짜리 에너지를 받는 주소로 위임해 준다.

공급처마다 응답 형식은 달라도 결과는 세 가지로 나눈다.
  성공(Placed) · 거절(Rejected: 주문이 만들어지지 않았다, 다른 공급처로 넘어가도 된다)
  불확실(Uncertain: 주문이 됐는지 알 수 없다. 다른 공급처로 넘기면 두 번 살 수 있다)
불확실하면 같은 번호(ref)의 두 번째 요청을 새 주문으로 만들지 않는 공급처(idempotent)에만 다시 보낸다.
그렇지 않은 공급처는 다시 보내지 않고 체인에서 에너지가 들어왔는지로 판단한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import aiohttp

log = logging.getLogger(__name__)

SUN = 1_000_000
TIMEOUT = 20


class SupplierError(Exception):
    """가격·잔고 조회 실패. 주문과는 관계없다."""


class Rejected(Exception):
    """주문이 만들어지지 않았다(청구 없음)."""

    def __init__(self, msg: str, low_balance: bool = False) -> None:
        super().__init__(msg)
        self.low_balance = low_balance


class Uncertain(Exception):
    """주문이 됐는지 알 수 없다. 같은 ref로만 다시 보낸다."""


@dataclass(frozen=True)
class Placed:
    order_id: str
    txid: str = ""          # 위임 거래 해시 (공급처가 바로 안 주면 빈 값)
    cost: int = 0           # 실제로 빠진 금액(sun). 모르면 0


def trx_to_sun(v) -> int:
    return int((Decimal(str(v)) * SUN).to_integral_value())


class Supplier:
    name = ""
    min_energy = 65_000
    price_ttl = 30          # 가격을 다시 묻지 않는 시간(초)
    idempotent = True       # 같은 ref로 다시 보내도 새 주문이 생기지 않는다

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._price: tuple[float, float] | None = None     # (조회 시각, sun/에너지)
        self._lock = asyncio.Lock()

    def _http(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def unit_price(self, now: float | None = None) -> float:
        """1시간 에너지 1당 가격(sun). 짧게 기억해 둔다."""
        now = now or time.time()
        async with self._lock:
            if self._price and now - self._price[0] < self.price_ttl:
                return self._price[1]
            price = await self._fetch_price()
            if price <= 0:
                raise SupplierError(f"{self.name} 가격이 올바르지 않아요: {price}")
            self._price = (now, price)
            return price

    async def cost(self, energy: int, now: float | None = None) -> int:
        """energy만큼 1시간 빌리는 원가(sun)."""
        return int(Decimal(str(await self.unit_price(now))) * energy + Decimal("0.999999"))

    async def _fetch_price(self) -> float:
        raise NotImplementedError

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        raise NotImplementedError

    async def balance(self) -> int:
        """남은 잔고(sun)."""
        raise NotImplementedError

    async def _request(self, method: str, url: str, **kw) -> tuple[int, object]:
        """(HTTP 코드, JSON). 네트워크 오류·시간 초과·JSON이 아닌 응답은 Uncertain."""
        try:
            async with self._http().request(method, url, **kw) as r:
                try:
                    body = await r.json(content_type=None)
                except ValueError:
                    body = None
                return r.status, body
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise Uncertain(f"{self.name} 요청 실패: {e}") from e


# ───────────── Netts ─────────────

class Netts(Supplier):
    """https://doc.netts.io — X-API-KEY + 화이트리스트 IP(X-Real-IP). 서명 없음.

    주문: POST /order1h {amount, receiveAddress}. X-Idempotency-Key(64자 hex)를 주문마다 고정하면
    같은 키의 재요청은 208(이미 처리됨, 추가 청구 없음)이나 409(처리 중)를 준다.
    가격: GET /pricing?services=energy_1h (분당 1회 제한 → 길게 기억한다).
    """

    name = "netts"
    min_energy = 61_000
    price_ttl = 90

    def __init__(self, api_key: str, real_ip: str, base: str = "https://netts.io/apiv2") -> None:
        super().__init__(base)
        self.api_key, self.real_ip = api_key, real_ip

    def _headers(self, key: str | None = None) -> dict:
        h = {"X-API-KEY": self.api_key, "X-Real-IP": self.real_ip}
        if key:
            h["X-Idempotency-Key"] = key
        return h

    def idem_key(self, receiver: str, energy: int, ref: str) -> str:
        return hmac.new(self.api_key.encode(), f"{receiver}:{energy}:{ref}".encode(), hashlib.sha256).hexdigest()

    async def _fetch_price(self) -> float:
        try:
            status, body = await self._request("GET", f"{self.base}/pricing", params={"services": "energy_1h"},
                                               headers=self._headers())
        except Uncertain as e:
            raise SupplierError(str(e)) from e
        try:
            svc = body["data"]["services"]["energy_1h"]
            period = next(p for p in svc["periods"] if p.get("is_current")) if svc.get("periods") else None
            if period is None:
                raise KeyError("periods")
            price = period["price"]
            for t in period.get("tiers") or []:          # 구간 가격이 있으면 65k가 들어가는 구간 값을 쓴다
                if (t.get("min_energy") or 0) <= 65_000 <= (t.get("max_energy") or 10**12):
                    price = t.get("price", price)
            return float(price)
        except (KeyError, TypeError, ValueError, StopIteration):
            raise SupplierError(f"netts 가격 응답을 읽지 못했어요 ({status})") from None

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        status, body = await self._request("POST", f"{self.base}/order1h",
                                           json={"amount": energy, "receiveAddress": receiver},
                                           headers=self._headers(self.idem_key(receiver, energy, ref)))
        detail = body.get("detail") if isinstance(body, dict) else None
        if status in (200, 208) and isinstance(detail, dict) and detail.get("code") == 10000:
            data = detail.get("data") or {}
            if data.get("delegateAddress") not in (None, "", receiver):
                raise Uncertain(f"netts가 다른 주소로 처리했다고 응답했어요: {data.get('delegateAddress')}")
            return Placed(str(data.get("orderId") or ""), str(data.get("hash") or ""),
                          trx_to_sun(data.get("paidTRX") or 0))
        if status == 409:
            raise Uncertain("netts: 같은 주문을 처리하는 중")
        code = (body.get("code") if isinstance(body, dict) else None) or (detail.get("code") if isinstance(detail, dict) else None)
        msg = str((body.get("msg") if isinstance(body, dict) else None) or detail or body)[:200]
        if status == 403 and code == 1004:
            raise Rejected(f"netts 잔고 부족: {msg}", low_balance=True)
        if status in (400, 401, 422, 429) or (status == 503 and code in (5001, 5002, 5003, 5004)):
            raise Rejected(f"netts 거절 {status}: {msg}")
        raise Uncertain(f"netts 응답 {status}: {msg}")

    async def balance(self) -> int:
        try:
            status, body = await self._request("GET", f"{self.base}/userinfo", headers=self._headers())
            return trx_to_sun(body["stats"]["balance"])
        except (Uncertain, KeyError, TypeError, ValueError) as e:
            raise SupplierError(f"netts 잔고 조회 실패: {e}") from None


# ───────────── CatFee ─────────────

class CatFee(Supplier):
    """https://docs.catfee.io — CF-ACCESS-KEY / CF-ACCESS-SIGN / CF-ACCESS-TIMESTAMP.

    서명 = Base64(HMAC-SHA256(secret, 시각 + 메서드 + 경로?쿼리)). 본문은 없고 값은 모두 쿼리에 넣는다.
    서명한 문자열과 실제 주소가 한 글자라도 다르면 안 되므로 쿼리를 직접 만들어 그대로 보낸다.
    주문: POST /v1/order?quantity&receiver&duration=1h&client_order_id&activate=false
      같은 client_order_id는 새 주문을 만들지 않고 처음 주문을 돌려준다.
      HTTP 200이어도 code가 0이 아니거나, code=0이어도 status가 실패 값이면 실패다.
    """

    name = "catfee"
    min_energy = 65_000
    OK_STATUS = ("PAYMENT_SUCCESS", "DELEGATE_SUCCESS")
    PRE_CHECK = {1, 100, 101, 106, 107, 108}      # 요청을 검사하는 단계에서 막힌 것(서명·형식·한도)

    def __init__(self, api_key: str, secret: str, base: str = "https://api.catfee.io") -> None:
        super().__init__(base)
        self.api_key, self.secret = api_key, secret

    @staticmethod
    def stamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def sign(self, ts: str, method: str, path: str) -> str:
        import base64

        mac = hmac.new(self.secret.encode(), (ts + method.upper() + path).encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    async def _call(self, method: str, path: str, query: list[tuple[str, str]] | None = None) -> dict:
        from urllib.parse import urlencode

        import yarl

        full = f"{path}?{urlencode(query)}" if query else path
        ts = self.stamp()
        headers = {"Content-Type": "application/json", "CF-ACCESS-KEY": self.api_key,
                   "CF-ACCESS-SIGN": self.sign(ts, method, full), "CF-ACCESS-TIMESTAMP": ts}
        status, body = await self._request(method, yarl.URL(self.base + full, encoded=True), headers=headers)
        if not isinstance(body, dict):
            raise Uncertain(f"catfee 응답 {status}: JSON이 아님")
        return body

    @staticmethod
    def code_of(body: dict) -> int | None:
        try:
            return int(body.get("code"))
        except (TypeError, ValueError):
            return None

    async def _fetch_price(self) -> float:
        try:
            body = await self._call("GET", "/v1/estimate", [("quantity", "65000"), ("duration", "1h")])
            total = float(body["data"])
        except (Uncertain, KeyError, TypeError, ValueError) as e:
            raise SupplierError(f"catfee 가격 조회 실패: {e}") from None
        if self.code_of(body) != 0:
            raise SupplierError(f"catfee 가격 조회 실패: {body.get('msg')}")
        return total / 65_000 if total > 10_000 else total     # 총액(sun)으로 오면 1당 가격으로 바꾼다

    def _placed(self, data: dict, receiver: str) -> Placed:
        if data.get("receiver") not in (None, "", receiver):
            raise Uncertain(f"catfee가 다른 주소로 처리했다고 응답했어요: {data.get('receiver')}")
        return Placed(str(data.get("id") or ""), str(data.get("delegate_hash") or ""), int(data.get("pay_amount_sun") or 0))

    def _judge(self, data: dict, receiver: str) -> Placed:
        st = str(data.get("status") or "")
        if st in self.OK_STATUS:
            return self._placed(data, receiver)
        if st in ("INSUFFICIENT_BALANCE",):
            raise Rejected("catfee 잔고 부족", low_balance=True)
        if st in ("REFUND_FAIL", "UNKNOWN", ""):
            raise Uncertain(f"catfee 주문 상태 {st or '없음'}")
        raise Rejected(f"catfee 주문 실패: {st}")

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        q = [("quantity", str(energy)), ("receiver", receiver), ("duration", "1h"), ("client_order_id", ref),
             ("activate", "false")]
        body = await self._call("POST", "/v1/order", q)
        code = self.code_of(body)
        if code == 0 and isinstance(body.get("data"), dict):
            return self._judge(body["data"], receiver)
        if code == 201:
            raise Rejected(f"catfee 잔고 부족: {body.get('msg')}", low_balance=True)
        if code in self.PRE_CHECK:
            raise Rejected(f"catfee 거절 {code}: {body.get('msg')}")
        # 그 밖의 코드는 주문이 만들어졌는지 확실하지 않다 → 같은 번호로 조회해 본다
        found = await self._call("GET", "/v1/order", [("client_order_id", ref)])
        fcode = self.code_of(found)
        if fcode == 0 and isinstance(found.get("data"), dict):
            return self._judge(found["data"], receiver)
        if fcode == 112 and ref in str(found.get("msg") or ""):
            raise Rejected(f"catfee 거절 {code}: {body.get('msg')}")
        raise Uncertain(f"catfee 응답 {code}: {body.get('msg')}")

    async def balance(self) -> int:
        try:
            body = await self._call("GET", "/v1/account")
            if self.code_of(body) != 0:
                raise ValueError(body.get("msg"))
            return int(body["data"]["balance"])
        except (Uncertain, KeyError, TypeError, ValueError) as e:
            raise SupplierError(f"catfee 잔고 조회 실패: {e}") from None


# ───────────── trxx (itrx와 같은 서버) ─────────────

class Trxx(Supplier):
    """https://develop.trxx.io — POST만 서명: HMAC-SHA256(secret, f"{초 단위 시각}&{키 정렬·공백 없는 JSON}") hex.

    서명한 JSON 문자열을 그대로 본문으로 보낸다(json= 을 쓰면 공백이 들어가 서명이 깨진다).
    주문: POST /api/v1/frontend/order {energy_amount, period: "1H", receive_address, out_trade_no}
      errno=0이고 serial이 있으면 접수(이미 청구됨). 같은 번호의 중복 방지는 문서에 없다 → 불확실하면 다시 보내지 않는다.
    가격: GET /api/v1/frontend/order/price?energy_amount=65000&period=1H → price(sun/에너지)
    잔고: GET /api/v1/frontend/index-data → balance(sun)
    """

    name = "trxx"
    min_energy = 32_000
    idempotent = False

    def __init__(self, api_key: str, secret: str, base: str = "https://trxx.io") -> None:
        super().__init__(base)
        self.api_key, self.secret = api_key, secret

    def sign(self, ts: str, body: str) -> str:
        return hmac.new(self.secret.encode(), f"{ts}&{body}".encode(), hashlib.sha256).hexdigest()

    def _headers(self) -> dict:
        return {"API-KEY": self.api_key, "Accept-Language": "en"}

    async def _fetch_price(self) -> float:
        try:
            status, body = await self._request("GET", f"{self.base}/api/v1/frontend/order/price",
                                               params={"energy_amount": "65000", "period": "1H"}, headers=self._headers())
            return float(body["price"])
        except (Uncertain, KeyError, TypeError, ValueError) as e:
            raise SupplierError(f"trxx 가격 조회 실패: {e}") from None

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        import json

        body = json.dumps({"energy_amount": energy, "out_trade_no": ref, "period": "1H", "receive_address": receiver},
                          sort_keys=True, separators=(",", ":"))
        ts = str(int(time.time()))
        headers = {**self._headers(), "TIMESTAMP": ts, "SIGNATURE": self.sign(ts, body), "Content-Type": "application/json"}
        status, out = await self._request("POST", f"{self.base}/api/v1/frontend/order", data=body, headers=headers)
        if status == 200 and isinstance(out, dict) and out.get("errno") == 0 and out.get("serial"):
            return Placed(str(out["serial"]), "", int(out.get("amount") or 0))
        msg = str((out or {}).get("message") or (out or {}).get("detail") or out)[:200] if isinstance(out, dict) else str(out)[:200]
        if status in (400, 401, 403, 404, 405) or (status == 200 and isinstance(out, dict) and out.get("errno") not in (0, None)):
            raise Rejected(f"trxx 거절 {status}: {msg}", low_balance="insufficient" in msg.lower() or "余额" in msg)
        raise Uncertain(f"trxx 응답 {status}: {msg}")

    async def balance(self) -> int:
        try:
            status, body = await self._request("GET", f"{self.base}/api/v1/frontend/index-data", headers=self._headers())
            return int(body["balance"])
        except (Uncertain, KeyError, TypeError, ValueError) as e:
            raise SupplierError(f"trxx 잔고 조회 실패: {e}") from None
