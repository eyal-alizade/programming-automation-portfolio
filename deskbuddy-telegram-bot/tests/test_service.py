from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deskbuddy.db import Store
from deskbuddy.models import Message
from deskbuddy.runner import Runner
from deskbuddy.service import BotService
from deskbuddy.transport import FakeAPI, TransportError


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.now = 1_700_000_000.0
        self.counter = 1
        self.store = Store(self.path)
        self.store.bind_runtime("offline")
        self.service = BotService(self.store, {9001}, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def send(self, text, user=1001, identifier=None):
        identifier = self.counter if identifier is None else identifier
        self.counter = max(self.counter, identifier + 1)
        self.now += 1
        outcome = self.service.handle(Message(identifier, user, user, text))
        rows = self.store.connection.execute(
            "SELECT * FROM outbox WHERE update_id=? ORDER BY ordinal", (identifier,)).fetchall()
        return outcome, rows

    def ticket(self, user=1001):
        self.now += 61
        for text in ("/new", "Demo Bakery", "Combine daily orders into a kitchen preparation list.", "Reply in this synthetic chat", "send"):
            self.send(text, user)
        return self.store.connection.execute("SELECT MAX(id) FROM tickets").fetchone()[0]

    def test_intake_requires_confirmation_and_notifies_only_configured_admin(self):
        for text in ("/new", "Demo Bakery", "Please organize the daily orders.", "Here in this chat"):
            self.send(text)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 0)
        outcome, rows = self.send("send")
        self.assertEqual(outcome.replies_queued, 2)
        self.assertEqual([r["chat_id"] for r in rows], [1001, 9001])
        self.assertIsNone(self.store.conversation(1001))
        self.assertEqual(self.store.ticket(1)["status"], "open")

    def test_duplicate_confirmation_never_creates_second_ticket_or_reply(self):
        self.ticket()
        old_count = self.store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        result = self.service.handle(Message(self.counter-1, 1001, 1001, "send"))
        self.assertTrue(result.duplicate)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], old_count)

    def test_restart_resumes_draft_and_duplicate_history(self):
        self.send("/new")
        self.send("Demo Bakery")
        self.store.close()
        self.store = Store(self.path)
        self.service = BotService(self.store, {9001}, clock=lambda: self.now)
        self.assertEqual(self.store.conversation(1001)[0], "need")
        self.assertTrue(self.service.handle(Message(2, 1001, 1001, "Demo Bakery")).duplicate)
        self.send("A valid request after the application restarted.")
        self.assertEqual(self.store.conversation(1001)[0], "contact")

    def test_cancellation_discards_only_unsubmitted_draft(self):
        ticket_id = self.ticket()
        self.send("/new")
        self.send("/cancel")
        self.assertIsNone(self.store.conversation(1001))
        self.assertEqual(self.store.ticket(ticket_id)["status"], "open")

    def test_admin_commands_authorize_sender_not_claimed_name(self):
        ticket_id = self.ticket()
        for command in ("/queue", f"/ticket {ticket_id}", f"/close {ticket_id}"):
            _, rows = self.send(command, user=2002)
            self.assertIn("מנהלי", rows[0]["text"])
            self.assertNotIn("Demo Bakery", rows[0]["text"])
        self.assertEqual(self.store.ticket(ticket_id)["status"], "open")

    def test_customer_status_never_exposes_another_customers_ticket(self):
        ticket_id = self.ticket()
        _, rows = self.send(f"/status {ticket_id}", user=2002)
        self.assertIn("לא נמצאו", rows[0]["text"])
        self.assertNotIn("Demo Bakery", rows[0]["text"])

    def test_admin_closure_notifies_requester_once(self):
        self.ticket()
        _, first = self.send("/close 1 Resolved in the synthetic demo", user=9001)
        _, second = self.send("/close 1", user=9001)
        self.assertEqual([r["chat_id"] for r in first], [9001, 1001])
        self.assertEqual([r["chat_id"] for r in second], [9001])
        self.assertEqual(self.store.ticket(1)["status"], "closed")

    def test_bad_and_oversized_input_keeps_current_step(self):
        self.send("/new")
        self.send("x")
        self.send("x" * 2001)
        self.assertEqual(self.store.conversation(1001)[0], "name")
        self.send("<demo & business>")
        self.assertEqual(self.store.conversation(1001)[1]["name"], "<demo & business>")

    def test_fourth_open_request_is_rejected(self):
        for _ in range(3):
            self.ticket()
        self.now += 61
        _, rows = self.send("/new")
        self.assertIn("שלוש", rows[0]["text"])
        self.assertIsNone(self.store.conversation(1001))

    def test_rate_limit_survives_restart_and_warns_once_per_window(self):
        self.service = BotService(self.store, {9001}, clock=lambda: self.now, rate_limit=2)
        self.send("/help")
        self.send("/help")
        first, warnings = self.send("/new")
        second, silence = self.send("/new")
        self.assertTrue(first.rate_limited)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(second.rate_limited)
        self.assertEqual(len(silence), 0)
        self.store.close()
        self.store = Store(self.path)
        self.service = BotService(self.store, {9001}, clock=lambda: self.now, rate_limit=2)
        self.assertTrue(self.send("/help")[0].rate_limited)
        self.now += 61
        self.assertFalse(self.send("/new")[0].rate_limited)

    def test_database_transaction_rolls_back_ticket_state_and_update_marker(self):
        for text in ("/new", "Demo Bakery", "A sufficiently detailed synthetic request.", "Here in chat"):
            self.send(text)
        update_id = self.counter
        with patch.object(self.store, "enqueue", side_effect=RuntimeError("simulated disk failure")):
            with self.assertRaises(RuntimeError):
                self.send("send", identifier=update_id)
        self.assertFalse(self.store.seen(update_id))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 0)
        self.assertEqual(self.store.conversation(1001)[0], "confirm")
        self.send("send", identifier=update_id)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1)

    def test_committed_pending_reply_is_delivered_after_restart(self):
        self.send("/help")
        self.store.close()
        self.store = Store(self.path)
        self.service = BotService(self.store, {9001}, clock=lambda: self.now)
        api = FakeAPI()
        Runner(self.service, api).flush()
        Runner(self.service, api).flush()
        self.assertEqual(len(api.messages), 1)
        self.assertEqual(len(self.store.pending()), 0)

    def test_temporary_delivery_failure_leaves_reply_pending(self):
        self.send("/help")
        api = FakeAPI()
        api.failure = TransportError("temporary", retry_after=5)
        self.assertEqual(Runner(self.service, api).flush(), 5)
        self.assertEqual(len(self.store.pending()), 1)
        api.failure = None
        Runner(self.service, api).flush()
        self.assertEqual(len(api.messages), 1)

    def test_offline_database_cannot_be_bound_to_live_telegram(self):
        with self.assertRaisesRegex(ValueError, "separate databases"):
            self.store.bind_runtime("telegram", 123)
        self.assertEqual(self.store.meta("runtime"), "offline")

    def test_non_private_core_message_is_ignored_but_marked_processed(self):
        result = self.service.handle(Message(90, 1001, -1001, "/new", "group"))
        self.assertEqual(result.replies_queued, 0)
        self.assertTrue(self.store.seen(90))
        self.assertIsNone(self.store.conversation(1001))


if __name__ == "__main__":
    unittest.main()
