"""Turning a cart into an order: hold the stock, price it, record it."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from shop import pricing
from shop.cart import Cart
from shop.catalog import Catalog
from shop.inventory import Inventory

_ORDER_IDS = itertools.count(1001)


@dataclass
class Order:
    id: int
    items: list[tuple[str, int]]
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    code: str | None = None
    status: str = "placed"
    notes: list[str] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents + self.tax_cents

    def total(self) -> float:
        return self.total_cents / 100

    def receipt(self, catalog: Catalog) -> str:
        lines = [f"Order {self.id}"]
        for sku, qty in self.items:
            product = catalog.get(sku)
            lines.append(f"  {qty} x {product.name:<20} {product.price_cents * qty / 100:>8.2f}")
        lines.append(f"  {'subtotal':<24} {self.subtotal_cents / 100:>8.2f}")
        if self.discount_cents:
            lines.append(f"  {'discount (' + str(self.code) + ')':<24} {-self.discount_cents / 100:>8.2f}")
        lines.append(f"  {'tax':<24} {self.tax_cents / 100:>8.2f}")
        lines.append(f"  {'total':<24} {self.total():>8.2f}")
        return "\n".join(lines)


class Checkout:
    def __init__(self, catalog: Catalog, inventory: Inventory) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self.orders: list[Order] = []

    def place(self, cart: Cart) -> Order:
        """Hold stock for every line, price the cart and record the order. Nothing is held if a line fails."""
        if cart.is_empty():
            raise ValueError("the cart is empty")
        held: list[tuple[str, int]] = []
        try:
            for sku, qty in cart.items():
                self.inventory.reserve(sku, qty)
                held.append((sku, qty))
        except Exception:
            for sku, qty in held:
                self.inventory.release(sku, qty)
            raise
        sub = pricing.subtotal(cart.lines, self.catalog)
        off = pricing.discount(sub, cart.code)
        order = Order(next(_ORDER_IDS), cart.items(), sub, off, pricing.tax(cart.items(), self.catalog, off), cart.code)
        self.orders.append(order)
        cart.clear()
        return order

    def ship(self, order: Order) -> None:
        for sku, qty in order.items:
            self.inventory.ship(sku, qty)
        order.status = "shipped"

    def cancel(self, order: Order) -> None:
        if order.status != "placed":
            raise ValueError(f"order {order.id} is already {order.status}")
        for sku, qty in order.items:
            self.inventory.release(sku, qty)
        order.status = "cancelled"
