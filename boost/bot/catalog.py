"""config/products.toml: 판매 상품과 레벨 계산 규칙."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from bot.money import to_micro

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "products.toml"


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    service_id: int      # SMM 패널 서비스 ID. 0이면 아직 설정 전이라 판매하지 않는다
    price_micro: int     # 부스트 1개당 가격
    days: int = 0
    description: str = ""

    @property
    def enabled(self) -> bool:
        return self.service_id > 0


@dataclass(frozen=True)
class Catalog:
    products: tuple[Product, ...]
    level_target: int = 10
    level_divisor: int = 250
    min_qty: int = 10        # 한 주문의 최소/최대 부스트 개수 ([order] 로 바꿀 수 있다)
    max_qty: int = 1000

    def sellable(self) -> list[Product]:
        return [p for p in self.products if p.enabled]

    def get(self, pid: str) -> Product | None:
        return next((p for p in self.products if p.id == pid), None)


def load_catalog(path: Path | str | None = None) -> Catalog:
    path = Path(path or os.environ.get("PRODUCTS_CONFIG") or DEFAULT_PATH)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    products, seen = [], set()
    for raw in data.get("products", []):
        p = Product(
            id=str(raw["id"]), name=raw["name"], service_id=int(raw["service_id"]),
            price_micro=to_micro(raw["price"]),
            days=int(raw.get("days", 0)), description=raw.get("description", ""),
        )
        if p.id in seen:
            raise ValueError(f"상품 설정이 올바르지 않아요: {p.id}")
        seen.add(p.id)
        products.append(p)
    lv = data.get("levels", {})
    od = data.get("order", {})
    cat = Catalog(tuple(products), int(lv.get("target", 10)), int(lv.get("divisor", 250)),
                  int(od.get("min", 10)), int(od.get("max", 1000)))
    if not 1 <= cat.min_qty <= cat.max_qty:
        raise ValueError("[order] min/max 설정이 올바르지 않아요")
    return cat
