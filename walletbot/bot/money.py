"""USDT·TRX 금액은 정수(마이크로 단위, 1 = 1,000,000)로 다룬다. 부동소수점은 금액에 쓰지 않는다."""
from __future__ import annotations

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
