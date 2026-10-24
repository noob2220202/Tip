"""부스트 레벨 계산기.

필요 부스트 수 = 계수 × 레벨별 배율. 계수는 구독자(멤버) 수에서 나온다.
이 공식은 2025년 5월 이후 규칙을 정리한 서드파티 자료 기준이라 텔레그램이 바꿀 수 있다.
그래서 값은 config/products.toml의 [levels]에서 바꿀 수 있고, 안내문에도 '채널의 부스트 화면 수치가 우선'이라고 적는다.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.catalog import Product

MAX_NEED = 50_000   # 조합 계산 상한


@dataclass(frozen=True)
class LevelRule:
    target: int = 10
    divisor: int = 250        # 계수 = 구독자 // divisor + 1


def coefficient(subscribers: int, rule: LevelRule) -> int:
    return subscribers // rule.divisor + 1


def boosts_required(level: int, subscribers: int, rule: LevelRule = LevelRule()) -> int:
    c = coefficient(subscribers, rule)
    if level <= 8:
        return -(-c * level // 2)      # 올림
    if level == 9:
        return c * 7
    return c * level


@dataclass(frozen=True)
class Bundle:
    items: tuple[tuple[Product, int], ...]   # (상품, 개수)
    boosts: int
    cost_micro: int


def best_bundle(products: list[Product], need: int) -> Bundle | None:
    """need개 이상을 가장 싸게 채우는 상품 조합 (무제한 배낭 DP). need가 너무 크면 None."""
    if need <= 0 or need > MAX_NEED or not products:
        return None
    top = need + max(p.quantity for p in products)
    inf = float("inf")
    cost = [inf] * (top + 1)
    pick: list[Product | None] = [None] * (top + 1)
    cost[0] = 0
    for q in range(1, top + 1):
        for p in products:
            if p.quantity <= q and cost[q - p.quantity] + p.price_micro < cost[q]:
                cost[q] = cost[q - p.quantity] + p.price_micro
                pick[q] = p
    # need 이상 중 가장 싼 것 (같으면 적게 사는 쪽)
    best = min(range(need, top + 1), key=lambda q: (cost[q], q))
    if cost[best] == inf:
        return None
    counts: dict[str, list] = {}
    q = best
    while q > 0:
        p = pick[q]
        counts.setdefault(p.id, [p, 0])[1] += 1
        q -= p.quantity
    items = tuple((p, n) for p, n in sorted(counts.values(), key=lambda x: -x[0].quantity))
    return Bundle(items=items, boosts=best, cost_micro=int(cost[best]))
