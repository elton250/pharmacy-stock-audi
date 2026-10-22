"""Expected-stock calculations from inventory transactions."""
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session as DbSession

from app.models.inventory import SIGN, InventoryTransaction, TransactionType

POSITIVE_TYPES = [t.name for t, sign in SIGN.items() if sign == 1]
NEGATIVE_TYPES = [t.name for t, sign in SIGN.items() if sign == -1]


def expected_stock(db: DbSession, medicine_id: int) -> Decimal:
    """Expected Stock = Opening + Received + ReturnsIn + PositiveAdj
    − Issued − ReturnsOut − NegativeAdj."""
    signed = case(
        (InventoryTransaction.transaction_type.in_(POSITIVE_TYPES), InventoryTransaction.quantity),
        else_=-InventoryTransaction.quantity,
    )
    total = db.execute(
        select(func.coalesce(func.sum(signed), 0)).where(InventoryTransaction.medicine_id == medicine_id)
    ).scalar_one()
    return Decimal(total)


def expected_stock_breakdown(db: DbSession, medicine_id: int) -> dict[str, Decimal]:
    rows = db.execute(
        select(
            InventoryTransaction.transaction_type,
            func.coalesce(func.sum(InventoryTransaction.quantity), 0),
        )
        .where(InventoryTransaction.medicine_id == medicine_id)
        .group_by(InventoryTransaction.transaction_type)
    ).all()
    breakdown = {t.name: Decimal("0") for t in TransactionType}
    for type_name, total in rows:
        breakdown[type_name] = Decimal(total)
    return breakdown
