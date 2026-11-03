import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from demo_server import create_demo_app
from stockpilot import create_app


class DemoUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = create_demo_app(self.directory / "demo.sqlite3")
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def demo_headers(self):
        return {"X-API-Key": self.client.get("/demo/session").json()["api_key"]}

    def test_hebrew_ui_and_separate_assets_are_served(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('lang="he" dir="rtl"', page.text)
        self.assertIn("נתוני הדגמה", page.text)
        self.assertIn("הזמנה חדשה", page.text)
        self.assertNotIn(self.app.state.api_key, page.text)
        self.assertEqual(self.client.get("/demo-assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/demo-assets/styles.css").status_code, 200)
        self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])

    def test_demo_still_requires_authentication_for_business_api(self):
        self.assertEqual(self.client.get("/api/v1/products").status_code, 401)
        session = self.client.get("/demo/session")
        self.assertTrue(session.json()["synthetic_data"])
        self.assertEqual(session.headers["Cache-Control"], "no-store")
        products = self.client.get("/api/v1/products", headers=self.demo_headers()).json()
        self.assertEqual(products["total"], 5)
        self.assertEqual(products["items"][0]["name"], "כבל USB-C קלוע")
        # The visible starting reservation has already deducted three units.
        self.assertEqual(products["items"][0]["available"], 21)

    def test_screen_workflow_uses_real_inventory_and_order_transactions(self):
        headers = self.demo_headers()
        request = {"customer_reference": "בדיקת מסך סינתטית", "items": [{"product_id": 1, "quantity": 2}]}
        key_headers = headers | {"Idempotency-Key": "demo-ui-order-001"}
        created = self.client.post("/api/v1/orders", json=request, headers=key_headers)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/products/1", headers=headers).json()["available"], 19)
        self.assertEqual(self.client.post("/api/v1/orders", json=request, headers=key_headers).json(), created.json())
        shortage = self.client.post("/api/v1/orders", json={"items": [{"product_id": 1, "quantity": 1000}]},
                                    headers=headers | {"Idempotency-Key": "demo-ui-shortage"})
        self.assertEqual(shortage.status_code, 409)
        cancel = f"/api/v1/orders/{created.json()['id']}/cancel"
        self.assertEqual(self.client.post(cancel, headers=headers).json()["status"], "cancelled")
        self.client.post(cancel, headers=headers)
        self.assertEqual(self.client.get("/api/v1/products/1", headers=headers).json()["available"], 21)
        movements = self.client.get("/api/v1/products/1/ledger", headers=headers).json()["items"]
        self.assertEqual(sum(item["kind"] == "release" for item in movements), 1)

    def test_demo_ignores_real_environment_key_and_database(self):
        private_key = secrets.token_urlsafe(32)
        private_path = self.directory / "private.sqlite3"
        production = create_app(private_path, private_key)
        with TestClient(production, headers={"X-API-Key": private_key}) as private_client:
            private_client.post("/api/v1/products", json={"sku": "PRIVATE-DEMO", "name": "Synthetic isolation sentinel", "price_cents": 1})
            with patch.dict(os.environ, {"STOCKPILOT_API_KEY": private_key, "STOCKPILOT_DB": str(private_path)}):
                isolated = create_demo_app(self.directory / "isolated.sqlite3")
            with TestClient(isolated, base_url="http://127.0.0.1") as demo_client:
                key = demo_client.get("/demo/session").json()["api_key"]
                self.assertNotEqual(key, private_key)
                self.assertNotEqual(isolated.state.database.path, private_path)
                self.assertNotEqual(key, self.app.state.api_key)
                self.assertEqual(demo_client.get("/api/v1/products", headers={"X-API-Key": private_key}).status_code, 401)
                products = demo_client.get("/api/v1/products", headers={"X-API-Key": key}).json()
                self.assertEqual(products["total"], 5)
                self.assertFalse(any(item["sku"] == "PRIVATE-DEMO" for item in products["items"]))
            self.assertEqual(private_client.get("/api/v1/products").json()["total"], 1)
            for path in ("/demo/session", "/demo-assets/app.js", "/"):
                self.assertEqual(private_client.get(path).status_code, 404)

    def test_demo_refuses_existing_database_instead_of_reusing_data(self):
        sentinel = self.directory / "existing.sqlite3"
        sentinel.write_bytes(b"synthetic sentinel: preserve")
        with self.assertRaisesRegex(ValueError, "new database"):
            create_demo_app(sentinel)
        self.assertEqual(sentinel.read_bytes(), b"synthetic sentinel: preserve")

    def test_unrecognized_host_cannot_read_demo_session(self):
        response = self.client.get("/demo/session", headers={"Host": "outside.example"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.app.state.api_key, response.text)


if __name__ == "__main__":
    unittest.main()
