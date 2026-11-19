"""Stock levels, and stock held for orders that haven't shipped."""
from __future__ import annotations


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self.stock: dict[str, int] = dict(stock or {})
        self.held: dict[str, int] = {}

    def available(self, sku: str) -> int:
        return self.stock.get(sku, 0) - self.held.get(sku, 0)

    def hold(self, sku: str, qty: int) -> None:
        """Set aside qty of a SKU for an order; raises OutOfStock if there isn't enough."""
        if qty <= 0:
            raise ValueError("quantity must be positive")
        if self.available(sku) < qty:
            raise OutOfStock(f"{sku}: wanted {qty}, have {self.available(sku)}")
        self.held[sku] = self.held.get(sku, 0) + qty

    def release(self, sku: str, qty: int) -> None:
        self.held[sku] = max(0, self.held.get(sku, 0) - qty)

    def ship(self, sku: str, qty: int) -> None:
        """Held stock leaves the warehouse."""
        if self.held.get(sku, 0) < qty:
            raise ValueError(f"{sku}: only {self.held.get(sku, 0)} held")
        self.held[sku] -= qty
        self.stock[sku] -= qty

    def restock(self, sku: str, qty: int) -> None:
        self.stock[sku] = self.stock.get(sku, 0) + qty

    def low(self, threshold: int = 3) -> list[str]:
        return sorted(sku for sku in self.stock if self.available(sku) <= threshold)

    def report(self) -> str:
        return "\n".join(f"{sku}: {self.stock[sku]} in stock, {self.held.get(sku, 0)} held"
                         for sku in sorted(self.stock))
