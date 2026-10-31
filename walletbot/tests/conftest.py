import pytest

from bot.energy import SCHEMA as ENERGY_SCHEMA
from bot.energy import Desk, migrate
from bot.handlers import router
from bot.notify import Notifier
from bot.prices import Prices
from bot.scanner import Scanner
from bot.store import SCHEMA
from bot.texts import TEXTS
from bot.chain import TronReader
from bot.watcher import Watcher
from core.bot_runner import assemble
from core.db import open_db
from core.texts import Texts
from bot.energy import SIM_HOLDER, SIM_OWNER
from support import PAY, FakeMarket, FakeSupplier, FakeTron


class Env:
    pass


@pytest.fixture(autouse=True)
def _fresh_memory():
    """메모리에만 두는 상태(대화별 마지막 주소, 조회 횟수 제한)를 테스트마다 비운다."""
    from bot.handlers import business, quick

    business.RECENT.clear()
    business._fail_notice.clear()
    quick.LIMIT.clear()


@pytest.fixture
async def make_env(make_cfg, bot, tg):
    made = []

    async def _make(channels=(), suppliers=None, pay=PAY, margin=0, max_loss=1_000_000, support="", ttl=600):
        tron, market = FakeTron(), FakeMarket()
        tron.usdt_bal.update({SIM_OWNER: 10**12, SIM_HOLDER: 10**12})     # 모의 실행용 주소 (실제 체인과 같게)
        await tron.start()
        await market.start()
        cfg = make_cfg(channels=channels)
        db = await open_db(cfg.db_path, SCHEMA + ENERGY_SCHEMA)
        await migrate(db)
        reader, prices = TronReader(tron.base, ""), Prices(market.base, market.base)
        notifier = Notifier(bot, db, cfg.admin_ids)
        watcher = Watcher(db, reader, prices, notifier, reconcile_seconds=1800)
        if suppliers is None:
            suppliers = [FakeSupplier("s1", 16, tron), FakeSupplier("s2", 20, tron)]
        else:
            for s in suppliers:
                s.tron = tron
        desk = Desk(db, reader, notifier, suppliers, pay, margin=margin, max_loss=max_loss, support=support, ttl=ttl)
        scanner = Scanner(db, reader, watcher, desk)
        router._parent_router = None
        dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS),
                               {"chain": reader, "prices": prices, "notifier": notifier, "watcher": watcher,
                                "max_wallets_total": 30, "scanner": scanner, "energy": desk})
        e = Env()
        e.tron, e.market, e.db, e.dp, e.bot, e.tg, e.watcher, e.chain, e.prices, e.notifier, e.cfg = (
            tron, market, db, dp, bot, tg, watcher, reader, prices, notifier, cfg)
        e.scanner, e.desk, e.suppliers = scanner, desk, suppliers
        made.append(e)
        return e

    yield _make
    for e in made:
        await e.chain.close(); await e.prices.close()
        await e.tron.runner.cleanup(); await e.market.runner.cleanup()
        await e.db.close()


@pytest.fixture
async def env(make_env):
    return await make_env()
