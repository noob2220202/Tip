"""공급처 클라이언트: 실제 API 문서의 요청 형식·서명·응답 처리를 가짜 서버로 확인한다."""
import base64
import hashlib
import hmac
import json

import pytest
from aiohttp import web

from bot.suppliers import CatFee, Netts, Rejected, SupplierError, Trxx, Uncertain
from support import W1, W2


async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


# ───────────── Netts ─────────────

class NettsFake:
    def __init__(self):
        self.mode, self.keys, self.bodies, self.price = "ok", {}, [], 27

    def app(self):
        a = web.Application()
        a.router.add_get("/apiv2/pricing", self.pricing)
        a.router.add_post("/apiv2/order1h", self.order)
        a.router.add_get("/apiv2/userinfo", self.userinfo)
        return a

    def auth(self, req):
        return req.headers.get("X-API-KEY") == "nk" and req.headers.get("X-Real-IP") == "1.2.3.4"

    async def pricing(self, req):
        assert self.auth(req) and req.query["services"] == "energy_1h"
        return web.json_response({"success": True, "data": {"services": {"energy_1h": {"unit": "sun", "periods": [
            {"id": "night", "is_current": False, "price": 20, "tiers": None},
            {"id": "daytime", "is_current": True, "price": self.price, "tiers": None}]}}}})

    async def order(self, req):
        if not self.auth(req):
            return web.json_response({"detail": "Invalid API key or IP not in whitelist"}, status=401)
        body = await req.json()
        self.bodies.append(body)
        assert set(body) == {"amount", "receiveAddress"}                    # 다른 필드는 거절된다(문서)
        key = req.headers["X-Idempotency-Key"]
        assert len(key) == 64 and key == key.lower() and all(c in "0123456789abcdef" for c in key)
        if self.mode == "low":
            return web.json_response({"code": 1004, "msg": "Insufficient funds. Required: 1.75 TRX"}, status=403)
        if self.mode == "busy":
            return web.json_response({"success": False, "error": "duplicate_request_processing"}, status=409)
        if self.mode == "err":
            return web.json_response({"code": 5000, "msg": "Internal server error occurred"}, status=500)
        if self.mode == "down":
            return web.json_response({"code": 5003, "msg": "All energy providers are currently unavailable."}, status=503)
        if self.mode == "garbage":
            return web.Response(text="<html>bad gateway</html>", status=502)
        data = {"orderId": "1H42", "paidTRX": 1.755, "hash": "dh1",
                "delegateAddress": W2 if self.mode == "other" else body["receiveAddress"], "energy": body["amount"] + 50}
        if key in self.keys:
            return web.json_response({"detail": {"code": 10000, "msg": "ok", "data": self.keys[key]},
                                      "idempotency": {"cached": True}}, status=208)
        self.keys[key] = data
        return web.json_response({"detail": {"code": 10000, "msg": "Successful, 1.755 TRX deducted", "data": data}})

    async def userinfo(self, req):
        assert self.auth(req)
        return web.json_response({"status": "success", "stats": {"balance": 123.5}})


@pytest.fixture
async def netts():
    fake = NettsFake()
    runner, base = await serve(fake.app())
    client = Netts("nk", "1.2.3.4", base + "/apiv2")
    yield fake, client
    await client.close()
    await runner.cleanup()


async def test_netts_price_order_and_idempotency(netts):
    fake, c = netts
    assert await c.unit_price() == 27 and await c.cost(65_000) == 1_755_000
    p = await c.order(W1, 65_000, "e7")
    assert (p.order_id, p.txid, p.cost) == ("1H42", "dh1", 1_755_000)
    assert fake.bodies[-1] == {"amount": 65_000, "receiveAddress": W1}
    again = await c.order(W1, 65_000, "e7")                                 # 같은 주문 번호 → 208, 새 주문 아님
    assert again.order_id == "1H42" and len(fake.keys) == 1
    await c.order(W1, 65_000, "e8")
    assert len(fake.keys) == 2
    assert await c.balance() == 123_500_000


@pytest.mark.parametrize("mode,exc,low", [("low", Rejected, True), ("down", Rejected, False), ("busy", Uncertain, False),
                                          ("err", Uncertain, False), ("garbage", Uncertain, False),
                                          ("other", Uncertain, False)])
async def test_netts_errors(netts, mode, exc, low):
    fake, c = netts
    fake.mode = mode
    with pytest.raises(exc) as e:
        await c.order(W1, 65_000, "e1")
    if exc is Rejected:
        assert e.value.low_balance is low


async def test_netts_auth_failure_is_rejected():
    fake = NettsFake()
    runner, base = await serve(fake.app())
    c = Netts("wrong", "1.2.3.4", base + "/apiv2")
    with pytest.raises(Rejected):
        await c.order(W1, 65_000, "e1")
    await c.close()
    await runner.cleanup()


async def test_network_failure_is_uncertain_and_price_failure_is_supplier_error():
    c = Netts("nk", "1.2.3.4", "http://127.0.0.1:9/apiv2")
    with pytest.raises(Uncertain):
        await c.order(W1, 65_000, "e1")
    with pytest.raises(SupplierError):
        await c.unit_price()
    await c.close()


# ───────────── CatFee ─────────────

class CatFeeFake:
    def __init__(self):
        self.mode, self.orders, self.paths, self.lookup = "ok", {}, [], "found"

    def app(self):
        a = web.Application()
        a.router.add_get("/v1/estimate", self.estimate)
        a.router.add_post("/v1/order", self.order)
        a.router.add_get("/v1/order", self.query)
        a.router.add_get("/v1/account", self.account)
        return a

    def signed(self, req) -> bool:
        ts = req.headers["CF-ACCESS-TIMESTAMP"]
        want = base64.b64encode(hmac.new(b"cs", (ts + req.method + req.raw_path).encode(), hashlib.sha256).digest()).decode()
        return req.headers.get("CF-ACCESS-KEY") == "ck" and req.headers.get("CF-ACCESS-SIGN") == want and ts.endswith("Z")

    async def estimate(self, req):
        if not self.signed(req):
            return web.json_response({"code": 101, "msg": "bad sign"})
        return web.json_response({"code": 0, "data": int(req.query["quantity"]) * 18})

    def payload(self, q, status="PAYMENT_SUCCESS"):
        return {"id": "cf1", "client_order_id": q["client_order_id"], "receiver": q["receiver"], "status": status,
                "confirm_status": "UNCONFIRMED", "pay_amount_sun": int(q["quantity"]) * 18, "delegate_hash": ""}

    async def order(self, req):
        self.paths.append(req.raw_path)
        if not self.signed(req):
            return web.json_response({"code": 101, "msg": "bad sign"})
        q = req.query
        if self.mode == "low":
            return web.json_response({"code": 201, "msg": "Insufficient balance", "data": None})
        if self.mode == "param":
            return web.json_response({"code": 1, "msg": "Invalid parameter"})
        if self.mode == "status_low":
            return web.json_response({"code": 0, "data": self.payload(q, "INSUFFICIENT_BALANCE")})
        if self.mode == "unsure":
            if self.lookup == "found":
                self.orders[q["client_order_id"]] = self.payload(q)
            return web.json_response({"code": 114, "msg": "cannot confirm"})
        if q["client_order_id"] in self.orders:
            return web.json_response({"code": 0, "sub_code": "SUCCESS", "sub_msg": "Duplicate order request",
                                      "data": self.orders[q["client_order_id"]]})
        self.orders[q["client_order_id"]] = self.payload(q)
        return web.json_response({"code": 0, "data": self.orders[q["client_order_id"]]})

    async def query(self, req):
        assert self.signed(req)
        ref = req.query["client_order_id"]
        if ref in self.orders:
            return web.json_response({"code": 0, "data": self.orders[ref]})
        if self.lookup == "missing":
            return web.json_response({"code": 112, "msg": f"order not found: {ref}"})
        return web.json_response({"code": 111, "msg": "service error"})

    async def account(self, req):
        assert self.signed(req)
        return web.json_response({"code": 0, "data": {"balance": 55_000_000}})


@pytest.fixture
async def catfee():
    fake = CatFeeFake()
    runner, base = await serve(fake.app())
    client = CatFee("ck", "cs", base)
    yield fake, client
    await client.close()
    await runner.cleanup()


async def test_catfee_signature_query_order_and_idempotency(catfee):
    fake, c = catfee
    assert await c.unit_price() == 18
    p = await c.order(W1, 65_000, "e5")
    assert (p.order_id, p.cost) == ("cf1", 1_170_000)
    assert fake.paths[-1] == f"/v1/order?quantity=65000&receiver={W1}&duration=1h&client_order_id=e5&activate=false"
    await c.order(W1, 65_000, "e5")
    assert len(fake.orders) == 1
    assert await c.balance() == 55_000_000


async def test_catfee_doc_signature_vector():
    c = CatFee("k", "demo_api_secret")
    assert c.sign("2023-08-26T12:34:56.789Z", "POST", "/v1/order?quantity=65000&receiver=TRON_ADDRESS&duration=1h") \
        == "FGXIbbin87a4asGCPj87JnyM6clEKUkgbapm5NKpZ4k="


@pytest.mark.parametrize("mode,lookup,exc,low", [
    ("low", "found", Rejected, True), ("param", "found", Rejected, False), ("status_low", "found", Rejected, True),
    ("unsure", "missing", Rejected, False), ("unsure", "error", Uncertain, False)])
async def test_catfee_errors(catfee, mode, lookup, exc, low):
    fake, c = catfee
    fake.mode, fake.lookup = mode, lookup
    with pytest.raises(exc) as e:
        await c.order(W1, 65_000, "e9")
    if exc is Rejected:
        assert e.value.low_balance is low


async def test_catfee_unsure_but_order_exists_is_success(catfee):
    fake, c = catfee
    fake.mode = "unsure"
    p = await c.order(W1, 65_000, "e3")
    assert p.order_id == "cf1"


# ───────────── trxx ─────────────

class TrxxFake:
    def __init__(self):
        self.mode, self.bodies = "ok", []

    def app(self):
        a = web.Application()
        a.router.add_get("/api/v1/frontend/order/price", self.price)
        a.router.add_post("/api/v1/frontend/order", self.order)
        a.router.add_get("/api/v1/frontend/index-data", self.index)
        return a

    async def price(self, req):
        assert req.query["period"] == "1H"
        return web.json_response({"energy_amount": req.query["energy_amount"], "period": "1H", "price": 15.9,
                                  "total_price": int(int(req.query["energy_amount"]) * 15.9)})

    async def order(self, req):
        raw = await req.text()
        self.bodies.append(raw)
        want = hmac.new(b"ts", f"{req.headers['TIMESTAMP']}&{raw}".encode(), hashlib.sha256).hexdigest()
        if req.headers.get("API-KEY") != "tk" or req.headers.get("SIGNATURE") != want:
            return web.json_response({"detail": "API not found"}, status=403)
        if self.mode == "low":
            return web.json_response({"errno": 1, "message": "Insufficient funds"})
        if self.mode == "bad":
            return web.json_response({"energy_amount": ["energy_amount must be >= 10000"]}, status=400)
        if self.mode == "err":
            return web.Response(text="oops", status=502)
        return web.json_response({"errno": 0, "serial": "sx1", "amount": 1_033_500, "balance": 9_000_000})

    async def index(self, req):
        return web.json_response({"balance": 77_000_000} if req.headers.get("API-KEY") == "tk" else {})


@pytest.fixture
async def trxx():
    fake = TrxxFake()
    runner, base = await serve(fake.app())
    client = Trxx("tk", "ts", base)
    yield fake, client
    await client.close()
    await runner.cleanup()


async def test_trxx_signed_compact_body_and_order(trxx):
    fake, c = trxx
    assert await c.unit_price() == 15.9 and await c.cost(65_000) == 1_033_500
    p = await c.order(W1, 65_000, "e2")
    assert (p.order_id, p.cost) == ("sx1", 1_033_500)
    assert fake.bodies[-1] == json.dumps({"energy_amount": 65_000, "out_trade_no": "e2", "period": "1H",
                                          "receive_address": W1}, sort_keys=True, separators=(",", ":"))
    assert await c.balance() == 77_000_000 and c.idempotent is False


def test_trxx_doc_signature_vector():
    c = Trxx("k", "0285A2741D0E76E2E187260EB23E51851D48403A756333E7D0CF845406ABF3E8")
    body = ('{"callback_url":"http://{mydomain}/callback","energy_amount":32000,"out_trade_no":"123456",'
            '"period":"1D","receive_address":"TExWKszFWYTKZH8LYiovAPKzS3L9MLZ4kw"}')
    assert c.sign("1686796826", body) == "07c125cabfe006614a02131f927cca9e7a654f7644a817b155fad591e1c50f7b"


@pytest.mark.parametrize("mode,exc,low", [("low", Rejected, True), ("bad", Rejected, False), ("err", Uncertain, False)])
async def test_trxx_errors(trxx, mode, exc, low):
    fake, c = trxx
    fake.mode = mode
    with pytest.raises(exc) as e:
        await c.order(W1, 65_000, "e1")
    if exc is Rejected:
        assert e.value.low_balance is low
