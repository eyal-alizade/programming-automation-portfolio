"""Thin HTTP routes: authenticate, validate, choose a transaction, call an operation."""
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.security import APIKeyHeader

from . import inventory, orders
from .database import Database, transaction
from .responses import csv_response, idempotent
from .schemas import (Adjustment, AdjustmentResult, IdempotencyKey, LedgerPage, Limit,
                      MAX_STOCK, Offset, Order, OrderCreate, OrderPage, PathId,
                      Product, ProductCreate, ProductPage, ProductPatch)

key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def authorize(request: Request, supplied: Annotated[str | None, Depends(key_header)]) -> None:
    expected = request.app.state.api_key
    if supplied is None or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})


def get_database(request: Request) -> Database:
    return request.app.state.database


DatabaseDependency = Annotated[Database, Depends(get_database)]
router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])


@router.post("/products", response_model=Product, status_code=201, tags=["products"])
def create_product(body: ProductCreate, database: DatabaseDependency):
    with database.connect() as db, transaction(db):
        return inventory.create_product(db, body)


@router.get("/products", response_model=ProductPage, tags=["products"])
def list_products(database: DatabaseDependency, limit: Limit = 50, offset: Offset = 0,
                  active: bool | None = True, q: Annotated[str | None, Query(max_length=80)] = None,
                  low_stock: Annotated[int | None, Query(ge=0, le=MAX_STOCK)] = None):
    with database.connect() as db:
        return inventory.list_products(db, limit=limit, offset=offset, active=active, q=q, low_stock=low_stock)


@router.get("/products/{product_id}", response_model=Product, tags=["products"])
def get_product(product_id: PathId, database: DatabaseDependency):
    with database.connect() as db:
        return inventory.get_product(db, product_id)


@router.patch("/products/{product_id}", response_model=Product, tags=["products"])
def update_product(product_id: PathId, body: ProductPatch, database: DatabaseDependency):
    with database.connect() as db, transaction(db):
        return inventory.update_product(db, product_id, body)


@router.delete("/products/{product_id}", response_model=Product, tags=["products"])
def archive_product(product_id: PathId, database: DatabaseDependency):
    with database.connect() as db, transaction(db):
        return inventory.archive_product(db, product_id)


@router.post("/products/{product_id}/adjustments", response_model=AdjustmentResult, tags=["stock"])
def adjust_stock(product_id: PathId, body: Adjustment, idempotency_key: IdempotencyKey,
                 database: DatabaseDependency):
    return idempotent(database, f"adjustment:{product_id}", idempotency_key, body.model_dump(),
                      lambda db: (inventory.adjust_stock(db, product_id, body), 200))


@router.get("/products/{product_id}/ledger", response_model=LedgerPage, tags=["stock"])
def ledger(product_id: PathId, database: DatabaseDependency, limit: Limit = 50, offset: Offset = 0):
    with database.connect() as db:
        return inventory.list_movements(db, product_id, limit=limit, offset=offset)


@router.post("/orders", response_model=Order, status_code=201, tags=["orders"])
def reserve_order(body: OrderCreate, idempotency_key: IdempotencyKey, database: DatabaseDependency):
    return idempotent(database, "order:create", idempotency_key, body.model_dump(),
                      lambda db: (orders.reserve_order(db, body), 201))


@router.get("/orders", response_model=OrderPage, tags=["orders"])
def list_orders(database: DatabaseDependency, limit: Limit = 50, offset: Offset = 0,
                status: Literal["reserved", "cancelled"] | None = None,
                customer_reference: Annotated[str | None, Query(max_length=120)] = None):
    with database.connect() as db:
        return orders.list_orders(db, limit=limit, offset=offset, status=status, customer_reference=customer_reference)


@router.get("/orders/{order_id}", response_model=Order, tags=["orders"])
def get_order(order_id: PathId, database: DatabaseDependency):
    with database.connect() as db:
        return orders.get_order(db, order_id)


@router.post("/orders/{order_id}/cancel", response_model=Order, tags=["orders"])
def cancel_order(order_id: PathId, database: DatabaseDependency):
    with database.connect() as db, transaction(db):
        return orders.cancel_order(db, order_id)


@router.get("/exports/{resource}.csv", tags=["exports"], response_class=Response)
def export_csv(resource: Literal["products", "orders"], database: DatabaseDependency):
    with database.connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM " + resource + " ORDER BY id LIMIT 10001")]
    if len(rows) > 10000:
        raise HTTPException(413, "CSV export is limited to 10000 rows")
    columns = (["id", "sku", "name", "price_cents", "available", "active"] if resource == "products"
               else ["id", "status", "customer_reference", "total_cents", "created_at"])
    return csv_response(columns, rows, resource + ".csv")
