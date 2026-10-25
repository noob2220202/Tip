"""지갑 알림 봇의 문구와 버튼.

kakao·boost와 같이 합쇼체로 쓰고, 이모지·굵게·인용·코드·링크를 적극적으로 쓴다.
(docs/STYLE_GUIDE.md의 해요체·절제 규칙에 대한 이 봇 한정 예외)
지갑 별명·주소처럼 사용자가 정한 값은 모두 esc()로 감싼다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from bot import money
from bot.chain import Balance, Deposit
from bot.prices import Quote
from core.style import esc

KST = timezone(timedelta(hours=9))   # 한국은 서머타임이 없어서 고정 오프셋이면 충분하다 (서버 시간대에 의존하지 않음)
SCAN_TX = "https://tronscan.org/#/transaction/{}"
SCAN_ADDR = "https://tronscan.org/#/address/{}"
SMALL = 1_000_000   # 이보다 작은 입금에는 주소 위장 스팸 경고를 붙인다 (1.0)

TEXTS: dict[str, list[str]] = {
    "start": [
        "💼 <b>지갑 알림 봇</b>에 오신 것을 환영합니다.\n\n"
        "<blockquote>📥 내 <b>TRON 지갑</b>(최대 3개)에 <b>USDT·TRX가 입금</b>되면 바로 알려 드립니다.\n"
        "💰 지갑 <b>잔액</b>을 원화로 환산해 볼 수 있습니다.\n"
        "📈 빗썸 <b>USDT 시세</b>와 <b>김프</b>를 확인하고, 조건이 되면 알림을 받습니다.</blockquote>\n\n"
        "아래 버튼으로 시작해 주십시오. 👇",
    ],
    "help": [
        "❓ <b>사용 방법</b>\n\n"
        "<blockquote>1️⃣ <b>💼 내 지갑</b>에서 TRON 주소(<code>T</code>로 시작)를 등록합니다.\n"
        "2️⃣ 등록한 시점부터 <b>들어오는 입금</b>을 알려 드립니다. 이전 내역은 알리지 않습니다.\n"
        "3️⃣ <b>💰 잔액</b>, <b>📈 시세</b>는 언제든 조회하실 수 있습니다.\n"
        "4️⃣ <b>🔔 알림 설정</b>에서 최소 알림 금액, 김프 조건, 정기 시세를 정하십시오.</blockquote>\n\n"
        "⌨️ <b>/지갑</b> <b>/잔액</b> <b>/시세</b> <b>/알림</b> · 입력 취소는 <b>/cancel</b>\n\n"
        "ℹ️ 입금은 블록체인에서 <b>확정된 뒤</b> 알려 드려서 실제 입금보다 1~2분 늦을 수 있습니다.",
    ],
}

UNKNOWN = "메뉴에서 선택해 주십시오. 👇"
CANCELLED = "취소했습니다."
BAD_ADDRESS = "❌ 올바른 TRON 주소가 아닙니다. <code>T</code>로 시작하는 34자리 주소를 확인해 주십시오."
DUP_WALLET = "⚠️ 이미 등록하신 지갑입니다."
WALLET_LIMIT = "⚠️ 지갑은 최대 <b>{n}개</b>까지 등록하실 수 있습니다. 쓰지 않는 지갑을 삭제한 뒤 다시 시도해 주십시오."
SERVER_FULL = "⚠️ 지금은 새 지갑을 받을 자리가 없습니다. 잠시 뒤에 다시 시도해 주십시오."
NO_WALLET = "등록된 지갑이 없습니다. <b>💼 내 지갑</b>에서 먼저 지갑을 등록해 주십시오."
NOT_FOUND = "해당 항목을 찾을 수 없습니다."
PRICE_FAIL = "⚠️ 시세를 가져오지 못했습니다. 잠시 뒤에 다시 시도해 주십시오."
CHAIN_FAIL = "⚠️ 블록체인 조회에 실패했습니다. 잠시 뒤에 다시 시도해 주십시오."


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-6:]}"


def kst(ts: float, fmt: str = "%m/%d %H:%M") -> str:
    return datetime.fromtimestamp(ts, KST).strftime(fmt)


def name_of(w) -> str:
    return esc(w["label"]) if w["label"] else short(w["address"])


# ---- 버튼 ----

def menu_kb() -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="💼 내 지갑", callback_data="w"), Btn(text="💰 잔액", callback_data="bal")],
        [Btn(text="📈 시세", callback_data="px"), Btn(text="🔔 알림 설정", callback_data="al")],
        [Btn(text="❓ 사용 방법", callback_data="help")],
    ])


def back_kb(to: str = "menu", label: str = "⬅️ 메뉴") -> Kb:
    return Kb(inline_keyboard=[[Btn(text=label, callback_data=to)]])


def refresh_kb(data: str) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🔄 새로고침", callback_data=data), Btn(text="⬅️ 메뉴", callback_data="menu")]])


def wallets_kb(wallets, can_add: bool) -> Kb:
    rows = [[Btn(text=f"💼 {w['label'] or short(w['address'])}", callback_data=f"w:{w['id']}")] for w in wallets]
    if can_add:
        rows.append([Btn(text="➕ 지갑 등록", callback_data="wa")])
    rows.append([Btn(text="⬅️ 메뉴", callback_data="menu")])
    return Kb(inline_keyboard=rows)


def wallet_kb(wid: int) -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="✏️ 별명 변경", callback_data=f"wr:{wid}"), Btn(text="🗑 삭제", callback_data=f"wd:{wid}")],
        [Btn(text="⬅️ 목록", callback_data="w")],
    ])


def confirm_kb(wid: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🗑 삭제합니다", callback_data=f"wdok:{wid}"),
                                Btn(text="취소", callback_data=f"w:{wid}")]])


def skip_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="별명 없이 등록", callback_data="wskip")]])


def alerts_kb(s, daily_on: bool) -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="💵 최소 금액(USDT)", callback_data="mn:USDT"), Btn(text="🪙 최소 금액(TRX)", callback_data="mn:TRX")],
        [Btn(text="📉 김프 조건", callback_data="kr"), Btn(text=f"🕘 정기 시세 {'ON' if daily_on else 'OFF'}", callback_data="dl")],
        [Btn(text="⬅️ 메뉴", callback_data="menu")],
    ])


def rules_kb(rules, can_add: bool) -> Kb:
    rows = [[Btn(text=f"🗑 {rule_label(r)}", callback_data=f"kr-:{r['id']}")] for r in rules]
    if can_add:
        rows.append([Btn(text="⬆️ 이상일 때 추가", callback_data="kr+:up"), Btn(text="⬇️ 이하일 때 추가", callback_data="kr+:down")])
    rows.append([Btn(text="⬅️ 알림 설정", callback_data="al")])
    return Kb(inline_keyboard=rows)


def daily_kb(times, on: bool, can_add: bool) -> Kb:
    rows = [[Btn(text=f"정기 시세 {'끄기' if on else '켜기'}", callback_data="dl:off" if on else "dl:on")]]
    rows += [[Btn(text=f"🗑 {t['hhmm']}", callback_data=f"dl-:{t['hhmm']}")] for t in times]
    if can_add:
        rows.append([Btn(text="➕ 시각 추가", callback_data="dl+")])
    rows.append([Btn(text="⬅️ 알림 설정", callback_data="al")])
    return Kb(inline_keyboard=rows)


# ---- 지갑 ----

def wallets_text(wallets, limit: int) -> str:
    if not wallets:
        return "💼 <b>내 지갑</b>\n\n" + NO_WALLET + f"\n\n(최대 {limit}개)"
    lines = "\n".join(f"{i}. <b>{name_of(w)}</b>\n    <code>{w['address']}</code>" for i, w in enumerate(wallets, 1))
    return f"💼 <b>내 지갑</b> ({len(wallets)}/{limit})\n\n<blockquote>{lines}</blockquote>"


def wallet_text(w) -> str:
    return (f"💼 <b>{name_of(w)}</b>\n\n<blockquote><code>{w['address']}</code>\n"
            f"📅 등록 {kst(w['created_at'])}\n"
            f"🔗 <a href=\"{SCAN_ADDR.format(w['address'])}\">TRONSCAN에서 보기</a></blockquote>")


ADD_ASK = ("➕ <b>지갑 등록</b>\n\n등록할 <b>TRON 지갑 주소</b>를 보내 주십시오. (<code>T</code>로 시작하는 34자리)\n"
           "입금 알림만 받는 용도이며 <b>개인키나 시드는 절대 보내지 마십시오</b>.\n취소는 /cancel")
LABEL_ASK = "🏷 이 지갑의 <b>별명</b>을 보내 주십시오. (최대 20자)\n별명 없이 등록하려면 아래 버튼을 누르십시오."
RENAME_ASK = "🏷 새 <b>별명</b>을 보내 주십시오. (최대 20자, 지우려면 <code>-</code>)"


def added(w) -> str:
    return (f"✅ <b>{name_of(w)}</b> 지갑을 등록했습니다.\n\n<blockquote><code>{w['address']}</code></blockquote>\n"
            "지금부터 들어오는 <b>USDT·TRX 입금</b>을 알려 드립니다.")


# ---- 입금 알림 ----

def deposit_text(w, d: Deposit, quote: Quote | None) -> str:
    amount = money.fmt(d.value)
    approx = ""
    if quote:
        price = quote.usdt_krw if d.token == "USDT" else quote.trx_krw
        approx = f" (≈ {money.won(d.value, price):,}원)"
    warn = ""
    if d.value < SMALL:
        warn = ("\n\n⚠️ <b>소액 입금</b>입니다. 내 주소와 비슷한 주소로 위장해 송금을 유도하는 스팸일 수 있으니, "
                "<b>보낸 주소로 송금하지 마십시오.</b>")
    return (
        f"📥 <b>입금 알림</b> — {name_of(w)}\n\n"
        f"<blockquote>💰 <b>+{amount} {d.token}</b>{approx}\n"
        f"👤 보낸 주소 <code>{esc(d.sender)}</code>\n"
        f"🕒 {kst(d.ts_ms / 1000)} (KST)\n"
        f"🔗 <a href=\"{SCAN_TX.format(d.txid)}\">거래 내역 보기</a></blockquote>{warn}"
    )


# ---- 잔액 ----

def balance_text(rows: list[tuple[object, Balance | None]], quote: Quote | None) -> str:
    lines, tot_usdt, tot_trx = [], 0, 0
    for w, bal in rows:
        if bal is None:
            lines.append(f"💼 <b>{name_of(w)}</b>\n    ⚠️ 조회 실패")
            continue
        tot_usdt, tot_trx = tot_usdt + bal.usdt, tot_trx + bal.trx
        lines.append(f"💼 <b>{name_of(w)}</b>\n    💵 {money.fmt(bal.usdt)} USDT\n    🪙 {money.fmt(bal.trx)} TRX"
                     + (f"\n    ≈ {_krw(bal, quote)}원" if quote else ""))
    out = "💰 <b>잔액</b>\n\n<blockquote>" + "\n\n".join(lines) + "</blockquote>"
    if len(rows) > 1:
        out += (f"\n\n<b>합계</b>\n💵 {money.fmt(tot_usdt)} USDT · 🪙 {money.fmt(tot_trx)} TRX"
                + (f"\n≈ <b>{_krw(Balance(tot_usdt, tot_trx), quote):}원</b>" if quote else ""))
    if quote:
        out += "\n\n" + _basis(quote)
    else:
        out += "\n\n※ 시세를 가져오지 못해 원화 환산은 생략했습니다."
    return out


def _krw(bal: Balance, quote: Quote) -> str:
    return f"{money.won(bal.usdt, quote.usdt_krw) + money.won(bal.trx, quote.trx_krw):,}"


def _basis(q: Quote) -> str:
    return f"※ 빗썸 기준 · {kst(q.ts, '%H:%M:%S')}" + (" (조회 지연: 마지막 값)" if q.stale else "")


# ---- 시세 ----

def price_text(q: Quote, title: str = "📈 <b>시세</b>") -> str:
    if q.kimp is None:
        kimp = "🇰🇷 김프 — 환율을 가져오지 못해 계산할 수 없습니다."
        fx = ""
    else:
        sign = "+" if q.kimp >= 0 else ""
        kimp = f"{'🔺' if q.kimp >= 0 else '🔻'} 김프 — <b>{sign}{q.kimp:.2f}%</b>"
        fx = f"🌐 기준 환율(USD) — <b>{q.fx:,.2f}원</b>\n"
    return (
        f"{title}\n\n<blockquote>💵 빗썸 USDT — <b>{q.usdt_krw:,.0f}원</b>\n"
        f"{fx}{kimp}\n🪙 빗썸 TRX — <b>{q.trx_krw:,.2f}원</b></blockquote>\n\n{_basis(q)}\n"
        "※ 김프 = 빗썸 USDT 가격 ÷ 기준 환율 − 1 (USDT 1개를 1달러로 계산)"
    )


# ---- 알림 설정 ----

def alerts_text(s, rules, times) -> str:
    filtered = f"\n🧹 걸러진 소액 입금 — <b>{s['filtered']}건</b>" if s["filtered"] else ""
    daily = ("ON " + ", ".join(t["hhmm"] for t in times)) if s["daily_on"] and times else "OFF"
    return (
        "🔔 <b>알림 설정</b>\n\n"
        f"<blockquote>💵 최소 알림 금액(USDT) — <b>{money.fmt(s['min_usdt'])}</b>\n"
        f"🪙 최소 알림 금액(TRX) — <b>{money.fmt(s['min_trx'])}</b>{filtered}\n"
        f"📉 김프 조건 — <b>{len(rules)}개</b>\n"
        f"🕘 정기 시세 — <b>{daily}</b></blockquote>\n\n"
        "최소 금액보다 작은 입금은 알리지 않습니다. 소액 스팸 입금을 막는 용도이며 <code>0</code>이면 모두 알립니다."
    )


def rule_label(r) -> str:
    return f"김프 {r['pct']:+g}% {'이상' if r['direction'] == 'up' else '이하'}"


def rules_text(rules, kimp: float | None) -> str:
    now = "" if kimp is None else f"\n\n지금 김프는 <b>{kimp:+.2f}%</b>입니다."
    body = "\n".join(f"• {rule_label(r)}" + ("" if r["armed"] else " <i>(발동됨)</i>") for r in rules) or "등록된 조건이 없습니다."
    return ("📉 <b>김프 조건 알림</b>\n\n"
            f"<blockquote>{body}</blockquote>{now}\n\n"
            "조건이 되면 한 번 알려 드리고, 김프가 0.2%p 이상 되돌아가면 다시 대기합니다. (최대 3개)")


def asks(kind: str, **kw) -> str:
    return {
        "min": "💵 <b>{token}</b> 최소 알림 금액을 숫자로 보내 주십시오. (현재 {cur}, 모두 알리려면 <code>0</code>)",
        "rule": "📉 김프가 몇 <b>%</b>일 때 알려 드릴까요? 숫자로 보내 주십시오.\n예) <code>3</code>, <code>-1.5</code> (현재 김프 {now})",
        "time": "🕘 정기 시세를 받을 <b>시각</b>을 보내 주십시오. (한국 시간)\n예) <code>09:00</code>, <code>오후 9시 30분</code>",
    }[kind].format(**kw)


def rule_alert(r, q: Quote) -> str:
    return (f"📉 <b>김프 알림</b>\n\n<blockquote>설정하신 조건 <b>{rule_label(r)}</b>에 도달했습니다.\n"
            f"지금 김프 <b>{q.kimp:+.2f}%</b> · 빗썸 USDT <b>{q.usdt_krw:,.0f}원</b></blockquote>")


def daily_text(q: Quote) -> str:
    return price_text(q, "🕘 <b>정기 시세</b>")
