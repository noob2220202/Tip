"""USDT·TRX 금액은 정수(마이크로 단위, 1 = 1,000,000)로 다룬다. 부동소수점은 금액에 쓰지 않는다."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

UNIT = 10**6


def parse(text: str) -> int | None:
    """'0.5', '1,000', '12' → 마이크로 단위. 숫자가 아니거나 음수·소수 7자리 이상이면 None. 0은 허용(필터 끄기)."""
    try:
        d = Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        return None
    micro = d * UNIT
    if not d.is_finite() or micro != micro.to_integral_value() or micro < 0:
        return None
    return int(micro)


def fmt(micro: int) -> str:
    """12.5 → '12.50', 0.123456 → '0.123456'. 소수 둘째 자리까지는 항상 보여주고 뒤쪽 0은 줄인다."""
    whole, _, frac = format(Decimal(micro) / UNIT, "f").partition(".")
    frac = frac.rstrip("0").ljust(2, "0")
    return f"{int(whole):,}.{frac}"


def won(micro: int, price: float) -> int:
    """토큰 수량(마이크로)을 원화(원 단위 반올림)로."""
    return int((Decimal(micro) * Decimal(str(price)) / UNIT).quantize(Decimal(1)))


# ---- /환율 입력 ----

_UNITS = {"원": "KRW", "krw": "KRW", "₩": "KRW", "usdt": "USDT", "테더": "USDT", "trx": "TRX", "트론": "TRX",
          "달러": "USD", "usd": "USD", "$": "USD", "불": "USD"}
_SCALE = {"천": 10**3, "만": 10**4, "억": 10**8}
_AMOUNT = re.compile(r"^([$₩]?)([0-9]+(?:\.[0-9]+)?)(천|만|억)?(원|krw|₩|usdt|테더|trx|트론|달러|usd|\$|불)?$")
AMOUNT_MAX = Decimal(10) ** 13


def parse_amount(text: str) -> tuple[Decimal, str] | None:
    """'100' → (100, USDT), '10만원'·'100000원' → (100000, KRW), '50trx' → (50, TRX), '$20' → (20, USD).

    단위가 없으면 USDT로 본다. 만·억처럼 큰 수 단위만 붙으면 원으로 본다. 해석할 수 없으면 None.
    """
    s = text.strip().lower().replace(",", "").replace(" ", "")
    m = _AMOUNT.match(s)
    if not m:
        return None
    prefix, num, scale, unit = m.groups()
    value = Decimal(num) * _SCALE.get(scale or "", 1)
    if prefix and unit and _UNITS[prefix] != _UNITS[unit]:
        return None
    name = _UNITS.get(unit or prefix or "", "KRW" if scale else "USDT")
    if value <= 0 or value > AMOUNT_MAX:
        return None
    return value, name
