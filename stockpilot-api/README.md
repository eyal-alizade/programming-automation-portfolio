# StockPilot API

A small-business inventory and order-reservation backend built with **FastAPI, Pydantic and SQLite**. It focuses on the parts that must remain correct when a request is retried or two orders arrive together: stock quantities, transaction rollback, the movement ledger and idempotency.

**Personal portfolio project by Eyal Alizade (אייל אליזאדה).** Examples and the offline demo contain synthetic products and references only. This is not client work or a deployed inventory service. It has no marketing website, payment processing or external-service integration.

## Run the offline demonstration

Requires Python 3.11 or newer. From this project directory:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python demo.py --output-dir demo-output
.venv/bin/python -m unittest discover -s tests -v
```

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

The demo uses the actual ASGI application and a temporary SQLite database, generates a random runtime API key, and sends requests **in process**. It starts no HTTP server and contacts no external services. It creates a new output directory containing:

- `report.json`: reservation, retry, oversell-rejection and cancellation acceptance results.
- `products.csv` and `orders.csv`: synthetic spreadsheet-safe exports.
- `openapi.json`: the generated API specification, without the runtime key.

The directory must not already exist. The database is removed when the demo exits; generated reports remain. No live customer data is needed.

Expected acceptance result:

```json
{
  "product_created": true,
  "order_reserved": true,
  "idempotency_replayed": true,
  "oversell_rejected": true,
  "cancellation_repeat_safe": true,
  "original_stock_restored": true
}
```

## Start a local API

Generate a new key for your own local instance. The application has no default or embedded key.

```sh
export STOCKPILOT_API_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export STOCKPILOT_DB="data/stockpilot.sqlite3"
.venv/bin/python -m uvicorn stockpilot:create_app --factory --host 127.0.0.1 --port 8000
```

In PowerShell:

```powershell
$env:STOCKPILOT_API_KEY = .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:STOCKPILOT_DB = "data/stockpilot.sqlite3"
.venv\Scripts\python.exe -m uvicorn stockpilot:create_app --factory --host 127.0.0.1 --port 8000
```

API routes require `X-API-Key`. The key must be a 24–200 character URL-safe token; absent or invalid configuration fails startup. The database path is read at factory invocation, with no database creation on module import. `data/`, database files, `.env*`, `.venv/` and demo output are ignored by Git.

Local developer documentation: [Swagger UI](http://127.0.0.1:8000/docs), [ReDoc](http://127.0.0.1:8000/redoc), [OpenAPI JSON](http://127.0.0.1:8000/openapi.json). Use **Authorize** in Swagger and enter the key. The documentation schemas and `/health` are public; business data endpoints are protected. Swagger/ReDoc load their standard JavaScript assets from a CDN; the API, tests and exported OpenAPI JSON work without that CDN.

## Example workflow

In another terminal with the same `STOCKPILOT_API_KEY` value:

```sh
curl -s -H "X-API-Key: $STOCKPILOT_API_KEY" -H "Content-Type: application/json" \
  -d '{"sku":"DEMO-CABLE","name":"Synthetic cable","price_cents":2500,"initial_stock":10}' \
  http://127.0.0.1:8000/api/v1/products

curl -s -H "X-API-Key: $STOCKPILOT_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-demo-001" \
  -d '{"customer_reference":"SYNTHETIC-DEMO","items":[{"product_id":1,"quantity":3}]}' \
  http://127.0.0.1:8000/api/v1/orders

curl -s -X POST -H "X-API-Key: $STOCKPILOT_API_KEY" \
  http://127.0.0.1:8000/api/v1/orders/1/cancel
```

Use IDs returned by your requests; `1` above assumes a fresh database. Money uses integer cents throughout. Quantities are whole units. `available` is stock available for new reservations; reservation deducts it immediately and cancellation restores it. There is no separate physical/on-hand or warehouse model.

## API

All business endpoints use `/api/v1`:

| Method and path | Behavior |
|---|---|
| `POST /products` | Create SKU, price and optional opening stock; record opening movement |
| `GET /products` | Paginate; filter `active`, literal `q` name/SKU search, or `low_stock` threshold |
| `GET /products/{id}` | Read a product, including inactive products |
| `PATCH /products/{id}` | Change name, price or active flag; stock and SKU are not directly editable |
| `DELETE /products/{id}` | Soft-delete; preserve stock, order and ledger history |
| `POST /products/{id}/adjustments` | Signed stock change with reason and required `Idempotency-Key` |
| `GET /products/{id}/ledger` | Paginated movement history, newest first |
| `POST /orders` | Reserve all product lines atomically; require `Idempotency-Key` |
| `GET /orders` | Paginate; filter `status` and exact `customer_reference` |
| `GET /orders/{id}` | Read order plus name/SKU/price snapshots |
| `POST /orders/{id}/cancel` | Release stock exactly once; repeating is safe |
| `GET /exports/products.csv` | Export active and inactive products |
| `GET /exports/orders.csv` | Export order summaries |

Lists default to 50 rows, maximum 100. CSV exports fail with `413` above 10,000 rows rather than silently truncate. Text cells beginning with spreadsheet formula markers are prefixed with an apostrophe; CSV quoting and a UTF-8 BOM are applied. This is targeted formula-injection mitigation, not a guarantee about every spreadsheet application.

Validation errors return `422`, absent/incorrect API keys `401`, unknown IDs `404`, conflicting SKUs/idempotency requests/stock shortages `409`. Product and order IDs must be positive integers no greater than `2^63 - 1`; larger values fail validation before database access. A busy database returns `503` with `Retry-After: 1`; retry mutations using the **same** idempotency key and request. Unexpected database errors return a generic error without SQL details.

## Transaction and retry rules

- Writes use `BEGIN IMMEDIATE`, conditional stock updates and one commit for the complete operation. SQLite foreign keys and quantity checks are enabled. A failing line rolls back the order, earlier stock changes, ledger rows and idempotency record together.
- Idempotency keys are scoped to order creation or the individual product's adjustments. Repeating a successful normalized JSON request returns its original body and HTTP status, with `Idempotency-Replayed: true`. Reusing the key with a different body returns `409`. Failed requests are not cached.
- Array order is significant when comparing idempotency bodies. Reuse the same request body when retrying. Idempotency records do not expire automatically in this demo.
- A creation replay after cancellation still returns the **original reservation response** and does not reserve again. Use `GET /orders/{id}` for current status.
- Cancellation checks current state under the write transaction. Ledger uniqueness adds a second guard against duplicate reservation/release movements. If a manual adjustment has filled stock to its maximum and release would overflow, cancellation returns `409` and rolls back all releases.
- Prices, names and SKUs are snapshotted onto order lines. Later edits cannot rewrite order pricing. Archived products cannot be reserved, but existing orders can still be cancelled.

## Verification and practical limits

The tests exercise actual ASGI requests and temporary SQLite files: authorization, validation, CRUD, filters, price snapshots, foreign keys, stock underflow, partial-order rollback, concurrent oversell, concurrent retries, cancellation, cancellation rollback and spreadsheet exports. The offline demo is also tested. See [VALIDATION.md](VALIDATION.md) for the recorded local run.

This is a **single-tenant backend demonstration**, with one shared API key and bounded integer quantities. It does not provide per-user roles, TLS termination, rate limiting, tax/accounting rules, fulfillment, payments, multiple currencies, warehouse allocation or schema migration tooling. SQLite serializes writers; this is not a high-volume distributed stock engine. There is no claim of production readiness or deployed availability. Keep the local server bound to loopback. Public deployment would require a separate operational/security review.

## References and author

The implementation follows the current official [FastAPI testing documentation](https://fastapi.tiangolo.com/tutorial/testing/) and [API-key security reference](https://fastapi.tiangolo.com/reference/security/). Package bounds match the tested FastAPI/Pydantic/Uvicorn release families.

Eyal Alizade / אייל אליזאדה · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)

No license grant has been selected. Hebrew: [מדריך התחלה](QUICKSTART_HE.md).
