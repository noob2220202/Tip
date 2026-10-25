import hashlib
import json
import time
from datetime import datetime

import pytest
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from bot import chain, money, store
from bot.chain import USDT_CONTRACT, USDT_HEX, Block, TronReader
from bot.scanner import Scanner
from bot.handlers import router
from bot.notify import Notifier
from bot.prices import Prices
from bot.store import SCHEMA
from bot.texts import KST, TEXTS
from bot.watcher import Watcher
from core.bot_runner import assemble
from core.db import open_db
from core.texts import Texts
from core.tron import address_to_hex, hex_to_address, is_tron_address
from testkit.fixtures import ADMIN, cb_update, msg_update

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


def now_ms(plus=5000) -> int:
    return int(time.time() * 1000) + plus


class FakeTron:
    """TronGrid 흉내: USDT 전송·TRX 거래·계정 조회."""

    def __init__(self):
        self.usdt, self.trx, self.accounts = [], [], {}
        self.calls, self.fail, self.limit = [], False, False
        self.blocks: dict[int, dict] = {}
        self.head = 1000
        self.solid: set[str] = set()      # 확정된 거래
        self.known: set[str] = set()      # 노드가 알고 있는 거래(블록이 뒤집히면 여기서 빠진다)
        self.new_block([], ts=now_ms(0))

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/accounts/{a}/transactions/trc20", self.h_usdt)
        app.router.add_get("/v1/accounts/{a}/transactions", self.h_trx)
        app.router.add_get("/v1/accounts/{a}", self.h_acct)
        app.router.add_post("/wallet/getnowblock", self.h_now)
        app.router.add_post("/wallet/getblockbynum", self.h_block)
        app.router.add_post("/walletsolidity/gettransactioninfobyid", self.h_info)
        app.router.add_post("/wallet/gettransactionbyid", self.h_tx)
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

    async def h_info(self, req):
        if (r := self._gate(req)):
            return r
        txid = (await req.json())["value"]
        return web.json_response({"id": txid, "blockNumber": 1} if txid in self.solid else {})

    async def h_tx(self, req):
        if (r := self._gate(req)):
            return r
        txid = (await req.json())["value"]
        return web.json_response({"txID": txid} if txid in self.known else {})

    def new_block(self, txs, ts=None, number=None) -> int:
        """블록을 하나 만든다. 번호를 안 주면 최신 다음 번호."""
        n = number if number is not None else (self.head + 1 if self.blocks else self.head)
        self.blocks[n] = {"block_header": {"raw_data": {"number": n, "timestamp": ts or now_ms(0)}},
                          "transactions": txs}
        self.head = max(self.head, n)
        for t in txs:
            self.known.add(t["txID"])
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


@pytest.fixture
async def env(make_cfg, bot, tg):
    tron, market = FakeTron(), FakeMarket()
    await tron.start()
    await market.start()
    cfg = make_cfg()
    db = await open_db(cfg.db_path, SCHEMA)
    reader, prices = TronReader(tron.base, ""), Prices(market.base, market.base)
    notifier = Notifier(bot, db, cfg.admin_ids)
    watcher = Watcher(db, reader, prices, notifier, reconcile_seconds=1800)
    scanner = Scanner(db, reader, watcher)
    router._parent_router = None
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS),
                           {"chain": reader, "prices": prices, "notifier": notifier, "watcher": watcher,
                            "max_wallets_total": 30, "scanner": scanner})

    class E:  # noqa: N801
        pass

    e = E()
    e.tron, e.market, e.db, e.dp, e.bot, e.tg, e.watcher, e.chain, e.prices, e.notifier, e.cfg = (
        tron, market, db, dp, bot, tg, watcher, reader, prices, notifier, cfg)
    e.scanner = scanner
    yield e
    await reader.close(); await prices.close()
    await tron.runner.cleanup(); await market.runner.cleanup()
    await db.close()


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


# ───────────── 순수 함수 ─────────────

def test_money_parse_fmt_won():
    assert money.parse("0.5") == 500_000 and money.parse("1,000") == 1_000_000_000 and money.parse("0") == 0
    assert money.parse("-1") is None and money.parse("abc") is None and money.parse("0.1234567") is None
    assert money.parse("nan") is None and money.parse("inf") is None
    assert money.fmt(12_500_000) == "12.50" and money.fmt(123_456) == "0.123456" and money.fmt(1_234_567_000) == "1,234.567"
    assert money.won(2_500_000, 1428.0) == 3570 and money.won(1, 1428.0) == 0


def test_tron_helpers_and_address_roundtrip():
    assert is_tron_address(W1) and hex_to_address(W1H) == W1 and hex_to_address(W1H[2:]) == W1


def test_parsers_only_accept_real_incoming_deposits():
    good = {"transaction_id": "t", "from": SENDER, "to": W1, "value": "5", "type": "Transfer",
            "token_info": {"address": USDT_CONTRACT}, "block_timestamp": 1}
    assert chain._parse_usdt(good, W1).value == 5
    assert chain._parse_usdt({**good, "to": W2}, W1) is None                       # 남의 지갑으로 간 전송
    assert chain._parse_usdt({**good, "token_info": {"address": "TFake"}}, W1) is None   # 가짜 토큰
    assert chain._parse_usdt({**good, "value": "0"}, W1) is None
    assert chain._parse_usdt({**good, "type": "Approval"}, W1) is None and chain._parse_usdt({}, W1) is None
    trx = {"txID": "x", "block_timestamp": 2, "ret": [{"contractRet": "SUCCESS"}], "raw_data": {"contract": [{
        "type": "TransferContract", "parameter": {"value": {"amount": 7, "owner_address": SENDERH, "to_address": W1H}}}]}}
    d = chain._parse_trx(trx, W1)
    assert (d.token, d.value, d.sender) == ("TRX", 7, SENDER)
    assert chain._parse_trx({**trx, "ret": [{"contractRet": "REVERT"}]}, W1) is None
    assert chain._parse_trx(trx, W2) is None and chain._parse_trx({}, W1) is None


# ───────────── 지갑 등록 ─────────────

async def test_register_wallet_flow(env):
    await register(env)
    w = (await store.wallets_of(env.db, U1))[0]
    assert (w["address"], w["label"]) == (W1, "내 지갑") and "내 지갑" in last(env, U1)
    assert (await env.db.execute_fetchall("SELECT cursor_ms FROM watch_state"))[0][0] >= w["created_at"] * 1000 - 1
    await press(env, U1, "w")
    assert W1 in last(env, U1) and "1/3" in last(env, U1)
    await press(env, U1, f"w:{w['id']}")
    assert "TRONSCAN" in last(env, U1)


async def test_register_without_label_shows_short_address(env):
    await register(env, label=None)
    assert (await store.wallets_of(env.db, U1))[0]["label"] == ""
    await press(env, U1, "w")
    buttons = [b["text"] for row in json.loads(env.tg.of("editMessageText")[-1]["reply_markup"])["inline_keyboard"] for b in row]
    assert f"💼 {W1[:6]}…{W1[-6:]}" in buttons


async def test_wallet_validation_and_limits(env):
    await start(env)
    await press(env, U1, "wa")
    for bad in ("hello", W1[:-1] + ("a" if W1[-1] != "a" else "b"), "0x" + "a" * 40):
        await say(env, U1, bad)
        assert "올바른 TRON 주소가 아닙니다" in last(env, U1) and await store.wallets_of(env.db, U1) == []
    for addr in (W1, W2, W3):
        await press(env, U1, "wa") if addr != W1 else None
        await say(env, U1, addr)
        await say(env, U1, "-")
    assert len(await store.wallets_of(env.db, U1)) == 3
    await press(env, U1, "wa")                                   # 4번째
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert") and len(await store.wallets_of(env.db, U1)) == 3


async def test_duplicate_wallet_rejected_but_other_user_can_share_address(env):
    await register(env)
    await press(env, U1, "wa")
    await say(env, U1, W1)
    assert "이미 등록" in last(env, U1)
    await register(env, uid=U2, addr=W1, label="공유")
    assert len(await store.wallets_of(env.db, U2)) == 1
    assert await store.watched_total(env.db) == 1                # 같은 주소는 한 번만 센다


async def test_server_wallet_cap_counts_distinct_addresses(env):
    await start(env)
    await start(env, U2)
    env.dp.workflow_data["max_wallets_total"] = 1
    await press(env, U1, "wa"); await say(env, U1, W1); await say(env, U1, "-")
    await press(env, U2, "wa"); await say(env, U2, W2)
    assert "자리가 없습니다" in last(env, U2) and await store.wallets_of(env.db, U2) == []
    await press(env, U2, "wa"); await say(env, U2, W1); await say(env, U2, "-")    # 이미 지켜보는 주소는 자리를 안 쓴다
    assert len(await store.wallets_of(env.db, U2)) == 1


async def test_rename_delete_and_ownership(env):
    await register(env)
    w = await wid(env)
    await press(env, U1, f"wr:{w}")
    await say(env, U1, "새 이름")
    assert (await store.wallets_of(env.db, U1))[0]["label"] == "새 이름"
    await press(env, U2, f"wd:{w}")                              # 남의 지갑은 못 건드린다
    await press(env, U2, f"wdok:{w}")
    assert len(await store.wallets_of(env.db, U1)) == 1
    await press(env, U1, f"wd:{w}")
    assert len(await store.wallets_of(env.db, U1)) == 1          # 확인 전에는 지우지 않는다
    await press(env, U1, f"wdok:{w}")
    assert await store.wallets_of(env.db, U1) == [] and await store.watched_total(env.db) == 0
    assert await env.db.execute_fetchall("SELECT * FROM watch_state") == []


async def test_cancel_and_unknown_text(env):
    await start(env)
    await press(env, U1, "wa")
    await say(env, U1, "/cancel")
    await say(env, U1, W1)                                       # 취소 뒤에는 주소로 받지 않는다
    assert await store.wallets_of(env.db, U1) == [] and "메뉴에서 선택" in last(env, U1)


# ───────────── 입금 알림 (블록 스캔) ─────────────

async def init_scan(e):
    """스캔 위치를 현재 최신 블록으로 잡는다(봇이 처음 켜진 상황)."""
    await e.scanner.step()


async def scan_all(e):
    """아직 안 본 블록을 모두 처리한다."""
    while (await store.get_scan(e.db))["last_block"] < e.tron.head:
        await e.scanner.step()


def deposits_in(e, uid) -> list[str]:
    return [t for t in e.tg.texts_to(uid) if "📥 <b>입금 알림" in t]


async def test_scanner_starts_from_latest_block_and_ignores_history(env):
    await register(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 9_000_000, "before-start")])        # 스캔을 시작하기 전 블록
    await init_scan(env)
    assert (await store.get_scan(env.db))["last_block"] == env.tron.head
    assert deposits_in(env, U1) == []
    await env.scanner.step()                                                       # 다음 블록은 아직 없다
    assert deposits_in(env, U1) == []


async def test_usdt_deposit_pending_then_confirmed(env):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 25_500_000, "tx-usdt-1")])
    await scan_all(env)
    msg = last(env, U1)
    assert "입금 알림" in msg and "내 지갑" in msg and "+25.50 USDT" in msg and "확정 대기 중" in msg
    assert "≈ 36,414원" in msg and SENDER in msg and "tronscan.org/#/transaction/tx-usdt-1" in msg and "소액" not in msg
    assert len(await store.due_pending(env.db, 2**40)) == 1

    await env.watcher.confirm(time.time() + 30)                    # 아직 확정 전이면 그대로
    assert "확정 대기 중" in env.tg.of("sendMessage")[-1]["text"] and not env.tg.of("editMessageText")
    env.tron.solid.add("tx-usdt-1")
    await env.watcher.confirm(time.time() + 60)
    edited = env.tg.of("editMessageText")[-1]
    assert "✅ <b>확정</b>" in edited["text"] and "확정 대기" not in edited["text"] and "+25.50 USDT" in edited["text"]
    assert await store.due_pending(env.db, 2**40) == []


async def test_trx_deposit_alert(env):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_trx(W1H, 3_000_000, "tx-trx-1")])
    await scan_all(env)
    assert "+3.00 TRX" in last(env, U1) and "≈ 1,260원" in last(env, U1) and SENDER in last(env, U1)


async def test_transfer_from_uses_real_sender(env):
    await register(env)
    await init_scan(env)
    real_sender, real_hex = mk(55)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 4_000_000, "tx-from", selector="23b872dd", from_hex=real_hex)])
    await scan_all(env)
    assert "+4.00 USDT" in last(env, U1) and real_sender in last(env, U1) and SENDER not in last(env, U1)


async def test_non_deposits_are_ignored(env):
    await register(env)
    await init_scan(env)
    T = FakeTron
    env.tron.new_block([
        T.tx_trx(W1H, 9_000_000, "failed", ret="REVERT"),
        T.tx_trx(W1H, 9_000_000, "wrong-kind", kind="DelegateResourceContract"),
        T.tx_trx(W2H, 9_000_000, "someone-else"),
        T.tx_trx(W1H, 0, "zero"),
        T.tx_usdt(W1H, 9_000_000, "usdt-failed", ret="OUT_OF_ENERGY"),
        T.tx_usdt(W1H, 9_000_000, "fake-token", contract="41" + "ab" * 20),
        T.tx_usdt(W1H, 9_000_000, "approve", selector="095ea7b3"),
        T.tx_usdt(W2H, 9_000_000, "usdt-elsewhere"),
        {"txID": "garbage", "raw_data": {}},
        {"txID": "garbage2"},
    ])
    await scan_all(env)
    assert deposits_in(env, U1) == []
    assert await store.get_scan(env.db) is not None


def test_scan_block_handles_many_addresses_and_malformed_data():
    watch = {W1H: W1, W2H: W2}
    T = FakeTron
    blk = Block(7, 123, [T.tx_trx(W1H, 1, "a"), T.tx_usdt(W2H, 2, "b"), T.tx_trx(W3H, 3, "c"), {"x": 1}, None])
    found = chain.scan_block(blk, watch)
    assert [(d.txid, d.token, d.value, d.to, d.ts_ms) for d in found] == [("a", "TRX", 1, W1, 123), ("b", "USDT", 2, W2, 123)]
    assert address_to_hex(W1) == W1H


async def test_several_wallets_in_one_block_each_alerted_once(env):
    await register(env, U1, W1, "첫째")
    await register(env, U2, W2, "둘째")
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 1_000_000, "m1"), FakeTron.tx_trx(W2H, 2_000_000, "m2"),
                        FakeTron.tx_usdt(W4H, 3_000_000, "not-watched")])
    await scan_all(env)
    assert "첫째" in last(env, U1) and "+1.00 USDT" in last(env, U1)
    assert "둘째" in last(env, U2) and "+2.00 TRX" in last(env, U2)
    assert len(deposits_in(env, U1)) == len(deposits_in(env, U2)) == 1


async def test_same_address_two_users_both_alerted(env):
    await register(env, U1, W1, "첫째")
    await register(env, U2, W1, "둘째")
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 7_000_000, "shared-1")])
    await scan_all(env)
    assert "첫째" in last(env, U1) and "둘째" in last(env, U2)


async def test_deposit_before_registration_not_alerted(env):
    await start(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 50_000_000, "old-1")], ts=int(time.time() * 1000) - 3600_000)
    await press(env, U1, "wa"); await say(env, U1, W1); await say(env, U1, "-")
    await scan_all(env)
    assert deposits_in(env, U1) == []


async def test_small_deposit_filtered_by_minimum_and_counted(env):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 50_000, "dust-1")])      # 0.05 < 기본 0.1
    await scan_all(env)
    assert deposits_in(env, U1) == [] and (await store.get_settings(env.db, U1))["filtered"] == 1
    await press(env, U1, "al")
    assert "걸러진 소액 입금" in last(env, U1)
    await press(env, U1, "mn:USDT")
    await say(env, U1, "0")                                            # 모두 알림
    env.tron.new_block([FakeTron.tx_usdt(W1H, 50_000, "dust-2")])
    await scan_all(env)
    assert "+0.05 USDT" in last(env, U1) and "소액 입금" in last(env, U1)      # 소액 경고가 붙는다


async def test_minimum_input_validation(env):
    await register(env)
    await press(env, U1, "mn:TRX")
    for bad in ("abc", "-3", "0.1234567"):
        await say(env, U1, bad)
        assert (await store.get_settings(env.db, U1))["min_trx"] == 100_000
    await say(env, U1, "2.5")
    assert (await store.get_settings(env.db, U1))["min_trx"] == 2_500_000


async def test_new_wallet_is_picked_up_without_restart_and_deleted_one_is_dropped(env):
    await register(env, U1, W1)
    await init_scan(env)
    await register(env, U2, W2, "새 지갑")                               # 스캔이 도는 중에 등록
    env.tron.new_block([FakeTron.tx_trx(W2H, 5_000_000, "new-1")])
    await scan_all(env)
    assert "새 지갑" in last(env, U2)
    await press(env, U2, f"wdok:{await wid(env, U2, W2)}")
    env.tron.new_block([FakeTron.tx_trx(W2H, 6_000_000, "new-2")])
    await scan_all(env)
    assert len(deposits_in(env, U2)) == 1


async def test_blocked_user_is_deactivated_and_dropped_from_watch(env):
    await register(env)
    await init_scan(env)
    env.tg.forbidden.add(U1)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 4_000_000, "blocked-1")])
    await scan_all(env)
    async with env.db.execute("SELECT is_active FROM users WHERE user_id=?", (U1,)) as cur:
        assert (await cur.fetchone())["is_active"] == 0
    assert await store.watched_hex(env.db) == {}                       # 막은 사람의 지갑은 더 안 본다
    assert await store.due_pending(env.db, 2**40) == []


# ───────────── 블록 스캔 동작 ─────────────

async def test_step_waits_for_next_block_and_paces_itself(env):
    await init_scan(env)
    assert await env.scanner.step() == 0.7                              # 다음 블록이 아직 없다
    env.tron.new_block([], ts=now_ms(0))
    delay = await env.scanner.step()
    assert 2.5 <= delay <= 3.5                                          # 방금 만들어진 블록이면 약 3초 뒤에 다음 블록
    env.tron.new_block([], ts=now_ms(0) - 20_000)
    assert await env.scanner.step() == 0.0                              # 밀린 블록이면 기다리지 않고 바로


async def test_scan_resumes_after_restart_without_double_alerts(env):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 1_000_000, "r1")])
    await scan_all(env)
    # 봇이 꺼진 동안 블록 세 개가 생겼다
    for i in range(3):
        env.tron.new_block([FakeTron.tx_usdt(W1H, (i + 2) * 1_000_000, f"r{i + 2}")])
    fresh = Scanner(env.db, env.chain, env.watcher)
    while (await store.get_scan(env.db))["last_block"] < env.tron.head:
        await fresh.step()
    assert [t.split("+")[1].split(" ")[0] for t in deposits_in(env, U1)] == ["1.00", "2.00", "3.00", "4.00"]


async def test_long_lag_falls_back_to_history_and_jumps_to_head(env):
    await register(env)
    await init_scan(env)
    first = env.tron.head
    for _ in range(3):
        env.tron.new_block([], ts=now_ms(0) - 3600_000)               # 한 시간 전 블록들
    env.tron.pay_usdt(W1, 8_000_000, "missed-1")                        # 그동안 들어온 입금(내역 조회로 보임)
    await env.scanner.step()
    assert (await store.get_scan(env.db))["last_block"] == env.tron.head and env.tron.head > first + 1
    assert "+8.00 USDT" in last(env, U1) and "확정 대기" not in last(env, U1) and "✅ <b>확정</b>" in last(env, U1)


async def test_scan_errors_keep_position_and_recover(env):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 2_000_000, "e1")])
    env.tron.fail = True
    with pytest.raises(chain.TronError):
        await env.scanner.step()
    env.tron.fail, env.tron.limit = False, True
    with pytest.raises(chain.RateLimited):
        await env.scanner.step()
    env.tron.limit = False
    assert (await store.get_scan(env.db))["last_block"] == env.tron.head - 1       # 위치는 그대로
    await scan_all(env)
    assert "+2.00 USDT" in last(env, U1)


async def test_scan_loop_reports_rate_limit_to_admin_once(env):
    import asyncio

    from bot import services

    await register(env)
    await init_scan(env)
    env.tron.limit = True
    task = asyncio.create_task(services._scan_loop(env.scanner))
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert sum("호출 한도" in t for t in env.tg.texts_to(ADMIN)) == 1
    assert env.watcher.paused(time.time())


# ───────────── 확정 확인 ─────────────

async def pending_deposit(env, txid="p1", micro=6_000_000):
    await register(env)
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W1H, micro, txid)])
    await scan_all(env)


async def test_dropped_deposit_is_marked_cancelled(env):
    await pending_deposit(env)
    env.tron.known.discard("p1")                                       # 블록이 뒤집혀 거래가 사라졌다
    await env.watcher.confirm(time.time() + 120)                       # 아직 10분 전이라 기다린다
    assert not env.tg.of("editMessageText")
    await env.watcher.confirm(time.time() + 700)
    assert "❌ <b>취소됨</b>" in env.tg.of("editMessageText")[-1]["text"] and await store.due_pending(env.db, 2**40) == []


async def test_unconfirmed_for_too_long_gives_up_with_notice(env):
    await pending_deposit(env)                                         # 거래는 있는데 확정이 안 된다
    await env.watcher.confirm(time.time() + 700)
    assert not env.tg.of("editMessageText")                            # 10분이 지났어도 거래가 있으면 계속 기다린다
    await env.watcher.confirm(time.time() + 2000)
    assert "확정 여부를 확인하지 못했습니다" in env.tg.of("editMessageText")[-1]["text"]
    assert await store.due_pending(env.db, 2**40) == []


async def test_confirm_survives_chain_errors_and_rate_limit(env):
    await pending_deposit(env)
    env.tron.solid.add("p1")
    env.tron.fail = True
    await env.watcher.confirm(time.time() + 60)
    assert len(await store.due_pending(env.db, 2**40)) == 1 and not env.tg.of("editMessageText")
    env.tron.fail, env.tron.limit = False, True
    await env.watcher.confirm(time.time() + 90)
    assert env.watcher.paused(time.time() + 90) and not env.tg.of("editMessageText")
    env.tron.limit = False
    env.watcher._pause_until = 0
    await env.watcher.confirm(time.time() + 200)
    assert "✅ <b>확정</b>" in env.tg.of("editMessageText")[-1]["text"]


async def test_confirm_checks_are_spaced_out(env):
    await pending_deposit(env)
    env.tron.calls.clear()
    await env.watcher.confirm(time.time() + 25)
    await env.watcher.confirm(time.time() + 26)                        # 방금 확인했으니 20초 안에는 다시 묻지 않는다
    assert len(env.tron.calls) == 1


# ───────────── 잔액 대조 · 놓친 입금 보충 ─────────────

async def test_reconcile_records_baseline_then_finds_contract_deposits(env):
    await register(env)
    env.tron.balance(W1, usdt_micro=10_000_000)
    now = time.time()
    await env.watcher.reconcile(now + 2000)                            # 처음엔 기준만 기록한다
    assert deposits_in(env, U1) == []
    env.tron.balance(W1, usdt_micro=40_000_000)                        # 컨트랙트를 거쳐 30 USDT가 들어왔다(블록 스캔은 못 봄)
    env.tron.pay_usdt(W1, 30_000_000, "via-contract", ts=int(now * 1000) + 3000)
    await env.watcher.reconcile(now + 4000)
    assert "+30.00 USDT" in last(env, U1) and "확정 대기" not in last(env, U1)
    await env.watcher.reconcile(now + 6000)
    assert len(deposits_in(env, U1)) == 1                              # 두 번 알리지 않는다


async def test_reconcile_skips_history_when_balance_unchanged_or_lower(env):
    await register(env)
    env.tron.balance(W1, usdt_micro=10_000_000, trx_micro=5_000_000)
    now = time.time()
    await env.watcher.reconcile(now + 2000)
    env.tron.calls.clear()
    env.tron.balance(W1, usdt_micro=10_000_000, trx_micro=5_000_000)
    await env.watcher.reconcile(now + 4000)
    env.tron.balance(W1, usdt_micro=1_000_000, trx_micro=1_000_000)    # 출금으로 줄었다
    await env.watcher.reconcile(now + 6000)
    assert all(a == W1 for a in env.tron.calls) and len(env.tron.calls) == 2    # 잔액 조회 2번뿐, 내역 조회는 없다


async def test_reconcile_not_due_and_batch_limit(env):
    from bot import watcher as W

    await register(env)
    now = time.time()
    await env.watcher.reconcile(now + 2000)
    env.tron.calls.clear()
    await env.watcher.reconcile(now + 2100)                            # 대조 간격(30분) 전이면 조회하지 않는다
    assert env.tron.calls == []
    for i in range(W.RECONCILE_BATCH + 5):                             # 주소가 많아도 한 번에 일부만
        await store.add_wallet(env.db, U1, mk(100 + i)[0], "", now=1)
        await store.set_balance(env.db, mk(100 + i)[0], 0, 0, 0)
    env.tron.calls.clear()
    await env.watcher.reconcile(now + 2200)
    assert len(env.tron.calls) == W.RECONCILE_BATCH


async def test_failed_alert_is_recovered_by_reconcile(env, monkeypatch):
    await register(env)
    env.tron.balance(W1, usdt_micro=0)
    now = time.time()
    await env.watcher.reconcile(now + 2000)
    await init_scan(env)
    real = env.bot.send_message
    fails = {"n": 1}

    async def flaky(*a, **kw):
        if fails["n"]:
            fails["n"] -= 1
            raise TelegramAPIError(method=None, message="boom")
        return await real(*a, **kw)

    monkeypatch.setattr(env.bot, "send_message", flaky)
    env.tron.new_block([FakeTron.tx_usdt(W1H, 6_000_000, "flaky-1")])
    await scan_all(env)
    assert deposits_in(env, U1) == []
    env.tron.balance(W1, usdt_micro=6_000_000)
    env.tron.pay_usdt(W1, 6_000_000, "flaky-1", ts=int(now * 1000) + 3000)
    await env.watcher.reconcile(now + 4000)
    assert sum("+6.00 USDT" in t for t in deposits_in(env, U1)) == 1


async def test_reconcile_failures_are_reported_and_rate_limit_pauses(env):
    await register(env)
    now = time.time()
    env.tron.fail = True
    await env.watcher.reconcile(now + 2000)
    assert any("잔액 대조가 실패" in t for t in env.tg.texts_to(ADMIN))
    env.tron.fail, env.tron.limit = False, True
    await env.watcher.reconcile(now + 2100)
    assert env.watcher.paused(now + 2100) and any("호출 한도" in t for t in env.tg.texts_to(ADMIN))


# ───────────── 잔액 · 시세 ─────────────

async def test_balance_with_krw_and_total(env):
    await register(env, addr=W1, label="A")
    await press(env, U1, "wa"); await say(env, U1, W2); await say(env, U1, "B")
    env.tron.balance(W1, usdt_micro=100_000_000, trx_micro=50_000_000)
    env.tron.balance(W2, usdt_micro=1_500_000)
    await say(env, U1, "/잔액")
    out = last(env, U1)
    assert "100.00 USDT" in out and "50.00 TRX" in out and "1.50 USDT" in out and "0.00 TRX" in out
    assert "≈ 163,800원" in out                                    # 100*1428 + 50*420 = 163,800
    assert "합계" in out and "101.50 USDT" in out and "≈ <b>165,942원</b>" in out
    assert "빗썸 기준" in out


async def test_balance_inactive_address_and_failures(env):
    await register(env, label="A")
    await say(env, U1, "/balance")
    assert "0.00 USDT" in last(env, U1)                            # 아직 활성화 안 된 주소도 0으로
    env.chain._cache.clear()
    env.tron.fail = True
    await say(env, U1, "/잔액")
    assert "블록체인 조회에 실패" in last(env, U1)
    env.tron.fail = False
    await start(env, U2)
    await say(env, U2, "/잔액")
    assert "등록된 지갑이 없습니다" in last(env, U2)


async def test_balance_without_prices_still_shown(env):
    await register(env)
    env.tron.balance(W1, usdt_micro=2_000_000)
    env.market.bithumb_down = True
    await say(env, U1, "/잔액")
    assert "2.00 USDT" in last(env, U1) and "원화 환산은 생략" in last(env, U1)


async def test_price_and_kimp(env):
    await start(env)
    for cmd in ("/시세", "/price"):
        await say(env, U1, cmd)
        out = last(env, U1)
        assert "1,428원" in out and "1,400.00원" in out and "+2.00%" in out and "420.00원" in out, cmd
    env.market.usdt = 1330.0
    env.prices._last = None
    await say(env, U1, "/시세")
    assert "-5.00%" in last(env, U1)


async def test_price_cache_and_fallbacks(env):
    await start(env)
    await say(env, U1, "/시세")
    await say(env, U1, "/시세")
    assert env.market.calls["bithumb"] == 1                        # 10초 안에는 외부에 다시 묻지 않는다
    env.market.bithumb_down = True
    env.prices._last = type(env.prices._last)(**{**env.prices._last.__dict__, "ts": time.time() - 60})
    await say(env, U1, "/시세")
    assert "조회 지연" in last(env, U1) and "1,428원" in last(env, U1)
    env.prices._last = None
    await say(env, U1, "/시세")
    assert "시세를 가져오지 못했습니다" in last(env, U1)


async def test_fx_failure_keeps_price_but_no_kimp(env):
    await start(env)
    env.market.fx_down = True
    await say(env, U1, "/시세")
    assert "1,428원" in last(env, U1) and "계산할 수 없습니다" in last(env, U1)


# ───────────── 김프 조건 ─────────────

async def add_rule(e, direction, pct, uid=U1):
    await press(e, uid, f"kr+:{direction}")
    await say(e, uid, str(pct))


async def test_kimp_rule_fires_once_and_rearms(env):
    await register(env)
    await add_rule(env, "up", 3)
    assert "김프 +3% 이상" in last(env, U1)
    await env.watcher.kimp()                                       # 지금 +2.00% → 아직
    assert not any("김프 알림" in t for t in env.tg.texts_to(U1))
    env.market.usdt = 1450.0                                       # +3.57%
    env.prices._last = None
    await env.watcher.kimp()
    await env.watcher.kimp()
    assert sum("김프 알림" in t for t in env.tg.texts_to(U1)) == 1  # 연달아 확인해도 한 번만
    env.market.usdt = 1443.0                                       # +3.07% — 아직 조건 안쪽이라 다시 안 울린다
    env.prices._last = None
    await env.watcher.kimp()
    env.market.usdt = 1440.0                                       # +2.86%: 3-0.2=2.8보다 높아 재대기 안 됨
    env.prices._last = None
    await env.watcher.kimp()
    env.market.usdt = 1435.0                                       # +2.5%: 재대기
    env.prices._last = None
    await env.watcher.kimp()
    env.market.usdt = 1450.0
    env.prices._last = None
    await env.watcher.kimp()
    assert sum("김프 알림" in t for t in env.tg.texts_to(U1)) == 2


async def test_kimp_down_rule_and_negative_input(env):
    await start(env)
    await add_rule(env, "down", "-1.5")
    assert "김프 -1.5% 이하" in last(env, U1)
    env.market.usdt = 1375.0                                       # -1.79%
    env.prices._last = None
    await env.watcher.kimp()
    assert "김프 알림" in last(env, U1) and "-1.79%" in last(env, U1)


async def test_kimp_rule_limit_input_and_delete(env):
    await start(env)
    await press(env, U1, "kr+:up")
    for bad in ("abc", "500", "nan"):
        await say(env, U1, bad)
        assert await store.rules_of(env.db, U1) == []
    await say(env, U1, "1")
    for p in (2, 3):
        await add_rule(env, "up", p)
    assert len(await store.rules_of(env.db, U1)) == 3
    await press(env, U1, "kr+:down")
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")
    rid = (await store.rules_of(env.db, U1))[0]["id"]
    await press(env, U2, f"kr-:{rid}")                              # 남의 조건은 못 지운다
    assert len(await store.rules_of(env.db, U1)) == 3
    await press(env, U1, f"kr-:{rid}")
    assert len(await store.rules_of(env.db, U1)) == 2


async def test_kimp_rule_skipped_when_fx_missing_or_stale(env):
    await start(env)
    await add_rule(env, "up", -10)                                  # 항상 만족하는 조건
    env.market.fx_down = True
    env.prices._last = env.prices._last_fx = None                    # 캐시 없이 환율 조회가 실패하는 상황
    await env.watcher.kimp()
    assert not any("김프 알림" in t for t in env.tg.texts_to(U1))   # 환율이 없으면 알리지 않는다
    env.market.fx_down = False
    env.prices._last = None
    await env.watcher.kimp()
    assert "김프 알림" in last(env, U1)


# ───────────── 정기 시세 ─────────────

def is_daily(t: str) -> bool:
    return "🕘 <b>정기 시세</b>" in t and "빗썸 USDT" in t     # 설정 화면과 구분한다


def kst_ts(h, m, s=0, day=15):
    return datetime(2026, 11, day, h, m, s, tzinfo=KST).timestamp()


async def test_daily_time_input_and_toggle(env):
    await start(env)
    await press(env, U1, "dl+")
    await say(env, U1, "저녁쯤")
    assert await store.daily_times(env.db, U1) == []
    await say(env, U1, "오후 9시 30분")
    assert [t["hhmm"] for t in await store.daily_times(env.db, U1)] == ["21:30"]
    assert (await store.get_settings(env.db, U1))["daily_on"] == 1
    await press(env, U1, "dl+"); await say(env, U1, "21:30")
    assert "이미 등록한 시각" in last(env, U1)
    for t in ("09:00", "12:00"):
        await press(env, U1, "dl+") if t == "09:00" else await press(env, U1, "dl+")
        await say(env, U1, t)
    await press(env, U1, "dl+")
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")    # 4번째는 막는다
    await press(env, U1, "dl:off")
    assert (await store.get_settings(env.db, U1))["daily_on"] == 0
    await press(env, U1, "dl-:09:00")
    assert [t["hhmm"] for t in await store.daily_times(env.db, U1)] == ["12:00", "21:30"]


async def test_daily_sent_once_inside_window_only(env):
    await start(env)
    await press(env, U1, "dl+"); await say(env, U1, "09:00")
    sent = lambda: sum(is_daily(t) for t in env.tg.texts_to(U1))   # noqa: E731
    await env.watcher.daily(kst_ts(8, 59, 30))
    assert sent() == 0                                               # 아직 시각 전
    await env.watcher.daily(kst_ts(9, 0, 5))
    await env.watcher.daily(kst_ts(9, 0, 20))
    await env.watcher.daily(kst_ts(9, 5))
    assert sent() == 1                                               # 같은 날 두 번 안 보낸다
    await env.watcher.daily(kst_ts(9, 0, 5, day=16))
    assert sent() == 2                                               # 다음 날은 다시 보낸다


async def test_daily_catches_up_shortly_after_restart_but_not_much_later(env):
    await start(env)
    await press(env, U1, "dl+"); await say(env, U1, "09:00")
    await env.watcher.daily(kst_ts(9, 12))                           # 10분 넘게 늦었으면 보내지 않는다
    assert not any(is_daily(t) for t in env.tg.texts_to(U1))
    await env.watcher.daily(kst_ts(9, 8))                            # 10분 안이면 늦게라도 보낸다
    assert is_daily(last(env, U1))


async def test_daily_off_and_price_failure_retry(env):
    await start(env)
    await press(env, U1, "dl+"); await say(env, U1, "09:00")
    env.market.bithumb_down = True
    await env.watcher.daily(kst_ts(9, 1))
    assert not any(is_daily(t) for t in env.tg.texts_to(U1))
    env.market.bithumb_down = False
    await env.watcher.daily(kst_ts(9, 2))                            # 실패는 보냈다고 치지 않아서 이어서 보낸다
    assert is_daily(last(env, U1))
    await press(env, U1, "dl:off")
    n = len(env.tg.texts_to(U1))
    await env.watcher.daily(kst_ts(9, 0, day=17))
    assert len(env.tg.texts_to(U1)) == n


# ───────────── 명령어 · 시작·종료 ─────────────

async def test_commands_korean_and_english(env):
    await register(env)
    for cmd, needle in (("/지갑", "내 지갑"), ("/wallet", "내 지갑"), ("/알림", "알림 설정"), ("/alerts", "알림 설정"),
                        ("/설정", "알림 설정"), ("/시세", "시세"), ("/menu", "메뉴"), ("/help", "사용 방법")):
        await say(env, U1, cmd)
        assert needle in last(env, U1), cmd


async def test_startup_and_shutdown_hooks(env):
    import dataclasses

    from bot import services

    cfg = dataclasses.replace(env.cfg, env={"MAX_WALLETS_TOTAL": "7", "RECONCILE_MINUTES": "1", "TRONGRID_URL": env.tron.base,
                                            "BITHUMB_URL": env.market.base, "FX_URL": env.market.base})
    extra = await services.on_startup(env.bot, env.db, cfg)
    try:
        assert extra["max_wallets_total"] == 7 and extra["watcher"].reconcile_seconds == 300   # 너무 짧은 간격은 5분으로 올린다
    finally:
        await services.on_shutdown(extra)
    assert all(t.done() for t in extra["bg_tasks"])
    cfg = dataclasses.replace(env.cfg, env={"TRONGRID_URL": env.tron.base})
    extra = await services.on_startup(env.bot, env.db, cfg)
    try:
        assert extra["max_wallets_total"] == 500 and extra["watcher"].reconcile_seconds == 1800    # 기본값
    finally:
        await services.on_shutdown(extra)


async def test_wallet_added_without_start_is_still_watched(env):
    await press(env, U2, "wa")                                      # /start 없이 이전 메시지의 버튼만 눌러 온 사용자
    await say(env, U2, W2)
    await say(env, U2, "-")
    await init_scan(env)
    env.tron.new_block([FakeTron.tx_usdt(W2H, 8_000_000, "nostart-1")])
    await scan_all(env)
    assert "+8.00 USDT" in last(env, U2)
