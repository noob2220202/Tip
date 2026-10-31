"""에너지 공급처 API 클라이언트. 1시간짜리 에너지를 받는 주소로 위임해 준다.

공급처마다 응답 형식은 달라도 결과는 세 가지로 나눈다.
  성공(Placed) · 거절(Rejected: 주문이 만들어지지 않았다, 다른 공급처로 넘어가도 된다)
  불확실(Uncertain: 주문이 됐는지 알 수 없다. 다른 공급처로 넘기면 두 번 살 수 있다)
'거절'은 청구가 없었다고 확신할 수 있을 때만 쓴다. 성공 응답을 읽다가 형식이 이상하면 이미 청구됐을 수 있으므로 '불확실'이다.
불확실하면 같은 번호(ref)의 두 번째 요청을 새 주문으로 만들지 않는 공급처(idempotent)에만 다시 보낸다.
그렇지 않은 공급처는 다시 보내지 않고 체인에서 에너지가 들어왔는지로 판단한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
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
        self._costs: dict[int, tuple[float, int]] = {}      # 에너지 양 -> (조회 시각, 원가 sun)
        self._lock = asyncio.Lock()

    def _http(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def cost(self, energy: int, now: float | None = None) -> int:
        """energy만큼 1시간 빌리는 원가(sun). 양마다 짧게 기억한다. 실패하면 SupplierError."""
        now = now or time.time()
        async with self._lock:
            hit = self._costs.get(energy)
            if hit and now - hit[0] < self.price_ttl:
                return hit[1]
            try:
                total = await self._fetch_cost(energy)
            except SupplierError:
                raise
            except Exception as e:  # noqa: BLE001 - 가격 조회는 부작용이 없으니 어떤 오류든 '가격 없음'으로 본다
                raise SupplierError(f"{self.name} 가격 조회 실패: {e!r}") from None
            if total <= 0:
                raise SupplierError(f"{self.name} 가격이 올바르지 않아요: {total}")
            self._costs[energy] = (now, total)
            return total

    async def _fetch_cost(self, energy: int) -> int:
        raise NotImplementedError

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        raise NotImplementedError

    async def balance(self) -> int:
        """남은 잔고(sun). 실패하면 SupplierError."""
        try:
            return await self._fetch_balance()
        except SupplierError:
            raise
        except Exception as e:  # noqa: BLE001 - 잔고 조회도 부작용이 없다
            raise SupplierError(f"{self.name} 잔고 조회 실패: {e!r}") from None

    async def _fetch_balance(self) -> int:
        raise NotImplementedError

    async def _request(self, method: str, url, **kw) -> tuple[int, object]:
        """(HTTP 코드, JSON). 네트워크 오류·시간 초과는 Uncertain, JSON이 아닌 응답은 body=None."""
        try:
            async with self._http().request(method, url, **kw) as r:
                try:
                    body = await r.json(content_type=None)
                except ValueError:
                    body = None
                return r.status, body
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise Uncertain(f"{self.name} 요청 실패: {e!r}") from e


def total_sun(unit: float, energy: int) -> int:
    """sun/에너지 × 에너지를 올림해서 sun 총액으로."""
    return math.ceil(Decimal(str(unit)) * energy)


# ───────────── Netts ─────────────

class Netts(Supplier):
    """https://doc.netts.io — X-API-KEY + 화이트리스트 IP(X-Real-IP). 서명 없음.

    주문: POST /order1h {amount, receiveAddress}. X-Idempotency-Key(64자 hex)를 주문마다 고정하면
    같은 키의 재요청은 208(이미 처리됨, 추가 청구 없음)이나 409(처리 중)를 준다.
    응답의 delegateAddress는 에너지를 보낸 풀 주소라서 받는 주소와 다른 것이 정상이다.
    가격: GET /pricing?services=energy_1h (분당 1회 제한 → 응답 전체를 길게 기억하고 양마다 구간 가격을 고른다).
    """

    name = "netts"
    min_energy = 61_000
    price_ttl = 90

    def __init__(self, api_key: str, real_ip: str, base: str = "https://netts.io/apiv2") -> None:
        super().__init__(base)
        self.api_key, self.real_ip = api_key, real_ip
        self._period: tuple[float, dict] | None = None

    def _headers(self, key: str | None = None) -> dict:
        h = {"X-API-KEY": self.api_key, "X-Real-IP": self.real_ip}
        if key:
            h["X-Idempotency-Key"] = key
        return h

    def idem_key(self, receiver: str, energy: int, ref: str) -> str:
        return hmac.new(self.api_key.encode(), f"{receiver}:{energy}:{ref}".encode(), hashlib.sha256).hexdigest()

    async def _current_period(self) -> dict:
        now = time.time()
        if self._period and now - self._period[0] < self.price_ttl:
            return self._period[1]
        try:
            status, body = await self._request("GET", f"{self.base}/pricing", params={"services": "energy_1h"},
                                               headers=self._headers())
        except Uncertain as e:
            raise SupplierError(str(e)) from e
        try:
            periods = body["data"]["services"]["energy_1h"]["periods"]
            period = next(p for p in periods if isinstance(p, dict) and p.get("is_current"))
            float(period["price"])
        except Exception:  # noqa: BLE001 - 형식이 다르면 어떤 오류든 가격 없음
            raise SupplierError(f"netts 가격 응답을 읽지 못했어요 ({status})") from None
        self._period = (now, period)
        return period

    @staticmethod
    def price_for(period: dict, energy: int) -> float:
        """구간 가격이 있으면 이 양이 들어가는 구간의 값, 없으면 기본 값(sun/에너지)."""
        for t in period.get("tiers") or []:
            if not isinstance(t, dict) or t.get("price") is None:
                continue
            hi = t.get("max_energy")
            if (t.get("min_energy") or 0) <= energy <= (hi if hi is not None else 10**12):
                return float(t["price"])
        return float(period["price"])

    async def _fetch_cost(self, energy: int) -> int:
        return total_sun(self.price_for(await self._current_period(), energy), energy)

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        status, body = await self._request("POST", f"{self.base}/order1h",
                                           json={"amount": energy, "receiveAddress": receiver},
                                           headers=self._headers(self.idem_key(receiver, energy, ref)))
        detail = body.get("detail") if isinstance(body, dict) else None
        if status in (200, 208) and isinstance(detail, dict) and detail.get("code") == 10000:
            try:
                data = detail.get("data") or {}
                got = int(data.get("energy") or 0)
                placed = Placed(str(data.get("orderId") or ""), str(data.get("hash") or ""),
                                trx_to_sun(data.get("paidTRX") or 0))
            except Exception as e:  # noqa: BLE001 - 청구된 뒤라서 형식이 이상하면 불확실
                raise Uncertain(f"netts 성공 응답을 읽지 못했어요: {e!r}") from None
            if got and got < energy:          # 같은 키의 다른 주문 응답이 돌아온 경우
                raise Uncertain(f"netts 응답 에너지 {got}이 주문 {energy}보다 적어요")
            return placed
        if status == 409:
            raise Uncertain("netts: 같은 주문을 처리하는 중")
        code = (body.get("code") if isinstance(body, dict) else None) or (detail.get("code") if isinstance(detail, dict) else None)
        msg = str((body.get("msg") if isinstance(body, dict) else None) or detail or body)[:200]
        if status == 403 and code == 1004:
            raise Rejected(f"netts 잔고 부족: {msg}", low_balance=True)
        if status in (400, 401, 422, 429) or (status == 503 and code in (5001, 5002, 5003, 5004)):
            raise Rejected(f"netts 거절 {status}: {msg}")
        raise Uncertain(f"netts 응답 {status}: {msg}")

    async def _fetch_balance(self) -> int:
        status, body = await self._request("GET", f"{self.base}/userinfo", headers=self._headers())
        return trx_to_sun(body["stats"]["balance"])


# ───────────── CatFee ─────────────

class CatFee(Supplier):
    """https://docs.catfee.io — CF-ACCESS-KEY / CF-ACCESS-SIGN / CF-ACCESS-TIMESTAMP.

    서명 = Base64(HMAC-SHA256(secret, 시각 + 메서드 + 경로?쿼리)). 본문은 없고 값은 모두 쿼리에 넣는다.
    서명한 문자열과 실제 주소가 한 글자라도 다르면 안 되므로 쿼리를 직접 만들어 그대로 보낸다.
    주문: POST /v1/order?quantity&receiver&duration=1h&client_order_id&activate=false
      같은 client_order_id는 새 주문을 만들지 않고 처음 주문을 돌려준다.
      HTTP 200이어도 code가 0이 아니거나, code=0이어도 status가 실패 값이면 실패다.
      114·111·113·203·999처럼 '결과를 아직 모른다'는 코드는 바로 조회해서 없더라도 불확실로 본다(문서: 곧바로 다시 사지 말 것).
    """

    name = "catfee"
    min_energy = 65_000
    OK_STATUS = ("PAYMENT_SUCCESS", "DELEGATE_SUCCESS")
    PRE_CHECK = {1, 100, 101, 106, 107, 108}         # 요청을 검사하는 단계에서 막힌 것(서명·형식·한도) → 조회 없이 거절
    NOT_PROCESSED = {110, 202, 1003, 1004, 1005, 1006}   # 상태·조건이 안 맞아 처리하지 않은 것 → 조회해서 없으면 거절

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

    async def _fetch_cost(self, energy: int) -> int:
        try:
            body = await self._call("GET", "/v1/estimate", [("quantity", str(energy)), ("duration", "1h")])
        except Uncertain as e:
            raise SupplierError(str(e)) from None
        if self.code_of(body) != 0:
            raise SupplierError(f"catfee 가격 조회 실패: {body.get('msg')}")
        total = float(body["data"])
        return total_sun(total, energy) if total < 10_000 else math.ceil(total)   # 1당 가격으로 오면 총액으로 바꾼다

    def _placed(self, data: dict, receiver: str) -> Placed:
        try:
            if data.get("receiver") not in (None, "", receiver):
                raise Uncertain(f"catfee가 다른 주소로 처리했다고 응답했어요: {data.get('receiver')}")
            return Placed(str(data.get("id") or ""), str(data.get("delegate_hash") or ""),
                          int(Decimal(str(data.get("pay_amount_sun") or 0))))
        except Uncertain:
            raise
        except Exception as e:  # noqa: BLE001
            raise Uncertain(f"catfee 성공 응답을 읽지 못했어요: {e!r}") from None

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
        if code in self.NOT_PROCESSED and fcode == 112 and ref in str(found.get("msg") or ""):
            raise Rejected(f"catfee 거절 {code}: {body.get('msg')}")
        raise Uncertain(f"catfee 응답 {code}: {body.get('msg')}")

    async def _fetch_balance(self) -> int:
        body = await self._call("GET", "/v1/account")
        if self.code_of(body) != 0:
            raise SupplierError(f"catfee 잔고 조회 실패: {body.get('msg')}")
        return int(Decimal(str(body["data"]["balance"])))


# ───────────── trxx (itrx와 같은 서버) ─────────────

class Trxx(Supplier):
    """https://develop.trxx.io — POST만 서명: HMAC-SHA256(secret, f"{초 단위 시각}&{키 정렬·공백 없는 JSON}") hex.

    서명한 JSON 문자열을 그대로 본문으로 보낸다(json= 을 쓰면 공백이 들어가 서명이 깨진다).
    주문: POST /api/v1/frontend/order {energy_amount, period: "1H", receive_address, out_trade_no}
      errno=0이고 serial이 있으면 접수(이미 청구됨). 같은 번호의 중복 방지는 문서에 없다 → 불확실하면 다시 보내지 않는다.
    가격: GET /api/v1/frontend/order/price?energy_amount=N&period=1H → total_price(sun, 소액 수수료 포함)
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

    async def _fetch_cost(self, energy: int) -> int:
        try:
            status, body = await self._request("GET", f"{self.base}/api/v1/frontend/order/price",
                                               params={"energy_amount": str(energy), "period": "1H"},
                                               headers=self._headers())
        except Uncertain as e:
            raise SupplierError(str(e)) from None
        by_unit = total_sun(body["price"], energy) + int(body.get("addition") or 0)
        return max(int(body.get("total_price") or 0), by_unit)     # 소액 수수료(addition)가 빠지지 않게

    async def order(self, receiver: str, energy: int, ref: str) -> Placed:
        body = json.dumps({"energy_amount": energy, "out_trade_no": ref, "period": "1H", "receive_address": receiver},
                          sort_keys=True, separators=(",", ":"))
        ts = str(int(time.time()))
        headers = {**self._headers(), "TIMESTAMP": ts, "SIGNATURE": self.sign(ts, body), "Content-Type": "application/json"}
        status, out = await self._request("POST", f"{self.base}/api/v1/frontend/order", data=body, headers=headers)
        if status == 200 and isinstance(out, dict) and out.get("errno") == 0 and out.get("serial"):
            try:
                return Placed(str(out["serial"]), "", int(Decimal(str(out.get("amount") or 0))))
            except Exception as e:  # noqa: BLE001 - 접수된 뒤라서 형식이 이상하면 불확실
                raise Uncertain(f"trxx 성공 응답을 읽지 못했어요: {e!r}") from None
        msg = str(out.get("message") or out.get("detail") or out)[:200] if isinstance(out, dict) else str(out)[:200]
        if status in (400, 401, 403, 404, 405) or (status == 200 and isinstance(out, dict) and out.get("errno") not in (0, None)):
            raise Rejected(f"trxx 거절 {status}: {msg}", low_balance="insufficient" in msg.lower() or "余额" in msg)
        raise Uncertain(f"trxx 응답 {status}: {msg}")

    async def _fetch_balance(self) -> int:
        status, body = await self._request("GET", f"{self.base}/api/v1/frontend/index-data", headers=self._headers())
        return int(body["balance"])
