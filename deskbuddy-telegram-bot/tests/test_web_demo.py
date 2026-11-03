import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deskbuddy.models import Message
from deskbuddy.transport import TelegramAPI
from deskbuddy.web_demo import (
    DemoApplication, DemoRequestHandler, DemoRouter, MAX_BODY_BYTES, json_response,
)


class WebDemoTests(unittest.TestCase):
    def setUp(self):
        self.app = DemoApplication()
        self.router = DemoRouter(self.app, 8812)
        self.headers = {
            "Host": "127.0.0.1:8812", "Origin": "http://127.0.0.1:8812",
            "Content-Type": "application/json",
        }
        self.sequence = 0

    def tearDown(self):
        self.app.close()

    def send(self, text, role="customer", request_id=None):
        self.sequence += 1
        payload = {"role": role, "text": text,
                   "request_id": request_id or f"message-{self.sequence:04}"}
        response = self.router.dispatch(
            "POST", "/api/message", self.headers,
            json.dumps(payload, ensure_ascii=False).encode())
        return response, json.loads(response.body)

    def create_ticket(self):
        for text in ("/new", "מאפיית הדגמה", "צריך לרכז הזמנות יומיות ברשימת הכנה אחת.", "כאן בצ׳אט", "שלח"):
            response, state = self.send(text)
            self.assertEqual(response.status, 200)
        return state

    def test_browser_intake_uses_the_real_service_and_database(self):
        self.send("/new")
        self.send("מאפיית הדגמה")
        self.assertEqual(self.app.store.conversation(1001)[0], "need")
        state = json.loads(self.router.dispatch(
            "GET", "/api/state?role=customer", self.headers).body)
        self.assertEqual(state["draft_step"], "need")
        self.assertEqual(state["tickets"], [])
        self.send("צריך לרכז הזמנות יומיות ברשימת הכנה אחת.")
        self.send("כאן בצ׳אט")
        self.assertIsNone(self.app.store.ticket(1))
        self.send("שלח")
        self.assertEqual(self.app.store.ticket(1)["name"], "מאפיית הדגמה")
        self.assertIsNone(self.app.store.conversation(1001))

    def test_admin_view_and_close_are_real_core_actions(self):
        self.create_ticket()
        state = self.app.snapshot("admin")
        self.assertEqual(state["open_count"], 1)
        response, state = self.send("/close 1", role="admin")
        self.assertEqual(response.status, 200)
        self.assertEqual(state["closed_count"], 1)
        self.assertEqual(self.app.store.ticket(1)["status"], "closed")
        customer = self.app.snapshot("customer")
        self.assertIn("נסגרה", customer["messages"][-1]["text"])

    def test_customer_cannot_use_admin_command_or_supply_an_arbitrary_actor(self):
        self.create_ticket()
        _, state = self.send("/close 1", role="customer")
        self.assertEqual(self.app.store.ticket(1)["status"], "open")
        self.assertIn("מנהלי", state["messages"][-1]["text"])
        response = self.router.dispatch("POST", "/api/message", self.headers,
            json.dumps({"role": "customer", "text": "/close 1",
                        "request_id": "arbitrary-actor", "actor_id": 9001}).encode())
        self.assertEqual(response.status, 400)

    def test_replayed_http_request_does_not_repeat_a_message(self):
        self.send("/new", request_id="stable-request")
        count = len(self.app.snapshot("customer")["messages"])
        self.send("/new", request_id="stable-request")
        self.assertEqual(len(self.app.snapshot("customer")["messages"]), count)
        response, _ = self.send("/cancel", request_id="stable-request")
        self.assertEqual(response.status, 409)

    def test_external_or_missing_origins_cannot_write(self):
        for origin in (None, "https://example.invalid", "null"):
            with self.subTest(origin=origin):
                headers = dict(self.headers)
                if origin is None:
                    headers.pop("Origin")
                else:
                    headers["Origin"] = origin
                response = self.router.dispatch("POST", "/api/message", headers, b"{}")
                self.assertEqual(response.status, 403)
        self.assertIsNone(self.app.store.conversation(1001))

    def test_host_header_and_non_whitelisted_paths_are_rejected(self):
        response = self.router.dispatch("GET", "/", {"Host": "external.example:8812"})
        self.assertEqual(response.status, 403)
        for target in ("/../db.py", "/%2e%2e/db.py", "/deskbuddy/db.py", "/api/state?role=admin&role=customer"):
            with self.subTest(target=target):
                self.assertIn(self.router.dispatch("GET", target, self.headers).status, (400, 404))
        self.assertEqual(self.router.dispatch("GET", "//example.invalid/file", self.headers).status, 400)

    def test_json_and_text_limits_are_enforced_before_mutating_state(self):
        bad_payloads = [None, [], {},
            {"role": [], "text": "/new", "request_id": "bad-role-1"},
            {"role": "customer", "text": "x" * 2001, "request_id": "too-long-1"},
            {"role": "customer", "text": "/new", "request_id": "!"},
        ]
        for payload in bad_payloads:
            with self.subTest(payload_type=type(payload).__name__):
                response = self.router.dispatch("POST", "/api/message", self.headers, json.dumps(payload).encode())
                self.assertEqual(response.status, 400)
        too_large = self.router.dispatch("POST", "/api/message", self.headers, b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual(too_large.status, 413)
        wrong_type = self.router.dispatch("POST", "/api/message",
            {**self.headers, "Content-Type": "text/plain"}, b"{}")
        self.assertEqual(wrong_type.status, 415)
        self.assertIsNone(self.app.store.conversation(1001))

    def test_static_files_are_bundled_and_no_network_adapter_is_used(self):
        for target in ("/", "/styles.css", "/app.js"):
            self.assertEqual(self.router.dispatch("GET", target, self.headers).status, 200)
        with patch.object(TelegramAPI, "_request", side_effect=AssertionError("Network used")):
            self.create_ticket()
        self.assertEqual(self.app.store.meta("runtime"), "offline")

    def test_fresh_session_has_its_own_temporary_database(self):
        self.create_ticket()
        other = DemoApplication()
        try:
            self.assertEqual(other.snapshot("customer")["tickets"], [])
            self.assertEqual(other.snapshot("admin")["open_count"], 0)
        finally:
            other.close()

    def test_counts_do_not_silently_truncate_at_the_ticket_list_limit(self):
        with self.app.store.transaction():
            for number in range(25):
                ticket = self.app.store.create_ticket(
                    Message(100 + number, 1001, 1001, "demo"),
                    {"name": "דוגמה", "need": "בקשה מלאכותית לצורך הבדיקה", "contact": "כאן"}, 1000)
                self.app.store.close_ticket(ticket, "done", 1001)
        state = self.app.snapshot("admin")
        self.assertEqual(len(state["tickets"]), 20)
        self.assertEqual(state["closed_count"], 25)

    def test_http_handler_emits_security_headers_without_cors_permission(self):
        handler = object.__new__(DemoRequestHandler)
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        handler.wfile = io.BytesIO()
        handler._respond(json_response({"demo": True}))
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"demo": True})

    def test_oversized_content_length_is_rejected_without_reading_the_body(self):
        handler = object.__new__(DemoRequestHandler)
        handler.headers = {"Content-Length": "9" * 5000}
        handler.rfile, handler._respond = Mock(), Mock()
        handler.do_POST()
        self.assertEqual(handler._respond.call_args.args[0].status, 413)
        handler.rfile.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
