"""Product and stock operations. The caller owns the database transaction."""
import sqlite3

from fastapi import HTTPException

from .schemas import Adjustment, MAX_STOCK, ProductCreate, ProductPatch


def get_product(db: sqlite3.Connection, product_id: int) -> dict:
    row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Product not found")
    product = dict(row)
    product["active"] = bool(product["active"])
    return product


def list_products(db: sqlite3.Connection, *, limit=50, offset=0, active=True, q=None, low_stock=None) -> dict:
    clauses, values = [], []
    if active is not None:
        clauses.append("active=?")
        values.append(int(active))
    if q is not None:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(name LIKE ? ESCAPE '\\' OR sku LIKE ? ESCAPE '\\')")
        values.extend(["%" + escaped + "%"] * 2)
    if low_stock is not None:
        clauses.append("available<=?")
        values.append(low_stock)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = db.execute("SELECT COUNT(*) FROM products" + where, values).fetchone()[0]
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM products" + where + " ORDER BY id LIMIT ? OFFSET ?", (*values, limit, offset))]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def add_movement(db: sqlite3.Connection, product_id: int, delta: int, kind: str,
                 reason: str, order_id: int | None = None) -> dict:
    balance = get_product(db, product_id)["available"]
    cursor = db.execute(
        "INSERT INTO stock_ledger(product_id,delta,balance_after,kind,reason,order_id) VALUES(?,?,?,?,?,?)",
        (product_id, delta, balance, kind, reason, order_id))
    return dict(db.execute("SELECT * FROM stock_ledger WHERE id=?", (cursor.lastrowid,)).fetchone())


def change_stock(db: sqlite3.Connection, product_id: int, delta: int) -> None:
    changed = db.execute(
        "UPDATE products SET available=available+?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id=? AND available+? BETWEEN 0 AND ?", (delta, product_id, delta, MAX_STOCK))
    if changed.rowcount != 1:
        raise HTTPException(409, "Insufficient stock or stock capacity exceeded")


def create_product(db: sqlite3.Connection, body: ProductCreate) -> dict:
    try:
        cursor = db.execute("INSERT INTO products(sku,name,price_cents,available) VALUES(?,?,?,?)",
                            (body.sku, body.name, body.price_cents, body.initial_stock))
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "SKU already exists") from error
    product_id = cursor.lastrowid
    if body.initial_stock:
        add_movement(db, product_id, body.initial_stock, "opening", "Opening balance")
    return get_product(db, product_id)


def update_product(db: sqlite3.Connection, product_id: int, body: ProductPatch) -> dict:
    get_product(db, product_id)
    fields = body.model_dump(exclude_unset=True)
    # Field names come only from the closed request model, not arbitrary input.
    assignments = ",".join(field + "=?" for field in fields)
    db.execute("UPDATE products SET " + assignments + ",updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
               (*fields.values(), product_id))
    return get_product(db, product_id)


def archive_product(db: sqlite3.Connection, product_id: int) -> dict:
    get_product(db, product_id)
    db.execute("UPDATE products SET active=0,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (product_id,))
    return get_product(db, product_id)


def adjust_stock(db: sqlite3.Connection, product_id: int, body: Adjustment) -> dict:
    get_product(db, product_id)
    change_stock(db, product_id, body.delta)
    movement = add_movement(db, product_id, body.delta, "adjustment", body.reason)
    return {"product": get_product(db, product_id), "movement": movement}


def list_movements(db: sqlite3.Connection, product_id: int, *, limit=50, offset=0) -> dict:
    get_product(db, product_id)
    rows = db.execute("SELECT * FROM stock_ledger WHERE product_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                      (product_id, limit, offset))
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}
