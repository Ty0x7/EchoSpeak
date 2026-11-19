"""A shopper's cart: SKUs and quantities, and at most one discount code."""
from __future__ import annotations


class Cart:
    def __init__(self) -> None:
        self.lines: dict[str, int] = {}
        self.code: str | None = None

    def add(self, sku: str, qty: int = 1) -> None:
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self.lines[sku] = self.lines.get(sku, 0) + qty

    def remove(self, sku: str, qty: int | None = None) -> None:
        if sku not in self.lines:
            return
        if qty is None or qty >= self.lines[sku]:
            del self.lines[sku]
        else:
            self.lines[sku] -= qty

    def apply_code(self, code: str) -> None:
        """Codes are case-insensitive for shoppers; they're kept in one form."""
        self.code = code.strip().upper() or None

    def items(self) -> list[tuple[str, int]]:
        """(sku, quantity) pairs, in the order they were added."""
        return list(self.lines.items())

    def count(self) -> int:
        return sum(self.lines.values())

    def is_empty(self) -> bool:
        return not self.lines

    def clear(self) -> None:
        self.lines.clear()
        self.code = None
