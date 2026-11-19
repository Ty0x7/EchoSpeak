"""What the shop sells: products by SKU, with prices in cents."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int
    category: str = "general"
    taxable: bool = True

    def price(self) -> float:
        return self.price_cents / 100


class Catalog:
    def __init__(self, products: list[Product] | None = None) -> None:
        self._products: dict[str, Product] = {}
        for product in products or []:
            self.add(product)

    def add(self, product: Product) -> None:
        if product.sku in self._products:
            raise ValueError(f"duplicate SKU {product.sku}")
        self._products[product.sku] = product

    def get(self, sku: str) -> Product:
        try:
            return self._products[sku]
        except KeyError:
            raise KeyError(f"unknown SKU {sku!r}") from None

    def __contains__(self, sku: str) -> bool:
        return sku in self._products

    def in_category(self, category: str) -> list[Product]:
        return sorted((p for p in self._products.values() if p.category == category), key=lambda p: p.name)

    def search(self, text: str) -> list[Product]:
        words = text.lower().split()
        return [p for p in self._products.values() if all(w in p.name.lower() for w in words)]


def demo_catalog() -> Catalog:
    return Catalog([
        Product("W-100", "Blue widget", 450, "parts"),
        Product("G-200", "Gadget deluxe", 1000, "gadgets"),
        Product("B-300", "Gift card", 2500, "cards", taxable=False),
        Product("S-400", "Spare springs (10)", 199, "parts"),
    ])
