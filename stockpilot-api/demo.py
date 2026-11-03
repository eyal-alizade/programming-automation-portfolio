"""Exercise the real ASGI app offline with synthetic data and a temporary database."""
import argparse
import json
from pathlib import Path
import secrets
import tempfile

from fastapi.testclient import TestClient

from stockpilot import create_app


def run_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="stockpilot-demo-") as temporary:
        key = secrets.token_urlsafe(32)
        app = create_app(Path(temporary) / "demo.sqlite3", key)
        with TestClient(app, headers={"X-API-Key": key}) as client:
            product_response = client.post("/api/v1/products", json={
                "sku": "DEMO-CABLE", "name": "Synthetic USB cable", "price_cents": 2500, "initial_stock": 10})
            product_response.raise_for_status()
            product = product_response.json()
            second = client.post("/api/v1/products", json={
                "sku": "DEMO-ADAPTER", "name": "Synthetic adapter", "price_cents": 4000, "initial_stock": 2})
            second.raise_for_status()
            order_body = {"customer_reference": "SYNTHETIC-DEMO-001", "items": [
                {"product_id": product["id"], "quantity": 3},
                {"product_id": second.json()["id"], "quantity": 1}]}
            created = client.post("/api/v1/orders", json=order_body, headers={"Idempotency-Key": "demo-order-001"})
            created.raise_for_status()
            replayed = client.post("/api/v1/orders", json=order_body, headers={"Idempotency-Key": "demo-order-001"})
            rejected = client.post("/api/v1/orders", json={"items": [
                {"product_id": product["id"], "quantity": 100}]}, headers={"Idempotency-Key": "demo-oversell-001"})
            cancelled = client.post(f"/api/v1/orders/{created.json()['id']}/cancel")
            cancelled.raise_for_status()
            again = client.post(f"/api/v1/orders/{created.json()['id']}/cancel")
            final = client.get(f"/api/v1/products/{product['id']}").json()
            report = {
                "project": "StockPilot API", "synthetic_data_only": True,
                "transport": "in-process ASGI; no HTTP server or external service",
                "checks": {
                    "product_created": product_response.status_code == 201,
                    "order_reserved": created.status_code == 201,
                    "idempotency_replayed": replayed.headers.get("Idempotency-Replayed") == "true",
                    "oversell_rejected": rejected.status_code == 409,
                    "cancellation_repeat_safe": again.json() == cancelled.json(),
                    "original_stock_restored": final["available"] == 10,
                },
                "order_total_cents": created.json()["total_cents"],
                "order_status": cancelled.json()["status"],
                "final_available": final["available"],
            }
            if not all(report["checks"].values()):
                raise RuntimeError("Demo acceptance check failed")
            for resource in ("products", "orders"):
                export = client.get(f"/api/v1/exports/{resource}.csv")
                export.raise_for_status()
                (output / f"{resource}.csv").write_bytes(export.content)
            (output / "openapi.json").write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    arguments = parser.parse_args()
    print(json.dumps(run_demo(arguments.output_dir), indent=2))
