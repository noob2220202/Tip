"""/adstats <캠페인ID>: 전달 지표 리포트와 텍스트 내보내기.

텔레그램은 노출/열람/클릭을 알려주지 않으므로 전달 성공 수만 측정한다.
"""
from __future__ import annotations

import time

import aiosqlite
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from core.admin import AdminFilter
from core.broadcast import models
from core.style import b
from core.texts import Texts


def _ts(v: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"


async def build_report(db: aiosqlite.Connection, campaign_id: int) -> dict | None:
    c = await models.get_campaign(db, campaign_id)
    if c is None:
        return None
    counts = await models.recipient_counts(db, campaign_id)
    target = c["target_count"]
    rate = (counts["sent"] / target * 100) if target else 0.0
    return {"campaign": c, "counts": counts, "target": target, "rate": rate}


def report_text(r: dict) -> str:
    c, n = r["campaign"], r["counts"]
    return (
        f"캠페인 #{c['id']} {c['title']}\n"
        f"상태: {c['status']}\n"
        f"시작: {_ts(c['started_at'])} / 종료: {_ts(c['finished_at'])}\n"
        f"발송 대상: {r['target']}명\n"
        f"전달 성공: {n['sent']}명\n"
        f"차단·실패: {n['blocked'] + n['failed']}명 (차단 {n['blocked']}, 실패 {n['failed']})\n"
        f"대기 중: {n['pending']}명\n"
        f"전달률: {r['rate']:.1f}%\n"
        "\n"
        "* 텔레그램은 노출·열람·클릭을 알려주지 않아 측정하지 않습니다. 전달 성공 수가 대체 지표입니다.\n"
    )


def build_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="core_report")
    router.message.filter(AdminFilter(admin_ids))

    @router.message(Command("adstats"))
    async def adstats(message: Message, db: aiosqlite.Connection, texts: Texts) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("사용법: /adstats 캠페인ID")
            return
        r = await build_report(db, int(parts[1]))
        if r is None:
            await message.answer(texts.get("bc_not_found"))
            return
        n = r["counts"]
        rate = f"{r['rate']:.1f}%"
        await message.answer(
            f"캠페인 #{r['campaign']['id']} · {b(r['campaign']['status'])}\n"
            f"대상 {b(r['target'])} · 전달 성공 {b(n['sent'])} · 차단·실패 {n['blocked'] + n['failed']}\n"
            f"전달률 {b(rate)}\n"
            "노출·클릭은 텔레그램에서 안 알려줘서 못 재요."
        )
        await message.answer_document(
            BufferedInputFile(report_text(r).encode(), filename=f"campaign_{r['campaign']['id']}.txt"),
            caption="광고주에게 전달할 수 있는 텍스트 리포트예요.",
        )

    return router
