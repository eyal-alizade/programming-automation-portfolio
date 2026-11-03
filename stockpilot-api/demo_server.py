"""Open the Hebrew inventory demo against a fresh, temporary SQLite database."""
import argparse
from pathlib import Path
import secrets
import tempfile

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import uvicorn

from stockpilot import create_app
from stockpilot_core.database import transaction
from stockpilot_core.inventory import create_product
from stockpilot_core.orders import reserve_order
from stockpilot_core.schemas import OrderCreate, ProductCreate

ASSETS = Path(__file__).parent / "ui"
SAMPLE_PRODUCTS = (
    ("CBL-100", "כבל USB-C קלוע", 4900, 24),
    ("PWR-200", "מטען קיר 65W", 12900, 8),
    ("HUB-300", "רכזת USB-C", 18900, 5),
    ("STD-400", "מעמד למחשב נייד", 15900, 12),
    ("AUD-500", "אוזניות אלחוטיות", 22900, 3),
)


def seed_demo(database) -> None:
    """Use the same operations and transaction boundaries as the API."""
    with database.connect() as db, transaction(db):
        products = [create_product(db, ProductCreate(
            sku=sku, name=name, price_cents=price, initial_stock=stock))
            for sku, name, price, stock in SAMPLE_PRODUCTS]
        reserve_order(db, OrderCreate(customer_reference="הזמנת הדגמה 1042", items=[
            {"product_id": products[0]["id"], "quantity": 3},
            {"product_id": products[1]["id"], "quantity": 1},
        ]))


async def demo_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    return response


def create_demo_app(database_path: Path):
    """Demo-only factory: refuse existing databases and ignore real environment keys."""
    if database_path.exists() or database_path.is_symlink():
        raise ValueError("The demo requires a new database path")
    demo_key = secrets.token_urlsafe(32)
    app = create_app(database_path, demo_key)
    seed_demo(app.state.database)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.middleware("http")(demo_headers)
    app.mount("/demo-assets", StaticFiles(directory=ASSETS), name="demo-assets")
    app.add_api_route("/", lambda: FileResponse(ASSETS / "index.html"), include_in_schema=False)

    def demo_session():
        # This random key belongs only to this fresh synthetic database.
        # The production create_app() never registers this endpoint.
        return JSONResponse({"api_key": demo_key, "synthetic_data": True},
                            headers={"Cache-Control": "no-store"})

    app.add_api_route("/demo/session", demo_session, include_in_schema=False)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8811)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    with tempfile.TemporaryDirectory(prefix="stockpilot-ui-demo-") as workspace:
        app = create_demo_app(Path(workspace) / "demo.sqlite3")
        print(f"StockPilot demo: http://127.0.0.1:{args.port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
