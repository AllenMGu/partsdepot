"""Live warehouse-level stock status shared by stock and alert queries."""

from sqlalchemy import and_, case, func

from core.models import Goods, Location, Stock


def stock_totals(db, warehouse_id):
    # As in the inventory listing, book stock at disabled locations is included.
    return (
        db.query(Stock.goods_id, func.sum(Stock.quantity).label("quantity"))
        .join(Location, and_(Location.id == Stock.location_id, Location.warehouse_id == warehouse_id))
        .filter(Stock.warehouse_id == warehouse_id)
        .group_by(Stock.goods_id)
        .subquery()
    )


def alert_status(quantity):
    return case(
        (~Goods.stock_alert_enabled | Goods.min_stock_quantity.is_(None), "unmonitored"),
        (quantity == 0, "out_of_stock"),
        (quantity <= Goods.min_stock_quantity, "low"),
        else_="normal",
    )
