"""Application factory and HTTP-level error handling."""
import os
from pathlib import Path
import re
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .database import Database
from .routes import router


def health() -> dict:
    return {"status": "ok"}


async def database_failure(_request: Request, error: sqlite3.OperationalError) -> JSONResponse:
    busy = any(word in str(error).lower() for word in ("locked", "busy"))
    message = "Database busy; retry with the same idempotency key" if busy else "Database operation failed"
    return JSONResponse({"detail": message}, status_code=503 if busy else 500,
                        headers={"Retry-After": "1"} if busy else {})


def create_app(db_path: str | Path | None = None, api_key: str | None = None) -> FastAPI:
    """Read runtime configuration and fail closed when no valid key is supplied."""
    key = api_key if api_key is not None else os.environ.get("STOCKPILOT_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{24,200}", key):
        raise ValueError("Set STOCKPILOT_API_KEY to a random 24–200 character URL-safe token")
    database = Database(Path(db_path or os.environ.get("STOCKPILOT_DB", "data/stockpilot.sqlite3")))
    app = FastAPI(title="StockPilot API", version="1.0.0", description="Local inventory and order-reservation backend.")
    app.state.database = database
    app.state.api_key = key
    app.add_api_route("/health", health, tags=["system"])
    app.add_exception_handler(sqlite3.OperationalError, database_failure)
    app.include_router(router)
    return app
