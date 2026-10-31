"""지갑 봇 테스트가 같이 쓰는 가짜 서버(TronGrid·빗썸·환율·에너지 공급처)와 도우미."""
import hashlib
import time

from aiohttp import web

from bot import store
from bot.chain import USDT_CONTRACT, USDT_HEX
from bot.suppliers import Placed, Rejected, Supplier, SupplierError, Uncertain
from core.tron import hex_to_address
from testkit.fixtures import cb_update, msg_update

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
U1, U2 = 701, 702


def mk(seed: int) -> tuple[str, str]:
    """체크섬이 맞는 TRON 주소와 그 hex(41 접두어)를 만든다."""
    payload = b"\x41" + hashlib.sha256(f"w{seed}".encode()).digest()[:20]
    raw = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return out, payload.hex()


W1, W1H = mk(1)
W2, W2H = mk(2)
W3, W3H = mk(3)
W4, W4H = mk(4)
SENDER, SENDERH = mk(99)
PAY, PAYH = mk(500)          # 에너지 결제 주소
RCPT, RCPTH = mk(501)        # USDT를 받을 상대 주소


def now_ms(plus=5000) -> int:
    return int(time.time() * 1000) + plus


class FakeTron:
    """TronGrid 흉내: USDT 전송·TRX 거래·계정 조회."""

    def __init__(self):
        self.usdt, self.trx, self.accounts = [], [], {}
        self.calls, self.fail, self.limit = [], False, False
        self.blocks: dict[int, dict] = {}
        self.head = 1000
        self.acct: dict[str, dict] = {}         # 활성화된 계정 {주소: {"balance": sun, "type": ...}}
        self.res: dict[str, dict] = {}          # 계정 자원 {주소: getaccountresource 응답}
        self.usdt_bal: dict[str, int] = {}      # USDT 잔액(컨트랙트 조회용)
        self.sim: dict[str, int] = {}           # 받는 주소별 모의 실행 에너지 (없으면 USDT 보유 여부로 정한다)
        self.sim_fail = False
        self.const_error = False
        self.new_block([], ts=now_ms(0))

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/accounts/{a}/transactions/trc20", self.h_usdt)
        app.router.add_get("/v1/accounts/{a}/transactions", self.h_trx)
        app.router.add_get("/v1/accounts/{a}", self.h_acct)
        app.router.add_post("/wallet/getnowblock", self.h_now)
        app.router.add_post("/wallet/getblockbynum", self.h_block)
        app.router.add_post("/wallet/getaccount", self.h_getaccount)
        app.router.add_post("/wallet/getaccountresource", self.h_resource)
        app.router.add_post("/wallet/triggerconstantcontract", self.h_constant)
        app.router.add_get("/wallet/getchainparameters", self.h_params)

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    def _gate(self, req):
        self.calls.append(req.match_info.get("a", req.path))
        if self.limit:
            return web.Response(status=429)
        if self.fail:
            return web.Response(status=500)

    async def h_usdt(self, req):
        if (r := self._gate(req)):
            return r
        a, since = req.match_info["a"], int(req.query["min_timestamp"])
        assert req.query["only_to"] == "true" and req.query["only_confirmed"] == "true"
        return web.json_response({"success": True, "meta": {}, "data": [
            x for x in self.usdt if x["to"] == a and x["block_timestamp"] >= since]})

    async def h_trx(self, req):
        if (r := self._gate(req)):
            return r
        a, since = req.match_info["a"], int(req.query["min_timestamp"])
        rows = [x for x in self.trx if x["block_timestamp"] >= since and x["_to"] == a]
        return web.json_response({"success": True, "meta": {}, "data": [{k: v for k, v in x.items() if k != "_to"} for x in rows]})

    async def h_now(self, req):
        if (r := self._gate(req)):
            return r
        return web.json_response(self.blocks[self.head])

    async def h_block(self, req):
        if (r := self._gate(req)):
            return r
        return web.json_response(self.blocks.get((await req.json())["num"], {}))

    def new_block(self, txs, ts=None, number=None) -> int:
        """블록을 하나 만든다. 번호를 안 주면 최신 다음 번호."""
        n = number if number is not None else (self.head + 1 if self.blocks else self.head)
        self.blocks[n] = {"block_header": {"raw_data": {"number": n, "timestamp": ts or now_ms(0)}},
                          "transactions": txs}
        self.head = max(self.head, n)
        return n

    @staticmethod
    def tx_usdt(to_hex, micro, txid, sender_hex=SENDERH, selector="a9059cbb", from_hex=None, ret="SUCCESS", contract=USDT_HEX):
        word = lambda h: h[2:].rjust(64, "0")   # noqa: E731
        if selector == "a9059cbb":
            data = selector + word(to_hex) + f"{micro:064x}"
        else:
            data = selector + word(from_hex or sender_hex) + word(to_hex) + f"{micro:064x}"
        return {"txID": txid, "ret": [{"contractRet": ret}], "raw_data": {"contract": [{
            "type": "TriggerSmartContract", "parameter": {"value": {
                "owner_address": sender_hex, "contract_address": contract, "data": data}}}]}}

    @staticmethod
    def tx_trx(to_hex, micro, txid, ret="SUCCESS", kind="TransferContract"):
        return {"txID": txid, "ret": [{"contractRet": ret}], "raw_data": {"contract": [{
            "type": kind, "parameter": {"value": {"amount": micro, "owner_address": SENDERH, "to_address": to_hex}}}]}}

    async def h_acct(self, req):
        if (r := self._gate(req)):
            return r
        acct = self.accounts.get(req.match_info["a"])
        return web.json_response({"success": True, "data": [acct] if acct else []})

    def pay_usdt(self, to, micro, txid, sender=SENDER, ts=None, contract=USDT_CONTRACT):
        self.usdt.append({"transaction_id": txid, "from": sender, "to": to, "value": str(micro), "type": "Transfer",
                          "token_info": {"address": contract}, "block_timestamp": ts or now_ms()})

    def pay_trx(self, to, to_hex, micro, txid, kind="TransferContract", ret="SUCCESS", ts=None):
        self.trx.append({"_to": to, "txID": txid, "block_timestamp": ts or now_ms(), "ret": [{"contractRet": ret}],
                         "raw_data": {"contract": [{"type": kind, "parameter": {"value": {
                             "amount": micro, "owner_address": SENDERH, "to_address": to_hex}}}]}})

    # ---- 계정·자원·모의 실행 ----

    def activate(self, addr, trx_micro=5_000_000, energy=0, free_bw=600, contract=False):
        self.acct[addr] = {"address": addr, "balance": trx_micro, **({"type": "Contract"} if contract else {})}
        self.res[addr] = {"freeNetLimit": 600, "freeNetUsed": 600 - free_bw, "EnergyLimit": energy, "EnergyUsed": 0}

    def delegate(self, addr, energy):
        self.res.setdefault(addr, {})["EnergyLimit"] = self.res.get(addr, {}).get("EnergyLimit", 0) + energy

    async def h_getaccount(self, req):
        if (r := self._gate(req)):
            return r
        return web.json_response(self.acct.get((await req.json())["address"], {}))

    async def h_resource(self, req):
        if (r := self._gate(req)):
            return r
        a = (await req.json())["address"]
        return web.json_response(self.res.get(a, {}) if a in self.acct else {})

    async def h_constant(self, req):
        if (r := self._gate(req)):
            return r
        body = await req.json()
        owner, sel, param = body["owner_address"], body["function_selector"], body["parameter"]
        target = hex_to_address("41" + param[24:64])
        if sel == "balanceOf(address)" and self.const_error:
            return web.json_response({"result": {"code": "OTHER_ERROR", "message": "CPU timeout"}})
        if sel == "balanceOf(address)":
            return web.json_response({"result": {"result": True}, "energy_used": 900,
                                      "constant_result": [f"{self.usdt_bal.get(target, 0):064x}"]})
        assert sel == "transfer(address,uint256)"
        if self.sim_fail:
            return web.json_response({"result": {"code": "OTHER_ERROR", "message": "fail"}})
        if self.usdt_bal.get(owner, 0) <= 0:                        # 보내는 쪽에 USDT가 없으면 되돌려진다
            return web.json_response({"result": {"result": True, "message": "REVERT opcode executed"},
                                      "energy_used": 8624, "transaction": {"ret": [{"ret": "FAILED"}]}})
        energy = self.sim.get(target, 64_285 if self.usdt_bal.get(target, 0) > 0 else 130_285)
        return web.json_response({"result": {"result": True}, "energy_used": energy, "constant_result": ["0" * 64],
                                  "transaction": {"ret": [{}]}})

    async def h_params(self, req):
        if (r := self._gate(req)):
            return r
        return web.json_response({"chainParameter": [{"key": "getTransactionFee", "value": 1000},
                                                     {"key": "getEnergyFee", "value": 100}]})

    def balance(self, addr, usdt_micro=0, trx_micro=0):
        self.accounts[addr] = {"balance": trx_micro, "trc20": [{USDT_CONTRACT: str(usdt_micro)}] if usdt_micro else []}


class FakeMarket:
    """빗썸과 환율 API 흉내."""

    def __init__(self):
        self.usdt, self.trx, self.fx = 1428.0, 420.0, 1400.0
        self.bithumb_down = self.fx_down = False
        self.calls = {"bithumb": 0, "fx": 0}

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/ticker", self.h_ticker)
        app.router.add_get("/v6/latest/USD", self.h_fx)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    async def h_ticker(self, req):
        self.calls["bithumb"] += 1
        assert req.query["markets"] == "KRW-USDT,KRW-TRX"
        if self.bithumb_down:
            return web.Response(status=503)
        return web.json_response([{"market": "KRW-USDT", "trade_price": self.usdt},
                                  {"market": "KRW-TRX", "trade_price": self.trx}])

    async def h_fx(self, req):
        self.calls["fx"] += 1
        if self.fx_down:
            return web.Response(status=500)
        return web.json_response({"result": "success", "rates": {"KRW": self.fx}})



async def say(e, uid, text):
    await e.dp.feed_update(e.bot, msg_update(uid, text))


async def press(e, uid, data):
    await e.dp.feed_update(e.bot, cb_update(uid, data))


def last(e, uid):
    return e.tg.texts_to(uid)[-1]


async def start(e, uid=U1):
    await say(e, uid, "/start")


async def register(e, uid=U1, addr=W1, label="내 지갑"):
    await start(e, uid)
    await press(e, uid, "wa")
    await say(e, uid, addr)
    await say(e, uid, label) if label else await press(e, uid, "wskip")


async def wid(e, uid=U1, addr=W1) -> int:
    return next(w["id"] for w in await store.wallets_of(e.db, uid) if w["address"] == addr)




# ───────────── 에너지 공급처 (HTTP 없이 흉내) ─────────────

class FakeSupplier(Supplier):
    """mode: ok · reject · low · uncertain · price_fail. ok면 TronGrid 흉내에 에너지를 위임한 것처럼 반영한다."""

    def __init__(self, name, price, tron=None, mode="ok", deliver=True):
        super().__init__("http://unused")
        self.name, self.price, self.tron, self.mode, self.deliver = name, price, tron, mode, deliver
        self.calls: list[tuple[str, int, str]] = []
        self.placed: dict[str, Placed] = {}
        self.bal = 100 * 1_000_000
        self.price_ttl = 0

    async def _fetch_cost(self, energy):
        if self.mode == "price_fail":
            raise SupplierError("가격 없음")
        if self.mode == "price_crash":
            raise AttributeError("이상한 가격 응답")
        return int(self.price * energy)

    async def order(self, receiver, energy, ref):
        self.calls.append((receiver, energy, ref))
        mode = self.mode if isinstance(self.mode, str) else self.mode.pop(0)
        if mode in ("reject", "price_fail"):
            raise Rejected(f"{self.name} 거절")
        if mode == "low":
            raise Rejected(f"{self.name} 잔고 부족", low_balance=True)
        if mode == "uncertain":
            raise Uncertain(f"{self.name} 시간 초과")
        if mode == "crash":                         # 청구된 뒤 응답을 읽다가 예상하지 못한 오류
            raise ValueError("이상한 응답")
        if ref in self.placed:                      # 같은 번호는 새 주문이 아니다 (공급처의 중복 방지 흉내)
            return self.placed[ref]
        p = Placed(f"{self.name}-{ref}", f"dtx-{ref}", int(self.price * energy))
        self.placed[ref] = p
        if self.deliver and self.tron:
            self.tron.delegate(receiver, energy)
        return p

    async def _fetch_balance(self):
        return self.bal
