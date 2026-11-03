"""Request validation, API response models and shared numeric limits."""
from typing import Annotated, Literal

from fastapi import Header, Path as ApiPath, Query
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
