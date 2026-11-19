"""Subtotals, discount codes and tax."""
from __future__ import annotations

from shop.catalog import Catalog

# Discount codes and the share of the subtotal each takes off.
CODES = {"save10": 0.10, "welcome5": 0.05, "staff25": 0.25}
TAX_RATE = 0.05


def subtotal(items: list[tuple[str, int]], catalog: Catalog) -> int:
    """Cents for a list of (sku, quantity) pairs."""
    total = 0
    for sku, qty in items:
        total += catalog.get(sku).price_cents * qty
    return total


def discount(subtotal_cents: int, code: str | None) -> int:
    """Cents taken off for a discount code (0 for no code or an unknown one)."""
    if not code:
        return 0
    rate = CODES.get(code, 0.0)
    return round(subtotal_cents * rate)


def tax(items: list[tuple[str, int]], catalog: Catalog, discount_cents: int = 0) -> int:
    """Tax on the taxable part, after its share of the discount."""
    taxable = sum(catalog.get(sku).price_cents * qty for sku, qty in items if catalog.get(sku).taxable)
    whole = subtotal(items, catalog)
    share = round(discount_cents * taxable / whole) if whole else 0
    return round((taxable - share) * TAX_RATE)


def describe_code(code: str) -> str:
    rate = CODES.get(code)
    return f"{code}: {int(rate * 100)}% off" if rate else f"{code}: not a valid code"
