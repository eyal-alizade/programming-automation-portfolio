"""SQLite schema, connection settings and explicit transaction boundaries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

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
