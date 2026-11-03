"""All-or-nothing order reservations and repeat-safe cancellation."""
import sqlite3

from fastapi import HTTPException

from .inventory import add_movement, change_stock, get_product
from .schemas import OrderCreate


def get_order(db: sqlite3.Connection, order_id: int) -> dict:
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    order = dict(row)
    order["items"] = [dict(item) for item in db.execute(
        "SELECT product_id,sku,name,quantity,unit_price_cents FROM order_items WHERE order_id=? ORDER BY product_id",
        (order_id,))]
    return order


def reserve_order(db: sqlite3.Connection, body: OrderCreate) -> dict:
    """Caller holds a write transaction so every line succeeds together."""
    order_id = db.execute("INSERT INTO orders(customer_reference,status,total_cents) VALUES(?,'reserved',0)",
                          (body.customer_reference,)).lastrowid
    total = 0
    for item in sorted(body.items, key=lambda line: line.product_id):
        product = get_product(db, item.product_id)
        if not product["active"]:
            raise HTTPException(409, "Cannot reserve an inactive product")
        change_stock(db, item.product_id, -item.quantity)
        db.execute("INSERT INTO order_items(order_id,product_id,sku,name,quantity,unit_price_cents) VALUES(?,?,?,?,?,?)",
                   (order_id, item.product_id, product["sku"], product["name"], item.quantity, product["price_cents"]))
        add_movement(db, item.product_id, -item.quantity, "reserve", "Order reservation", order_id)
        total += item.quantity * product["price_cents"]
    db.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
    return get_order(db, order_id)


def list_orders(db: sqlite3.Connection, *, limit=50, offset=0, status=None, customer_reference=None) -> dict:
    clauses, values = [], []
    if status is not None:
        clauses.append("status=?")
        values.append(status)
    if customer_reference is not None:
        clauses.append("customer_reference=?")
        values.append(customer_reference)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = db.execute("SELECT COUNT(*) FROM orders" + where, values).fetchone()[0]
    rows = db.execute("SELECT * FROM orders" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", (*values, limit, offset))
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def cancel_order(db: sqlite3.Connection, order_id: int) -> dict:
    order = get_order(db, order_id)
    if order["status"] == "cancelled":
        return order
    for item in order["items"]:
        change_stock(db, item["product_id"], item["quantity"])
        add_movement(db, item["product_id"], item["quantity"], "release", "Order cancelled", order_id)
    db.execute("UPDATE orders SET status='cancelled',cancelled_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
               (order_id,))
    return get_order(db, order_id)
