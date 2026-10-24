"""USDT 금액은 항상 정수(마이크로 단위, 1 USDT = 1,000,000)로 다룬다. 부동소수점은 쓰지 않는다."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

UNIT = 10**6


def to_micro(value: str | int | float | Decimal) -> int:
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f"금액이 올바르지 않아요: {value!r}") from e
    micro = d * UNIT
    if micro != micro.to_integral_value() or micro <= 0:
        raise ValueError(f"금액은 소수 6자리 이하의 양수여야 해요: {value!r}")
    return int(micro)


def fmt(micro: int) -> str:
    """12.5 USDT -> '12.50'. 소수 둘째 자리까지는 항상 보여주고, 더 필요하면 그만큼 보여준다."""
    s = format(Decimal(micro) / UNIT, "f")
    whole, _, frac = s.partition(".")
    frac = frac.rstrip("0").ljust(2, "0")
    return f"{whole}.{frac}"
