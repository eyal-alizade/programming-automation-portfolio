"""StockPilot: a local inventory/reservation API, with explicit SQLite transactions."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path as ApiPath, Query
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_STOCK = 1_000_000_000
MAX_ID = 2**63 - 1
PositiveId = Annotated[int, Field(strict=True, gt=0, le=MAX_ID)]
PathId = Annotated[int, ApiPath(gt=0, le=MAX_ID)]
Quantity = Annotated[int, Field(strict=True, gt=0, le=1_000_000)]
Money = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Stock = Annotated[int, Field(strict=True, ge=0, le=MAX_STOCK)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8,
                                     max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY,
 sku TEXT NOT NULL UNIQUE CHECK(length(sku) BETWEEN 1 AND 64),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 price_cents INTEGER NOT NULL CHECK(typeof(price_cents)='integer' AND price_cents BETWEEN 0 AND 1000000000),
 available INTEGER NOT NULL DEFAULT 0 CHECK(typeof(available)='integer' AND available BETWEEN 0 AND 1000000000),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY,
 customer_reference TEXT,
 status TEXT NOT NULL CHECK(status IN ('reserved','cancelled')),
 total_cents INTEGER NOT NULL CHECK(typeof(total_cents)='integer' AND total_cents>=0),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
 order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 sku TEXT NOT NULL,
 name TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
 unit_price_cents INTEGER NOT NULL CHECK(typeof(unit_price_cents)='integer' AND unit_price_cents>=0),
 PRIMARY KEY(order_id,product_id)
);
CREATE TABLE IF NOT EXISTS stock_ledger (
 id INTEGER PRIMARY KEY,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 delta INTEGER NOT NULL CHECK(typeof(delta)='integer' AND delta<>0),
 balance_after INTEGER NOT NULL CHECK(typeof(balance_after)='integer' AND balance_after BETWEEN 0 AND 1000000000),
 kind TEXT NOT NULL CHECK(kind IN ('opening','adjustment','reserve','release')),
 reason TEXT NOT NULL,
 order_id INTEGER REFERENCES orders(id) ON DELETE RESTRICT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(order_id,product_id,kind)
);
CREATE TABLE IF NOT EXISTS idempotency (
 scope TEXT NOT NULL,
 request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 response_json TEXT NOT NULL,
 status_code INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(scope,request_key)
);
CREATE INDEX IF NOT EXISTS ledger_product ON stock_ledger(product_id,id);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status,id);
PRAGMA user_version=1;
"""


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str) and (not value or any(ord(c) < 32 for c in value)):
            raise ValueError("Text must be nonblank and contain no control characters")
        return value


class ProductCreate(InputModel):
    sku: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    price_cents: Money
    initial_stock: Stock = 0


class ProductPatch(InputModel):
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    price_cents: Money | None = None
    active: Annotated[bool | None, Field(strict=True)] = None

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Provide at least one field; explicit null values are not allowed")
        return self


class Adjustment(InputModel):
    delta: Annotated[int, Field(strict=True, ge=-1_000_000, le=1_000_000)]
    reason: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("Adjustment must be nonzero")
        return value


class OrderItemCreate(InputModel):
    product_id: PositiveId
    quantity: Quantity


class OrderCreate(InputModel):
    customer_reference: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    items: Annotated[list[OrderItemCreate], Field(min_length=1, max_length=100)]

    @field_validator("items")
    @classmethod
    def unique_products(cls, value):
        if len({item.product_id for item in value}) != len(value):
            raise ValueError("Each product may appear only once per order")
        return value


class Product(BaseModel):
    id: int
    sku: str
    name: str
    price_cents: int
    available: int
    active: bool
    created_at: str
    updated_at: str


class Movement(BaseModel):
    id: int
    product_id: int
    delta: int
    balance_after: int
    kind: Literal["opening", "adjustment", "reserve", "release"]
    reason: str
    order_id: int | None
    created_at: str


class AdjustmentResult(BaseModel):
    product: Product
    movement: Movement


class OrderSummary(BaseModel):
    id: int
    customer_reference: str | None
    status: Literal["reserved", "cancelled"]
    total_cents: int
    created_at: str
    cancelled_at: str | None


class OrderLine(BaseModel):
    product_id: int
    sku: str
    name: str
    quantity: int
    unit_price_cents: int


class Order(OrderSummary):
    items: list[OrderLine]


class ProductPage(BaseModel):
    items: list[Product]
    total: int
    limit: int
    offset: int


class OrderPage(BaseModel):
    items: list[OrderSummary]
    total: int
    limit: int
    offset: int


class LedgerPage(BaseModel):
    items: list[Movement]
    limit: int
    offset: int


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database schema version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()


@contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def product_data(db, product_id: int) -> dict:
    row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Product not found")
    result = dict(row)
    result["active"] = bool(result["active"])
    return result


def order_data(db, order_id: int) -> dict:
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    result = dict(row)
    result["items"] = [dict(item) for item in db.execute(
        "SELECT product_id,sku,name,quantity,unit_price_cents FROM order_items WHERE order_id=? ORDER BY product_id",
        (order_id,))]
    return result


def add_movement(db, product_id, delta, kind, reason, order_id=None):
    balance = product_data(db, product_id)["available"]
    cursor = db.execute(
        "INSERT INTO stock_ledger(product_id,delta,balance_after,kind,reason,order_id) VALUES(?,?,?,?,?,?)",
        (product_id, delta, balance, kind, reason, order_id))
    return dict(db.execute("SELECT * FROM stock_ledger WHERE id=?", (cursor.lastrowid,)).fetchone())


def change_stock(db, product_id: int, delta: int) -> None:
    changed = db.execute(
        "UPDATE products SET available=available+?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id=? AND available+? BETWEEN 0 AND ?", (delta, product_id, delta, MAX_STOCK))
    if changed.rowcount != 1:
        raise HTTPException(409, "Insufficient stock or stock capacity exceeded")


def idempotent(database, scope, key, body, operation):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with database.connect() as db, transaction(db):
        old = db.execute("SELECT * FROM idempotency WHERE scope=? AND request_key=?", (scope, key)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise HTTPException(409, "Idempotency key was already used with a different request")
            return JSONResponse(json.loads(old["response_json"]), status_code=old["status_code"],
                                headers={"Idempotency-Replayed": "true"})
        result, status = operation(db)
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        db.execute("INSERT INTO idempotency(scope,request_key,request_hash,response_json,status_code) VALUES(?,?,?,?,?)",
                   (scope, key, digest, encoded, status))
        return JSONResponse(result, status_code=status, headers={"Idempotency-Replayed": "false"})


def csv_response(columns, rows, filename):
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return "" if value is None else value

    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_cell(row[column]) for column in columns])
    return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Content-Type-Options": "nosniff"})


def create_app(db_path: str | Path | None = None, api_key: str | None = None) -> FastAPI:
    """Uvicorn factory. Configuration is read at runtime, with no database side effect on import."""
    key = api_key if api_key is not None else os.environ.get("STOCKPILOT_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{24,200}", key):
        raise ValueError("Set STOCKPILOT_API_KEY to a random 24–200 character URL-safe token")
    database = Database(Path(db_path or os.environ.get("STOCKPILOT_DB", "data/stockpilot.sqlite3")))
    app = FastAPI(title="StockPilot API", version="1.0.0",
                  description="Personal portfolio inventory and reservation backend. Synthetic demos; not client work.")
    app.state.database = database
    header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authorize(supplied: Annotated[str | None, Depends(header)]):
        if supplied is None or not secrets.compare_digest(supplied.encode(), key.encode()):
            raise HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    @app.exception_handler(sqlite3.OperationalError)
    async def database_failure(_request, error):
        busy = any(word in str(error).lower() for word in ("locked", "busy"))
        return JSONResponse({"detail": "Database busy; retry with the same idempotency key" if busy
                             else "Database operation failed"}, status_code=503 if busy else 500,
                            headers={"Retry-After": "1"} if busy else {})

    @router.post("/products", response_model=Product, status_code=201, tags=["products"])
    def create_product(body: ProductCreate):
        with database.connect() as db, transaction(db):
            try:
                cursor = db.execute("INSERT INTO products(sku,name,price_cents,available) VALUES(?,?,?,?)",
                                    (body.sku, body.name, body.price_cents, body.initial_stock))
            except sqlite3.IntegrityError as error:
                raise HTTPException(409, "SKU already exists") from error
            product_id = cursor.lastrowid
            if body.initial_stock:
                add_movement(db, product_id, body.initial_stock, "opening", "Opening balance")
            return product_data(db, product_id)

    @router.get("/products", response_model=ProductPage, tags=["products"])
    def list_products(limit: Limit = 50, offset: Offset = 0, active: bool | None = True,
                      q: Annotated[str | None, Query(max_length=80)] = None,
                      low_stock: Annotated[int | None, Query(ge=0, le=MAX_STOCK)] = None):
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
        with database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM products" + where, values).fetchone()[0]
            rows = [dict(row) for row in db.execute("SELECT * FROM products" + where + " ORDER BY id LIMIT ? OFFSET ?",
                                                   (*values, limit, offset))]
            return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/products/{product_id}", response_model=Product, tags=["products"])
    def get_product(product_id: PathId):
        with database.connect() as db:
            return product_data(db, product_id)

    @router.patch("/products/{product_id}", response_model=Product, tags=["products"])
    def update_product(product_id: PathId, body: ProductPatch):
        with database.connect() as db, transaction(db):
            product_data(db, product_id)
            fields = body.model_dump(exclude_unset=True)
            # Keys originate only from the closed Pydantic model, never arbitrary input.
            assignments = ",".join(field + "=?" for field in fields)
            db.execute("UPDATE products SET " + assignments + ",updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                       (*fields.values(), product_id))
            return product_data(db, product_id)

    @router.delete("/products/{product_id}", response_model=Product, tags=["products"])
    def archive_product(product_id: PathId):
        """Soft-delete a product. History and reserved orders remain available."""
        with database.connect() as db, transaction(db):
            product_data(db, product_id)
            db.execute("UPDATE products SET active=0,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (product_id,))
            return product_data(db, product_id)

    @router.post("/products/{product_id}/adjustments", response_model=AdjustmentResult, tags=["stock"])
    def adjust_stock(product_id: PathId, body: Adjustment, idempotency_key: IdempotencyKey):
        def operation(db):
            product_data(db, product_id)
            change_stock(db, product_id, body.delta)
            movement = add_movement(db, product_id, body.delta, "adjustment", body.reason)
            return {"product": product_data(db, product_id), "movement": movement}, 200
        return idempotent(database, f"adjustment:{product_id}", idempotency_key, body.model_dump(), operation)

    @router.get("/products/{product_id}/ledger", response_model=LedgerPage, tags=["stock"])
    def ledger(product_id: PathId, limit: Limit = 50, offset: Offset = 0):
        with database.connect() as db:
            product_data(db, product_id)
            return {"items": [dict(row) for row in db.execute(
                "SELECT * FROM stock_ledger WHERE product_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (product_id, limit, offset))], "limit": limit, "offset": offset}

    @router.post("/orders", response_model=Order, status_code=201, tags=["orders"])
    def reserve_order(body: OrderCreate, idempotency_key: IdempotencyKey):
        """Atomically reserve every line, or roll back the complete order."""
        def operation(db):
            order_id = db.execute("INSERT INTO orders(customer_reference,status,total_cents) VALUES(?,'reserved',0)",
                                  (body.customer_reference,)).lastrowid
            total = 0
            for item in sorted(body.items, key=lambda line: line.product_id):
                product = product_data(db, item.product_id)
                if not product["active"]:
                    raise HTTPException(409, "Cannot reserve an inactive product")
                change_stock(db, item.product_id, -item.quantity)
                db.execute("INSERT INTO order_items(order_id,product_id,sku,name,quantity,unit_price_cents) VALUES(?,?,?,?,?,?)",
                           (order_id, item.product_id, product["sku"], product["name"], item.quantity, product["price_cents"]))
                add_movement(db, item.product_id, -item.quantity, "reserve", "Order reservation", order_id)
                total += item.quantity * product["price_cents"]
            db.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
            return order_data(db, order_id), 201
        return idempotent(database, "order:create", idempotency_key, body.model_dump(), operation)

    @router.get("/orders", response_model=OrderPage, tags=["orders"])
    def list_orders(limit: Limit = 50, offset: Offset = 0,
                    status: Literal["reserved", "cancelled"] | None = None,
                    customer_reference: Annotated[str | None, Query(max_length=120)] = None):
        clauses, values = [], []
        if status is not None:
            clauses.append("status=?")
            values.append(status)
        if customer_reference is not None:
            clauses.append("customer_reference=?")
            values.append(customer_reference)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM orders" + where, values).fetchone()[0]
            rows = [dict(row) for row in db.execute("SELECT * FROM orders" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                                                   (*values, limit, offset))]
            return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/orders/{order_id}", response_model=Order, tags=["orders"])
    def get_order(order_id: PathId):
        with database.connect() as db:
            return order_data(db, order_id)

    @router.post("/orders/{order_id}/cancel", response_model=Order, tags=["orders"])
    def cancel_order(order_id: PathId):
        """Repeated cancellation returns the cancelled order without releasing stock twice."""
        with database.connect() as db, transaction(db):
            order = order_data(db, order_id)
            if order["status"] == "cancelled":
                return order
            for item in order["items"]:
                change_stock(db, item["product_id"], item["quantity"])
                add_movement(db, item["product_id"], item["quantity"], "release", "Order cancelled", order_id)
            db.execute("UPDATE orders SET status='cancelled',cancelled_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                       (order_id,))
            return order_data(db, order_id)

    @router.get("/exports/{resource}.csv", tags=["exports"], response_class=Response)
    def export_csv(resource: Literal["products", "orders"]):
        with database.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM " + resource + " ORDER BY id LIMIT 10001")]
        if len(rows) > 10000:
            raise HTTPException(413, "CSV export is limited to 10000 rows")
        columns = (["id", "sku", "name", "price_cents", "available", "active"] if resource == "products"
                   else ["id", "status", "customer_reference", "total_cents", "created_at"])
        return csv_response(columns, rows, resource + ".csv")

    app.include_router(router)
    return app
