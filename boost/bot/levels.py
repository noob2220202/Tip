"""부스트 레벨 계산기.

필요 부스트 수 = 계수 × 레벨별 배율. 계수는 구독자(멤버) 수에서 나온다.
이 공식은 2025년 5월 이후 규칙을 정리한 서드파티 자료 기준이라 텔레그램이 바꿀 수 있다.
그래서 값은 config/products.toml의 [levels]에서 바꿀 수 있고, 안내문에도 '채널의 부스트 화면 수치가 우선'이라고 적는다.
"""
from __future__ import annotations

from dataclasses import dataclass


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
