from shop.cart import Cart
from shop.catalog import demo_catalog
from shop.checkout import Checkout
from shop.inventory import Inventory


def _shop():
    return Checkout(demo_catalog(), Inventory({"W-100": 10, "G-200": 4, "B-300": 50, "S-400": 2}))


def test_placing_an_order_holds_the_stock():
    shop = _shop()
    cart = Cart()
    cart.add("W-100", 2)
    shop.place(cart)
    assert shop.inventory.available("W-100") == 8


def test_the_order_is_priced_from_the_catalog():
    shop = _shop()
    cart = Cart()
    cart.add("W-100", 2)
    cart.add("G-200", 1)
    order = shop.place(cart)
    assert order.subtotal_cents == 1900
    assert order.total_cents == 1900 + 95


def test_a_discount_code_works_whatever_its_case():
    shop = _shop()
    cart = Cart()
    cart.add("G-200", 2)
    cart.apply_code("Save10")
    order = shop.place(cart)
    assert order.discount_cents == 200
    assert order.total_cents == 2000 - 200 + 90
