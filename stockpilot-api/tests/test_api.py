from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from stockpilot import create_app, MAX_ID, MAX_STOCK


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "test.sqlite3"
        self.key = secrets.token_urlsafe(32)
        self.app = create_app(self.database, self.key)
        self.client = TestClient(self.app, headers={"X-API-Key": self.key})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def product(self, stock=10, sku="DEMO-001", name="Synthetic product", price=2500):
        response = self.client.post("/api/v1/products", json={
            "sku": sku, "name": name, "price_cents": price, "initial_stock": stock})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def order(self, lines, key="test-order-001", reference=None):
        body = {"items": [{"product_id": product, "quantity": quantity} for product, quantity in lines]}
        if reference is not None:
            body["customer_reference"] = reference
        return self.client.post("/api/v1/orders", json=body, headers={"Idempotency-Key": key})

    def adjust(self, product_id, delta, key="test-adjust-001", reason="Synthetic stock count"):
        return self.client.post(f"/api/v1/products/{product_id}/adjustments", json={"delta": delta, "reason": reason},
                                headers={"Idempotency-Key": key})

    def get_stock(self, product_id):
        return self.client.get(f"/api/v1/products/{product_id}").json()["available"]

    def ledger(self, product_id):
        return self.client.get(f"/api/v1/products/{product_id}/ledger").json()["items"]

    def count_rows(self, table):
        with self.app.state.database.connect() as db:
            return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_health_and_api_docs_do_not_expose_key(self):
        with TestClient(self.app) as public:
            self.assertEqual(public.get("/health").json(), {"status": "ok"})
            schema = public.get("/openapi.json")
            self.assertEqual(schema.status_code, 200)
            self.assertNotIn(self.key, schema.text)
            self.assertIn("APIKeyHeader", schema.json()["components"]["securitySchemes"])

    def test_missing_and_wrong_key_are_unauthorized(self):
        with TestClient(self.app) as public:
            for headers in ({}, {"X-API-Key": "wrong"}):
                response = public.get("/api/v1/products", headers=headers)
                self.assertEqual(response.status_code, 401)
            self.assertEqual(public.post("/api/v1/products", json={}).status_code, 401)

    def test_configuration_fails_closed_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                create_app(self.database)
        with self.assertRaises(ValueError):
            create_app(self.database, "too-short")

    def test_product_crud_and_history_preserving_delete(self):
        product = self.product()
        updated = self.client.patch(f"/api/v1/products/{product['id']}", json={"name": "Renamed demo", "price_cents": 3000})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["available"], 10)
        deleted = self.client.delete(f"/api/v1/products/{product['id']}")
        self.assertFalse(deleted.json()["active"])
        self.assertEqual(self.client.get("/api/v1/products").json()["total"], 0)
        self.assertEqual(self.client.get("/api/v1/products?active=false").json()["total"], 1)
        self.assertEqual(len(self.ledger(product["id"])), 1)

    def test_duplicate_sku_and_sql_like_input(self):
        self.product()
        duplicate = self.client.post("/api/v1/products", json={"sku": "DEMO-001", "name": "Duplicate", "price_cents": 1})
        self.assertEqual(duplicate.status_code, 409)
        response = self.client.get("/api/v1/products", params={"q": "%' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)

    def test_product_filters_and_pagination(self):
        self.product(2)
        self.product(20, "DEMO-002", "Second synthetic product")
        page = self.client.get("/api/v1/products", params={"low_stock": 5, "limit": 1}).json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["available"], 2)
        page = self.client.get("/api/v1/products?limit=1&offset=1").json()
        self.assertEqual(page["items"][0]["sku"], "DEMO-002")
        self.assertEqual(self.client.get("/api/v1/products?limit=0").status_code, 422)

    def test_input_validation_rejects_float_bool_unknown_and_blank(self):
        base = {"sku": "DEMO-001", "name": "Synthetic", "price_cents": 1}
        for update in ({"price_cents": 1.5}, {"initial_stock": True}, {"name": "   "},
                       {"name": "bad\nname"}, {"password": "not allowed"}, {"price_cents": -1}):
            response = self.client.post("/api/v1/products", json=base | update)
            self.assertEqual(response.status_code, 422, response.text)
        product = self.product()
        for body in ({}, {"name": None}, {"available": 999}, {"active": "false"}):
            self.assertEqual(self.client.patch(f"/api/v1/products/{product['id']}", json=body).status_code, 422)

    def test_adjustment_is_recorded_and_idempotent(self):
        product = self.product()
        response = self.adjust(product["id"], 5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["product"]["available"], 15)
        repeated = self.adjust(product["id"], 5)
        self.assertEqual(repeated.json(), response.json())
        self.assertEqual(repeated.headers["Idempotency-Replayed"], "true")
        self.assertEqual(self.get_stock(product["id"]), 15)
        self.assertEqual(len(self.ledger(product["id"])), 2)

    def test_conflicting_adjustment_key_and_underflow(self):
        product = self.product()
        self.assertEqual(self.adjust(product["id"], 1).status_code, 200)
        self.assertEqual(self.adjust(product["id"], 2).status_code, 409)
        before = len(self.ledger(product["id"]))
        self.assertEqual(self.adjust(product["id"], -99, "adjust-underflow").status_code, 409)
        self.assertEqual(self.get_stock(product["id"]), 11)
        self.assertEqual(len(self.ledger(product["id"])), before)

    def test_adjustment_zero_and_missing_idempotency_key(self):
        product = self.product()
        self.assertEqual(self.adjust(product["id"], 0).status_code, 422)
        response = self.client.post(f"/api/v1/products/{product['id']}/adjustments", json={"delta": 1, "reason": "Demo"})
        self.assertEqual(response.status_code, 422)

    def test_order_snapshot_and_integer_money_total(self):
        first = self.product(price=2501)
        second = self.product(4, "DEMO-002", price=103)
        response = self.order([(first["id"], 2), (second["id"], 3)])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["total_cents"], 5311)
        self.assertEqual(self.get_stock(first["id"]), 8)
        self.assertEqual(self.get_stock(second["id"]), 1)
        self.client.patch(f"/api/v1/products/{first['id']}", json={"price_cents": 9999, "name": "Changed"})
        order = self.client.get(f"/api/v1/orders/{response.json()['id']}").json()
        self.assertEqual(order["items"][0]["unit_price_cents"], 2501)
        self.assertEqual(order["items"][0]["name"], "Synthetic product")

    def test_multiline_failure_rolls_back_every_change(self):
        first = self.product(10)
        second = self.product(1, "DEMO-002")
        response = self.order([(first["id"], 3), (second["id"], 2)])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.get_stock(first["id"]), 10)
        self.assertEqual(self.get_stock(second["id"]), 1)
        self.assertEqual(self.count_rows("orders"), 0)
        self.assertEqual(self.count_rows("order_items"), 0)
        self.assertEqual(self.count_rows("idempotency"), 0)
        self.assertEqual(self.count_rows("stock_ledger"), 2)

    def test_unknown_product_rolls_back_earlier_line(self):
        first = self.product()
        response = self.order([(first["id"], 2), (999, 1)])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.get_stock(first["id"]), 10)
        self.assertEqual(self.count_rows("orders"), 0)

    def test_order_idempotency_and_payload_conflict(self):
        product = self.product()
        first = self.order([(product["id"], 2)])
        replay = self.order([(product["id"], 2)])
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(self.order([(product["id"], 3)]).status_code, 409)
        self.assertEqual(self.get_stock(product["id"]), 8)
        self.assertEqual(self.count_rows("orders"), 1)

    def test_inactive_product_cannot_be_reserved(self):
        product = self.product()
        self.client.delete(f"/api/v1/products/{product['id']}")
        self.assertEqual(self.order([(product["id"], 1)]).status_code, 409)

    def test_order_validation_duplicate_lines_and_missing_key(self):
        product = self.product()
        self.assertEqual(self.order([(product["id"], 1), (product["id"], 1)]).status_code, 422)
        self.assertEqual(self.order([(product["id"], 0)]).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/orders", json={"items": []}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/orders", json={"items": [
            {"product_id": product["id"], "quantity": 1}]}).status_code, 422)

    def test_failed_order_does_not_poison_idempotency_key(self):
        product = self.product(0)
        self.assertEqual(self.order([(product["id"], 1)]).status_code, 409)
        self.assertEqual(self.adjust(product["id"], 1).status_code, 200)
        self.assertEqual(self.order([(product["id"], 1)]).status_code, 201)
        self.assertEqual(self.get_stock(product["id"]), 0)

    def test_idempotency_is_scoped_per_adjustment_product(self):
        first = self.product()
        second = self.product(10, "DEMO-002")
        self.assertEqual(self.adjust(first["id"], 1).status_code, 200)
        self.assertEqual(self.adjust(second["id"], 1).status_code, 200)
        self.assertEqual(self.get_stock(first["id"]), 11)
        self.assertEqual(self.get_stock(second["id"]), 11)

    def test_cancellation_releases_once_even_after_product_deactivation(self):
        product = self.product()
        order = self.order([(product["id"], 3)]).json()
        self.client.delete(f"/api/v1/products/{product['id']}")
        cancelled = self.client.post(f"/api/v1/orders/{order['id']}/cancel")
        repeated = self.client.post(f"/api/v1/orders/{order['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(repeated.json(), cancelled.json())
        self.assertEqual(self.get_stock(product["id"]), 10)
        self.assertEqual([row["kind"] for row in self.ledger(product["id"])].count("release"), 1)

    def test_replayed_creation_after_cancel_returns_original_response_without_reserving(self):
        product = self.product()
        original = self.order([(product["id"], 3)]).json()
        self.client.post(f"/api/v1/orders/{original['id']}/cancel")
        replay = self.order([(product["id"], 3)])
        self.assertEqual(replay.json(), original)
        self.assertEqual(self.get_stock(product["id"]), 10)
        self.assertEqual(self.client.get(f"/api/v1/orders/{original['id']}").json()["status"], "cancelled")

    def test_cancellation_capacity_failure_rolls_back(self):
        first = self.product(10)
        second = self.product(MAX_STOCK, "DEMO-002")
        order = self.order([(first["id"], 1), (second["id"], 1)]).json()
        self.adjust(second["id"], 1)
        response = self.client.post(f"/api/v1/orders/{order['id']}/cancel")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.get_stock(first["id"]), 9)
        self.assertEqual(self.get_stock(second["id"]), MAX_STOCK)
        self.assertEqual(self.client.get(f"/api/v1/orders/{order['id']}").json()["status"], "reserved")
        self.assertFalse(any(row["kind"] == "release" for row in self.ledger(first["id"])))

    def test_concurrent_orders_cannot_oversell(self):
        product = self.product(1)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda n: self.order([(product["id"], 1)], f"concurrent-order-{n}"), range(2)))
        self.assertEqual(sorted(response.status_code for response in results), [201, 409])
        self.assertEqual(self.get_stock(product["id"]), 0)
        self.assertEqual(self.count_rows("orders"), 1)

    def test_concurrent_same_idempotency_key_does_not_duplicate(self):
        product = self.product(3)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.order([(product["id"], 1)]), range(2)))
        self.assertTrue(all(response.status_code == 201 for response in results))
        self.assertEqual(results[0].json(), results[1].json())
        self.assertEqual(self.get_stock(product["id"]), 2)
        self.assertEqual(self.count_rows("orders"), 1)

    def test_concurrent_cancel_does_not_double_release(self):
        product = self.product(4)
        order = self.order([(product["id"], 2)]).json()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.client.post(f"/api/v1/orders/{order['id']}/cancel"), range(2)))
        self.assertTrue(all(response.status_code == 200 for response in results))
        self.assertEqual(self.get_stock(product["id"]), 4)
        self.assertEqual(self.count_rows("stock_ledger"), 3)

    def test_filter_orders_by_status_and_reference(self):
        product = self.product()
        first = self.order([(product["id"], 1)], reference="DEMO-REF").json()
        self.order([(product["id"], 1)], key="test-order-002", reference="OTHER")
        self.client.post(f"/api/v1/orders/{first['id']}/cancel")
        page = self.client.get("/api/v1/orders", params={"status": "cancelled", "customer_reference": "DEMO-REF"}).json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], first["id"])

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        self.product(name="=HYPERLINK(\"example\")")
        response = self.client.get("/api/v1/exports/products.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertTrue(rows[0]["name"].startswith("'="))
        self.assertEqual(self.client.get("/api/v1/exports/idempotency.csv").status_code, 422)

    def test_sqlite_constraints_and_foreign_keys(self):
        product = self.product()
        with self.app.state.database.connect() as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE products SET available=-1 WHERE id=?", (product["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO stock_ledger(product_id,delta,balance_after,kind,reason) VALUES(999,1,1,'adjustment','demo')")

    def test_database_persists_across_app_instances(self):
        product = self.product(7)
        with TestClient(create_app(self.database, self.key), headers={"X-API-Key": self.key}) as second:
            self.assertEqual(second.get(f"/api/v1/products/{product['id']}").json()["available"], 7)

    def test_error_for_unknown_ids(self):
        for path in ("/api/v1/products/999", "/api/v1/products/999/ledger", "/api/v1/orders/999"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/orders/999/cancel").status_code, 404)

    def test_path_ids_are_positive_signed_64_bit_integers(self):
        product = self.product()
        order = self.order([(product["id"], 1)]).json()
        tables = ("products", "orders", "order_items", "stock_ledger", "idempotency")
        before = {table: self.count_rows(table) for table in tables}
        for invalid in (0, -1, MAX_ID + 1, 10**30):
            requests = (
                ("GET", f"/api/v1/products/{invalid}", {}),
                ("PATCH", f"/api/v1/products/{invalid}", {"json": {"name": "Rejected edit"}}),
                ("DELETE", f"/api/v1/products/{invalid}", {}),
                ("POST", f"/api/v1/products/{invalid}/adjustments", {
                    "json": {"delta": 1, "reason": "Rejected adjustment"},
                    "headers": {"Idempotency-Key": "invalid-path-id"}}),
                ("GET", f"/api/v1/products/{invalid}/ledger", {}),
                ("GET", f"/api/v1/orders/{invalid}", {}),
                ("POST", f"/api/v1/orders/{invalid}/cancel", {}),
            )
            for method, path, kwargs in requests:
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, **kwargs)
                    self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual({table: self.count_rows(table) for table in tables}, before)
        self.assertEqual(self.get_stock(product["id"]), 9)
        self.assertEqual(self.client.get(f"/api/v1/orders/{order['id']}").json()["status"], "reserved")
        self.assertEqual(self.client.get(f"/api/v1/products/{MAX_ID}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/orders/{MAX_ID}").status_code, 404)

    def test_json_product_ids_reject_overflow_without_partial_writes(self):
        product = self.product()
        for invalid in (MAX_ID + 1, 10**30):
            with self.subTest(product_id=invalid):
                response = self.order([(product["id"], 1), (invalid, 1)])
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.get_stock(product["id"]), 10)
        self.assertEqual(self.count_rows("orders"), 0)
        self.assertEqual(self.count_rows("order_items"), 0)
        self.assertEqual(self.count_rows("idempotency"), 0)
        self.assertEqual(self.count_rows("stock_ledger"), 1)
        self.assertEqual(self.order([(MAX_ID, 1)]).status_code, 404)


class DemoTests(unittest.TestCase):
    def test_offline_demo_writes_synthetic_outputs_without_secret(self):
        from demo import run_demo
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            report = run_demo(output)
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["openapi.json", "orders.csv", "products.csv", "report.json"])
            self.assertTrue(json.loads((output / "report.json").read_text())["synthetic_data_only"])


if __name__ == "__main__":
    unittest.main()
