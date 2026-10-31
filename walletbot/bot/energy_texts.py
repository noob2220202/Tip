"""에너지 임대 문구와 버튼. 처음 쓰는 분도 따라 할 수 있게 단계마다 무엇을 하는지 적는다.

지갑 봇과 같이 합쇼체, 이모지·굵게·인용·코드를 쓴다. 주소처럼 사용자가 보낸 값은 esc()로 감싼다.
"""
from __future__ import annotations

from decimal import Decimal

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from bot import money
from bot.chain import Deposit
from core.style import esc

SUN = 1_000_000
SCAN_TX = "https://tronscan.org/#/transaction/{}"
SCAN_ADDR = "https://tronscan.org/#/address/{}"


def trx(sun: int) -> str:
    return money.fmt(sun)


def plain(sun: int) -> str:
    """지갑 앱에 그대로 붙여 넣을 금액 (쉼표 없이)."""
    return format((Decimal(sun) / SUN).normalize(), "f")


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-6:]}"


def won(sun: int, trx_krw: float | None) -> str:
    return f" (≈ {money.won(sun, trx_krw):,}원)" if trx_krw else ""


def contact(support: str) -> str:
    """문의처. 설정이 없으면 채널 공지를 가리킨다."""
    return f"<b>{esc(support)}</b>" if support else "<b>관리자</b>(채널 공지의 문의처)"


def user_tag(user_id: int, username: str | None) -> str:
    name = f" @{esc(username)}" if username else ""
    return f"<a href=\"tg://user?id={user_id}\">{user_id}</a>{name}"


# ───────────── 버튼 ─────────────

def intro_kb(enabled: bool) -> Kb:
    rows = [[Btn(text="⚡ 에너지 사기", callback_data="en:buy")]] if enabled else []
    rows += [[Btn(text="📖 처음이십니까?", callback_data="en:guide"), Btn(text="❓ 자주 묻는 질문", callback_data="en:faq")],
             [Btn(text="📋 내 주문", callback_data="en:list"), Btn(text="⬅️ 메뉴", callback_data="menu")]]
    return Kb(inline_keyboard=rows)


def back_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="⬅️ 에너지 임대", callback_data="en")]])


def wallets_kb(wallets) -> Kb:
    rows = [[Btn(text=f"💼 {w['label'] or short(w['address'])}", callback_data=f"en:w:{w['id']}")] for w in wallets]
    rows.append([Btn(text="취소", callback_data="en")])
    return Kb(inline_keyboard=rows)


def recipient_kb() -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="✅ 받는 주소에 USDT가 있습니다", callback_data="en:r:1")],
        [Btn(text="⚪ USDT가 없습니다 · 잘 모릅니다", callback_data="en:r:0")],
        [Btn(text="취소", callback_data="en")],
    ])


def quote_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="✅ 주문하기", callback_data="en:ok"), Btn(text="취소", callback_data="en")]])


def order_kb(order_id: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🔄 상태 확인", callback_data=f"en:o:{order_id}"),
                                Btn(text="❌ 주문 취소", callback_data=f"en:x:{order_id}")]])


def refund_kb(order_id: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="💸 환불받을 주소 보내기", callback_data=f"en:rf:{order_id}")]])


def status_kb(order_id: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🔄 상태 확인", callback_data=f"en:o:{order_id}"),
                                Btn(text="⬅️ 에너지 임대", callback_data="en")]])


# ───────────── 소개 · 안내 ─────────────

def trx2(sun: int) -> str:
    """태우는 TRX처럼 대략만 보여 줄 금액 (소수 둘째 자리)."""
    return f"{Decimal(sun) / SUN:,.2f}"


def intro(prices: dict[bool, tuple[int, int]] | None, trx_krw: float | None, margin: int, enabled: bool) -> str:
    """prices: {받는 쪽 USDT 보유 여부: (우리 가격, 태우면 드는 TRX)}"""
    head = ("⚡ <b>TRON 에너지 임대</b>\n\n"
            "<blockquote>USDT(TRC20)를 보낼 때 지갑에 <b>에너지</b>가 없으면 수수료로 <b>TRX가 타서 없어집니다.</b>\n"
            "에너지를 <b>1시간</b> 빌려 두면 그 수수료를 <b>대부분 아낄 수 있습니다.</b></blockquote>")
    if not enabled:
        return head + "\n\n🛠 지금은 <b>준비 중</b>입니다. 조금만 기다려 주십시오."
    if not prices:
        return head + "\n\n⚠️ 지금 가격을 가져오지 못했습니다. 잠시 뒤에 다시 열어 주십시오."
    lines = []
    for has, label in ((True, "받는 주소에 USDT가 <b>있을 때</b>"), (False, "받는 주소에 USDT가 <b>없을 때</b>")):
        if has not in prices:
            continue
        ours, burn = prices[has]
        save = max(0, round((1 - ours / burn) * 100)) if burn else 0
        lines.append(f"{label}\n   ⚡ <b>{trx(ours)} TRX</b>{won(ours, trx_krw)}\n"
                     f"   🔥 그냥 보내면 {trx2(burn)} TRX{won(burn, trx_krw)} → <b>{save}% 절약</b>")
    promo = ("\n\n🎁 지금은 <b>공급처 원가 그대로</b> 드리는 홍보 기간입니다. "
             "(주문을 구분하는 금액 끝자리만 0.001 TRX 단위로 조금 다를 수 있습니다)") if margin == 0 else ""
    return (head + "\n\n💸 <b>지금 가격</b> (USDT 1번 보내는 데 필요한 양)\n<blockquote>" + "\n\n".join(lines)
            + "</blockquote>" + promo + "\n\n처음이시면 <b>📖 처음이십니까?</b>부터 읽어 주십시오. 👇")


def guide(late_minutes: int) -> str:
    return (
        "📖 <b>처음 쓰시는 분을 위한 안내</b>\n\n"
        "<blockquote>1️⃣ <b>⚡ 에너지 사기</b>를 누르고 <b>USDT를 보낼 내 지갑 주소</b>를 보냅니다.\n"
        "     에너지는 <b>보내는 지갑</b>에 있어야 합니다. 받는 사람 지갑이 아닙니다.\n\n"
        "2️⃣ <b>받는 사람 주소</b>를 보내 주시면 필요한 양을 정확히 계산해 드립니다.\n"
        "     받는 주소의 USDT 잔액이 <b>지금 0</b>이면(예전에 있었더라도) 에너지가 약 <b>두 배</b> 필요합니다.\n"
        "     잘 모르시면 주소를 보내 주시거나 <b>없음·모름</b>을 고르십시오.\n\n"
        "3️⃣ 안내된 <b>TRX 금액을 소수점까지 정확히</b> 보냅니다. (주문마다 끝자리가 다릅니다)\n"
        "     가능하면 <b>USDT를 보낼 그 지갑</b>에서 보내 주십시오. 거래소에서 출금하셔도 되지만 "
        f"<b>도착 금액</b>이 정확히 같아야 하고, 기한이 지나고 {late_minutes}분 넘게 늦으면 자동으로 처리되지 않습니다.\n\n"
        "4️⃣ 몇 초 뒤 <b>✅ 에너지 도착</b> 메시지가 오면 그때 <b>USDT를 보내십시오.</b>\n\n"
        "5️⃣ 에너지는 <b>1시간 동안</b>만 쓸 수 있습니다. 도착하면 바로 보내십시오.</blockquote>\n\n"
        "💡 <b>꼭 알아 두십시오</b>\n"
        "<blockquote>• 지갑에 <b>TRX가 0.5개 이상</b> 남아 있어야 합니다. USDT를 보낼 때 <b>대역폭 수수료</b>(최대 약 0.35 TRX)가 "
        "따로 나올 수 있습니다.\n"
        "• 처음 만든 지갑은 TRX를 한 번이라도 받아야 <b>활성화</b>됩니다. 활성화되지 않은 지갑에는 에너지를 보낼 수 없습니다.\n"
        "• 저희는 <b>개인키·시드·승인(Approve)</b>을 절대 요구하지 않습니다. 요구하는 곳은 사기입니다.</blockquote>"
    )


def faq(minutes: int, late_minutes: int, support: str) -> str:
    return (
        "❓ <b>자주 묻는 질문</b>\n\n"
        "<blockquote expandable><b>Q. 에너지란 무엇입니까?</b>\n"
        "TRON에서 USDT를 보낼 때 쓰는 연료입니다. 없으면 같은 양만큼 TRX가 타서 없어집니다.\n\n"
        "<b>Q. 왜 받는 주소에 따라 가격이 다릅니까?</b>\n"
        "받는 주소의 USDT 잔액이 지금 0이면 TRON 수수료 규칙상 에너지가 약 두 배(13만) 듭니다. "
        "모르시면 <b>없음·모름</b>을 고르시면 안전합니다.\n\n"
        "<b>Q. 거래소에서 보내도 됩니까?</b>\n"
        "됩니다. 에너지는 주문할 때 입력한 지갑으로 갑니다. 다만 <b>도착하는 금액</b>이 주문 금액과 정확히 같아야 합니다. "
        "출금 수수료가 보내는 금액에서 빠지는 거래소라면 그만큼 더해서 출금해 주십시오. "
        "출금이 늦어질 수 있으니 가능하면 개인 지갑에서 보내 주십시오.\n\n"
        "<b>Q. 금액을 잘못 보냈습니다.</b>\n"
        "자동으로 처리되지 않습니다. 주문한 지갑에서 보내셨다면 봇이 알려 드리고 <b>💸 환불받을 주소 보내기</b> 버튼이 나옵니다. "
        f"다른 지갑·거래소에서 보내셨다면 {contact(support)}에게 <b>주문 번호와 거래 해시(TXID)</b>를 보내 주십시오.\n\n"
        f"<b>Q. 주문 시간({minutes}분)이 지난 뒤에 보냈습니다.</b>\n"
        f"만료 후 {late_minutes}분 안에 <b>정확한 금액</b>이 도착하면 그대로 처리해 드립니다.\n\n"
        "<b>Q. 에너지가 도착했는데도 TRX가 조금 빠졌습니다.</b>\n"
        "에너지와 별개인 <b>대역폭 수수료</b>(최대 약 0.35 TRX)입니다. 하루 무료 대역폭은 USDT 한 번 보내기에도 빠듯합니다.\n\n"
        "<b>Q. 남은 에너지는 환불됩니까?</b>\n"
        "1시간이 지나면 회수되며 환불되지 않습니다. USDT를 보낼 때마다 그때그때 빌려 주십시오.\n\n"
        "<b>Q. 에너지가 들어왔는지 어떻게 확인합니까?</b>\n"
        "도착 메시지에 남은 에너지를 적어 드립니다. TronLink 같은 지갑의 <b>자원(에너지)</b> 화면이나 "
        "<b>/조회</b> <code>내주소</code>로도 확인하실 수 있습니다.</blockquote>"
    )


# ───────────── 주문 단계 ─────────────

def ask_receiver(has_wallets: bool) -> str:
    pick = "\n아래에서 등록한 지갑을 고르셔도 됩니다." if has_wallets else ""
    return ("⚡ <b>1단계 · 에너지를 받을 지갑</b>\n\n"
            "<b>USDT를 보낼 내 지갑 주소</b>를 보내 주십시오. (<code>T</code>로 시작하는 34자리)\n"
            "<blockquote>⚠️ 받는 사람 주소가 아닙니다. <b>보내는 지갑</b>에 에너지가 있어야 수수료가 빠지지 않습니다.\n"
            "⚠️ 거래소 입금 주소에 에너지를 넣으면 쓸 수 없습니다.</blockquote>" + pick + "\n취소는 /cancel")


NOT_ACTIVE = ("⚪ 이 지갑은 아직 <b>활성화되지 않았습니다.</b> (TRX를 한 번도 받은 적 없음)\n\n"
              "<blockquote>활성화되지 않은 지갑에는 에너지를 보낼 수 없습니다.\n"
              "거래소나 다른 지갑에서 <b>TRX를 1~2개</b> 먼저 보내 활성화한 뒤 다시 시도해 주십시오.\n"
              "(USDT 수수료 외에 대역폭 수수료로도 TRX가 조금 필요합니다)</blockquote>")
IS_CONTRACT = "📜 <b>컨트랙트 주소</b>에는 에너지를 보낼 수 없습니다. 개인 지갑 주소를 보내 주십시오."
IS_PAY = "⚠️ 결제 받는 주소입니다. <b>USDT를 보낼 내 지갑 주소</b>를 보내 주십시오."
CHECK_FAIL = "⚠️ 지갑을 확인하지 못했습니다. 잠시 뒤에 다시 시도해 주십시오."
PRICE_FAIL = "⚠️ 지금 공급처 가격을 가져오지 못했습니다. 잠시 뒤에 다시 시도해 주십시오."
SLOTS_FULL = "⚠️ 지금 주문이 몰려 있습니다. 1~2분 뒤에 다시 시도해 주십시오."
TOO_MANY = "⚠️ 1시간에 만들 수 있는 주문 수를 넘었습니다. 잠시 뒤에 다시 시도해 주십시오."
SAME_ADDRESS = "⚠️ 내 지갑과 같은 주소입니다. <b>USDT를 받을 상대 주소</b>를 보내 주십시오."
EXPIRED_QUOTE = "⌛ 견적이 오래되어 다시 계산했습니다. 금액을 확인하고 다시 눌러 주십시오."


def ask_recipient(receiver: str, energy: int, bandwidth: int, trx_balance: int) -> str:
    have = f"\n⚡ 지금 이 지갑의 에너지: <b>{energy:,}</b>" if energy else ""
    return ("⚡ <b>2단계 · USDT를 받을 주소</b>\n\n"
            f"<blockquote>💼 내 지갑 <code>{esc(receiver)}</code>{have}</blockquote>\n\n"
            "USDT를 <b>받을 상대 주소</b>를 보내 주시면 필요한 에너지를 <b>정확히</b> 계산해 드립니다.\n"
            "주소를 모르시면 아래 버튼을 눌러 주십시오. 👇")


def enough(energy: int, need: int) -> str:
    return (f"✅ 이 지갑에는 이미 에너지가 <b>{energy:,}</b> 있습니다. USDT 한 번에 필요한 양은 <b>{need:,}</b>이라서 "
            "<b>지금 그대로 보내셔도 됩니다.</b> 임대하지 않으셔도 됩니다.")


def quote_text(receiver: str, recipient: str | None, has_usdt: bool | None, need: int, energy: int, price: int,
               burn: int, trx_krw: float | None, margin: int, bandwidth: int) -> str:
    if recipient:
        to = f"📤 받는 주소 <code>{esc(recipient)}</code>"
    else:
        to = "📤 받는 주소에 USDT " + ("<b>있음</b>" if has_usdt else "<b>없음·모름</b> (넉넉하게 계산)")
    save = max(0, round((1 - price / burn) * 100)) if burn else 0
    bw = ("\n💡 지금 무료 대역폭이 부족해 USDT를 보낼 때 <b>대역폭 수수료</b>(약 0.35 TRX)가 따로 나올 수 있습니다. "
          "지갑에 TRX를 0.5개 이상 남겨 두십시오.") if bandwidth < 700 else ""
    promo = " · 🎁 공급처 원가 그대로" if margin == 0 else ""
    return ("⚡ <b>3단계 · 주문 확인</b>\n\n"
            f"<blockquote>💼 에너지 받을 지갑 <code>{esc(receiver)}</code>\n{to}\n"
            f"🔋 필요한 에너지 {need:,} → <b>{energy:,}</b> 임대 (1시간)\n"
            f"💰 가격 <b>{trx(price)} TRX</b>{won(price, trx_krw)}{promo}\n"
            f"🔥 그냥 보내면 {trx2(burn)} TRX{won(burn, trx_krw)} → <b>{save}% 절약</b></blockquote>{bw}\n\n"
            "맞으면 <b>✅ 주문하기</b>를 눌러 주십시오. 다음 화면에서 보낼 금액을 알려 드립니다.\n"
            "※ 주문을 구분하려고 보낼 금액 끝자리에 0.001~0.099 TRX가 더해질 수 있습니다.")


def order_created(o, pay_address: str, minutes: int, inline: bool = False) -> str:
    where = f"<code>{esc(pay_address)}</code>" if inline else "(다음 메시지를 눌러 복사)"
    return ("🧾 <b>주문 #{id} · 4단계 · 입금</b>\n\n"
            "아래 주소로 <b>정확히 이 금액</b>의 <b>TRX</b>를 보내 주십시오.\n\n"
            "<blockquote>💰 보낼 금액 <code>{amount}</code> <b>TRX</b>\n"
            "📥 받는 주소 {where}\n"
            "⏰ <u>{minutes}분 이내</u></blockquote>\n\n"
            "⚠️ <b>소수점까지 정확히</b> 보내야 자동으로 처리됩니다. 금액이 다르면 처리되지 않습니다.\n"
            "⚠️ <b>TRX</b>만 보내 주십시오. USDT를 보내면 처리되지 않습니다.\n"
            "💡 가능하면 USDT를 보낼 그 지갑에서 보내 주십시오. 거래소 출금은 <b>도착 금액</b>이 같아야 합니다.\n"
            "⚡ 입금이 블록에 들어가면 몇 초 안에 에너지를 보내 드립니다.").format(
        id=o["id"], amount=plain(o["amount"]), minutes=minutes, where=where)


def pay_address_msg(pay_address: str) -> str:
    return f"<code>{pay_address}</code>"


STATUS = {
    "awaiting": "⏳ 입금 대기", "paid": "💰 입금 확인 · 처리 중", "ordering": "⚙️ 에너지 보내는 중",
    "delivered": "⚙️ 도착 확인 중", "done": "✅ 완료", "expired": "⌛ 만료", "cancelled": "❌ 취소",
    "failed": "⚠️ 실패 · 관리자 확인", "review": "🔍 관리자 확인 중", "resolved": "☑️ 처리됨",
}


def order_status(o, pay_address: str = "", now: float = 0.0) -> str:
    if o["status"] == "awaiting":           # 결제 안내를 지우지 않도록 금액·기한을 그대로 다시 보여 준다
        left = max(1, int((o["expires_at"] - now) // 60)) if now else 1
        return order_created(o, pay_address, left, inline=True)
    lines = [f"🧾 <b>주문 #{o['id']}</b> — {STATUS.get(o['status'], o['status'])}\n",
             f"<blockquote>💼 <code>{esc(o['receiver'])}</code>",
             f"🔋 {o['energy']:,} 에너지 · 💰 {trx(o['amount'])} TRX"]
    if o["txid"]:
        lines.append(f"🔗 <a href=\"{SCAN_TX.format(o['txid'])}\">입금 거래</a>")
    if o["delegate_tx"]:
        lines.append(f"🔗 <a href=\"{SCAN_TX.format(o['delegate_tx'])}\">에너지 위임 거래</a>")
    if o["refund_to"]:
        lines.append(f"💸 환불 주소 <code>{esc(o['refund_to'])}</code>")
    return "\n".join(lines) + "</blockquote>"


def orders_text(rows) -> str:
    if not rows:
        return "📋 <b>내 주문</b>\n\n아직 주문이 없습니다."
    body = "\n".join(f"#{o['id']} · {STATUS.get(o['status'], o['status'])} · {o['energy']:,} · {trx(o['amount'])} TRX"
                     for o in rows)
    return f"📋 <b>내 주문</b> (최근 {len(rows)}건)\n\n<blockquote>{body}</blockquote>"


# ───────────── 진행 알림 ─────────────

def paid(o) -> str:
    return (f"💰 <b>주문 #{o['id']}</b> 입금을 확인했습니다. 에너지를 보내는 중입니다.\n"
            "<blockquote>⚠️ <b>✅ 에너지 도착</b> 메시지를 받은 뒤에 USDT를 보내 주십시오.</blockquote>")


def done(o, energy: int) -> str:
    link = f"\n🔗 <a href=\"{SCAN_TX.format(o['delegate_tx'])}\">에너지 위임 거래</a>" if o["delegate_tx"] else ""
    return (f"✅ <b>에너지 도착</b> — 주문 #{o['id']}\n\n"
            f"<blockquote>💼 <code>{esc(o['receiver'])}</code>\n"
            f"⚡ 지금 쓸 수 있는 에너지 <b>{energy:,}</b>{link}</blockquote>\n\n"
            "👉 <b>지금 USDT를 보내셔도 됩니다.</b>\n"
            "⏰ 에너지는 <u>1시간 안에</u> 쓰셔야 합니다. 그 뒤에는 회수됩니다.\n"
            "💡 대역폭이 부족하면 TRX가 약 0.35개 따로 나갈 수 있습니다.")


def mismatch(o, value: int) -> str:
    return (f"⚠️ <b>주문 #{o['id']}</b> 금액이 다릅니다.\n\n"
            f"<blockquote>주문 금액 <b>{trx(o['amount'])} TRX</b> · 도착 금액 <b>{trx(value)} TRX</b></blockquote>\n"
            "자동으로 처리할 수 없어서 관리자에게 전달했습니다.\n"
            "아래 버튼으로 <b>환불받을 본인 지갑 주소</b>를 보내 주시면 관리자가 확인 후 환불해 드립니다.")


def expired(o, late_minutes: int) -> str:
    return (f"⌛ <b>주문 #{o['id']}</b>의 입금 시간이 지났습니다.\n"
            f"<blockquote>이미 보내셨다면 <b>{late_minutes}분 안에</b> 정확한 금액이 도착하는 경우 그대로 처리해 드립니다.\n"
            "아직 안 보내셨다면 <b>보내지 마시고</b> 새로 주문해 주십시오. (가격이 바뀌었을 수 있습니다)</blockquote>")


def failed(o, support: str = "") -> str:
    return (f"⚠️ <b>주문 #{o['id']}</b> 에너지를 보내지 못했습니다.\n"
            "<blockquote>공급처가 모두 응답하지 않습니다. 관리자가 확인 후 <b>환불</b>해 드리겠습니다.\n"
            "아래 버튼으로 <b>환불받을 본인 지갑 주소</b>를 보내 주십시오. (거래소에서 보내셨다면 거래소 주소가 아닌 "
            f"<b>개인 지갑 주소</b>로 받으셔야 합니다. 문의: {contact(support)})\n"
            "⚠️ <b>USDT는 아직 보내지 마십시오.</b> 지금 보내면 수수료로 TRX가 탑니다.</blockquote>")


def review(o) -> str:
    return (f"🔍 <b>주문 #{o['id']}</b> 처리 결과를 확인하고 있습니다.\n"
            "<blockquote>공급처 응답이 늦어지고 있습니다. 1시간 안에 에너지가 들어오면 <b>바로 알려 드리고</b>, "
            "그렇지 않으면 관리자가 확인한 결과를 이 대화로 알려 드립니다.\n"
            "⚠️ <b>✅ 에너지 도착</b> 메시지를 받기 전에는 USDT를 보내지 마십시오.</blockquote>")


def slow(o) -> str:
    return (f"🔍 <b>주문 #{o['id']}</b> 에너지 도착이 늦어지고 있습니다.\n"
            "<blockquote>1시간 안에 에너지가 들어오면 바로 알려 드리고, 그렇지 않으면 관리자가 확인한 결과를 알려 드립니다.\n"
            f"직접 보시려면 <b>/조회</b> <code>{esc(o['receiver'])}</code>\n"
            "⚠️ 에너지가 보이지 않으면 USDT를 보내지 마십시오.</blockquote>")


def resolved(o, note: str) -> str:
    memo = f"\n<blockquote>{esc(note)}</blockquote>" if note else ""
    return f"☑️ <b>주문 #{o['id']}</b> 관리자 확인이 끝났습니다.{memo}"


ASK_REFUND = ("💸 <b>환불받을 주소</b>\n\n환불받으실 <b>본인 TRON 지갑 주소</b>(<code>T</code>로 시작)를 보내 주십시오.\n"
              "<blockquote>⚠️ 거래소 입금 주소는 메모 없이 받으면 돈을 찾지 못할 수 있습니다. 개인 지갑 주소를 보내 주십시오.</blockquote>\n"
              "취소는 /cancel")
REFUND_NOT_ALLOWED = "이 주문은 환불 주소를 받을 수 없는 상태입니다."
STALE = "이 단계는 끝났습니다. ⚡ 에너지 사기를 다시 눌러 주십시오."


def refund_saved(o) -> str:
    return (f"✅ <b>주문 #{o['id']}</b> 환불 주소를 받았습니다.\n<blockquote><code>{esc(o['refund_to'])}</code></blockquote>\n"
            "관리자가 확인 후 환불하고 이 대화로 알려 드립니다.")


def admin_message(o, text: str) -> str:
    return f"📩 <b>관리자 메시지</b> — 주문 #{o['id']}\n\n<blockquote>{esc(text)}</blockquote>"


# ───────────── 관리자 ─────────────

def admin_unknown(d: Deposit, why: str, o=None, username: str | None = None) -> str:
    who = f"\n손님 {user_tag(o['user_id'], username)} · 주문 금액 {trx(o['amount'])} TRX" if o is not None else ""
    return (f"💸 <b>에너지 결제 주소 입금</b> — {esc(why)}\n"
            f"<blockquote>{money.fmt(d.value)} {d.token} · 보낸 주소 <code>{esc(d.sender)}</code>{who}\n"
            f"<a href=\"{SCAN_TX.format(d.txid)}\">거래 보기</a></blockquote>")


def _order_lines(o, username: str | None) -> str:
    refund = f"\n💸 손님이 알려 준 환불 주소 <code>{esc(o['refund_to'])}</code>" if o["refund_to"] else ""
    return (f"손님 {user_tag(o['user_id'], username)}\n"
            f"받을 지갑 <code>{esc(o['receiver'])}</code> · {o['energy']:,}\n"
            f"받은 금액 {trx(o['amount'])} TRX · 보낸 주소 <code>{esc(o['payer'] or '-')}</code>\n"
            f"입금 <code>{esc(o['txid'] or '-')}</code>{refund}")


def admin_review(o, supplier: str, why: str, username: str | None = None) -> str:
    return (f"🔍 <b>에너지 주문 #{o['id']} 확인 필요</b> ({esc(supplier)})\n"
            f"<blockquote>{esc(why)[:300]}\n{_order_lines(o, username)}</blockquote>\n"
            "1시간 안에 체인에서 에너지가 보이면 자동으로 완료됩니다. 공급처에서 접수 여부를 확인한 뒤 "
            f"<code>/energy_done {o['id']} 메모</code>로 정리해 주십시오. (메모는 손님에게도 보입니다)")


def admin_failed(o, why: str, username: str | None = None) -> str:
    return (f"⚠️ <b>에너지 주문 #{o['id']} 실패 · 환불 필요</b>\n"
            f"<blockquote>{esc(why)}\n{_order_lines(o, username)}</blockquote>\n"
            "⚠️ 보낸 주소가 거래소일 수 있습니다. <b>손님이 알려 준 환불 주소</b>로 보내 주십시오(손님에게 주소를 보내 달라고 안내했습니다).\n"
            f"환불한 뒤 <code>/energy_done {o['id']} 환불했습니다</code>로 정리해 주십시오. (메모는 손님에게도 보입니다)")


def admin_refund_to(o, username: str | None = None) -> str:
    return (f"💸 <b>에너지 주문 #{o['id']} 환불 주소 도착</b> ({STATUS.get(o['status'], o['status'])})\n"
            f"<blockquote>{_order_lines(o, username)}</blockquote>")


def admin_auto_done(o) -> str:
    return (f"✅ <b>에너지 주문 #{o['id']}</b> 확인 필요였던 주문이 체인에서 에너지가 보여 자동으로 완료되었습니다. "
            "환불하지 마십시오.")


def admin_low(name: str, bal: int | None) -> str:
    left = f" (남은 잔고 {trx(bal)} TRX)" if bal is not None else ""
    return f"🪫 <b>에너지 공급처 {esc(name)}</b> 잔고가 부족합니다{left}. 충전해 주십시오."


def admin_summary(stats: dict, balances: dict, attention, suppliers: list[str], pay_address: str) -> str:
    done_ = stats.get("done", {"n": 0, "amount": 0, "cost": 0})
    bal = "\n".join(f"• {esc(n)} — " + (f"{trx(balances[n])} TRX" if balances.get(n) is not None else "확인 전/실패")
                    for n in suppliers) or "• 없음"
    counts = " · ".join(f"{STATUS.get(k, k)} {v['n']}" for k, v in sorted(stats.items())) or "주문 없음"
    att = "\n".join(f"#{o['id']} {STATUS.get(o['status'])} · {esc(o['note'][:60])}"
                    + (f" · 환불 주소 <code>{esc(o['refund_to'])}</code>" if o["refund_to"] else "")
                    for o in attention) or "없음"
    return ("⚡ <b>에너지 임대 현황</b> (최근 24시간)\n\n"
            f"<blockquote>{counts}\n완료 매출 {trx(done_['amount'])} TRX · 원가 {trx(done_['cost'])} TRX</blockquote>\n\n"
            f"💳 <b>공급처 잔고</b>\n<blockquote>{bal}</blockquote>\n\n"
            f"📥 결제 주소 <code>{esc(pay_address)}</code>\n\n"
            f"🔍 <b>확인 필요</b>\n<blockquote>{att}</blockquote>\n\n"
            "<code>/energy_done 주문번호 메모</code> 정리(손님에게 알림) · <code>/energy_msg 주문번호 내용</code> 손님에게 메시지")
