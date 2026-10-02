"""Orders: cart checkout and total price calculation with discounts."""

from dataclasses import dataclass, field


@dataclass
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


@dataclass
class Order:
    user_id: int
    items: list[LineItem] = field(default_factory=list)

    def total_cents(self, discount_percent: int = 0) -> int:
        subtotal = sum(i.unit_price_cents * i.quantity for i in self.items)
        return subtotal - subtotal * discount_percent // 100


def checkout(order: Order, payment_gateway) -> str:
    """Charge the user's card for the order total; returns the payment id."""
    if not order.items:
        raise ValueError("cannot checkout an empty order")
    return payment_gateway.charge(order.user_id, order.total_cents())
