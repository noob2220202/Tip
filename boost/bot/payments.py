"""입금 매칭. TronGrid에서 받은 전송 목록을 주문과 맞춰 본다.

인정 조건 (전부 만족해야 자동 승인):
  보낸 주소 == 주문에 입력한 주소 · 금액이 정확히 일치 · 주문 생성 시각 ≤ 블록 시간 ≤ 만료 시각 · 아직 안 쓰인 txid
같은 조건의 주문이 여러 개면 먼저 만든 주문부터 맞춘다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import aiosqlite

from bot.tron import Transfer


LATE_WINDOW_MS = 6 * 3600 * 1000   # 만료/취소 후 이 시간 안에 들어온 입금만 '늦은 입금'으로 본다


@dataclass(frozen=True)
class Event:
    kind: str          # paid | late | mismatch
    order_id: int
    user_id: int
    detail: str = ""


async def _record(db: aiosqlite.Connection, t: Transfer, kind: str, order_id: int | None, now: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO transfers (txid, sender, value, ts, kind, order_id, seen_at) VALUES (?,?,?,?,?,?,?)",
        (t.txid, t.sender, t.value, t.ts, kind, order_id, now),
    )


async def process_transfers(db: aiosqlite.Connection, transfers: list[Transfer], now: int | None = None) -> list[Event]:
    now = int(now if now is not None else time.time())
    events: list[Event] = []
    for t in sorted(transfers, key=lambda x: x.ts):
        async with db.execute("SELECT 1 FROM transfers WHERE txid=?", (t.txid,)) as cur:
            if await cur.fetchone():
                continue
        async with db.execute(
            """SELECT * FROM orders WHERE from_addr=? AND txid IS NULL
                 AND status IN ('awaiting_payment','expired','cancelled') AND created_at*1000<=?
                 AND (status='awaiting_payment' OR expires_at*1000>=?)
               ORDER BY created_at, id""",
            (t.sender, t.ts, t.ts - LATE_WINDOW_MS),
        ) as cur:
            cands = list(await cur.fetchall())

        exact = [o for o in cands if o["amount_micro"] == t.value]
        if exact:
            # 기한 안에 도착한 주문 중 가장 먼저 만든 것
            in_time = [o for o in exact if o["status"] != "cancelled" and t.ts <= o["expires_at"] * 1000]
            if in_time:
                o = in_time[0]
                ok = await _claim(db, o["id"], t, now)
                if ok:
                    await _record(db, t, "matched", o["id"], now)
                    events.append(Event("paid", o["id"], o["user_id"]))
                    continue
            # 정확히 맞지만 기한이 지났거나 취소된 주문 → 사람이 확인
            o = exact[0]
            cur = await db.execute(
                "UPDATE orders SET status='manual_review', txid=?, paid_at=?, note=? WHERE id=? AND txid IS NULL",
                (t.txid, now, "기한 후/취소 후 입금", o["id"]),
            )
            if cur.rowcount:
                await _record(db, t, "late", o["id"], now)
                events.append(Event("late", o["id"], o["user_id"], f"{t.txid}"))
                continue
        elif cands:
            await _record(db, t, "mismatch", cands[0]["id"], now)
            events.append(Event("mismatch", cands[0]["id"], cands[0]["user_id"],
                                f"{t.value} (주문 {cands[0]['amount_micro']}) {t.txid}"))
            continue
        await _record(db, t, "unknown", None, now)
    await db.commit()
    return events


async def _claim(db: aiosqlite.Connection, order_id: int, t: Transfer, now: int) -> bool:
    cur = await db.execute(
        "UPDATE orders SET status='paid', txid=?, paid_at=? WHERE id=? AND txid IS NULL "
        "AND status IN ('awaiting_payment','expired')",
        (t.txid, now, order_id),
    )
    return cur.rowcount == 1
