"""승인된 주문을 SMM 패널에 넣고, 진행 상태를 추적한다.

중복 주문 방지가 가장 중요하다:
  paid → submitting 으로 먼저 바꾼 뒤에만 패널을 호출한다 (한 번에 한 작업만 이 전이를 통과).
  패널이 명확히 거절하면 paid로 되돌려 재시도하고, 응답이 불확실하면(시간 초과 등) 절대 다시 보내지 않고 manual_review로 둔다.
"""
from __future__ import annotations

import logging
import time

import aiosqlite

from bot import orders
from bot.payments import Event
from bot.smm import PanelRejected, PanelUncertain, SmmPanel

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STATUS_INTERVAL = 120      # 처리 중 주문의 패널 상태를 확인하는 간격(초)
DONE = {"completed"}
FAILED = {"canceled", "cancelled", "refunded", "fail", "failed", "partial"}


async def recover(db: aiosqlite.Connection) -> int:
    """재시작 직후: 패널 호출 도중이던(submitting) 주문은 접수 여부를 알 수 없으니 사람이 확인하게 둔다."""
    cur = await db.execute(
        "UPDATE orders SET status='manual_review', note='패널 접수 중 재시작 – 패널에서 접수 여부 확인 필요' "
        "WHERE status='submitting'"
    )
    await db.commit()
    return cur.rowcount


async def fulfil_due(db: aiosqlite.Connection, panel: SmmPanel, now: int | None = None) -> list[Event]:
    now = int(now if now is not None else time.time())
    events: list[Event] = []
    async with db.execute("SELECT * FROM orders WHERE status='paid' AND next_try<=? ORDER BY id", (now,)) as cur:
        due = list(await cur.fetchall())
    for o in due:
        if not await orders.transition(db, o["id"], "paid", "submitting"):
            continue
        try:
            panel_id = await panel.add(o["service_id"], o["link"], o["quantity"])
        except PanelRejected as e:
            attempts = o["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                await orders.transition(db, o["id"], "submitting", "manual_review",
                                        attempts=attempts, note=f"패널 거절: {e}")
                events.append(Event("panel_failed", o["id"], o["user_id"], str(e)))
            else:
                await orders.transition(db, o["id"], "submitting", "paid", attempts=attempts,
                                        next_try=now + 60 * attempts, note=f"패널 거절: {e}")
                if attempts == 1:
                    events.append(Event("panel_retry", o["id"], o["user_id"], str(e)))
            continue
        except PanelUncertain as e:
            await orders.transition(db, o["id"], "submitting", "manual_review",
                                    note=f"패널 응답 불확실 – 접수 여부 확인 필요: {e}")
            events.append(Event("panel_uncertain", o["id"], o["user_id"], str(e)))
            continue
        await orders.transition(db, o["id"], "submitting", "processing", panel_order_id=panel_id,
                                attempts=o["attempts"] + 1, checked_at=now)
        events.append(Event("submitted", o["id"], o["user_id"], panel_id))
    return events


async def poll_status(db: aiosqlite.Connection, panel: SmmPanel, now: int | None = None) -> list[Event]:
    now = int(now if now is not None else time.time())
    events: list[Event] = []
    async with db.execute(
        "SELECT * FROM orders WHERE status='processing' AND checked_at<=? ORDER BY id", (now - STATUS_INTERVAL,)
    ) as cur:
        rows = list(await cur.fetchall())
    for o in rows:
        try:
            body = await panel.status(o["panel_order_id"])
        except (PanelRejected, PanelUncertain) as e:
            log.warning("주문 %s 상태 확인 실패: %s", o["id"], e)
            await db.execute("UPDATE orders SET checked_at=? WHERE id=?", (now, o["id"]))
            await db.commit()
            continue
        st = str(body.get("status", "")).strip()
        low = st.lower()
        if low in DONE:
            if await orders.transition(db, o["id"], "processing", "completed", panel_status=st, done_at=now):
                events.append(Event("completed", o["id"], o["user_id"]))
        elif low in FAILED:
            if await orders.transition(db, o["id"], "processing", "manual_review", panel_status=st,
                                       note=f"패널 상태 {st} – 환불/재처리 필요"):
                events.append(Event("panel_failed", o["id"], o["user_id"], st))
        else:
            await db.execute("UPDATE orders SET panel_status=?, checked_at=? WHERE id=?", (st, now, o["id"]))
            await db.commit()
    return events
