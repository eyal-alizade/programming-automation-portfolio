import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from deskbuddy.__main__ import main
from deskbuddy.db import Store
from deskbuddy.demo import run_demo
from deskbuddy.runner import Runner
from deskbuddy.service import BotService
from deskbuddy.transport import FakeAPI, TelegramAPI, TransportError, parse_message


def update(number=1, text="/help", user=1001, chat_type="private"):
    return {"update_id": number, "message": {"from": {"id": user, "is_bot": False},
            "chat": {"id": user, "type": chat_type}, "text": text}}


class TransportTests(unittest.TestCase):
    def test_parser_ignores_groups_bots_attachments_and_edited_messages(self):
        self.assertIsNotNone(parse_message(update()))
        self.assertIsNone(parse_message(update(chat_type="group")))
        bot = update()
        bot["message"]["from"]["is_bot"] = True
        self.assertIsNone(parse_message(bot))
        self.assertIsNone(parse_message({"update_id": 1, "edited_message": update()["message"]}))
        malformed = update()
        malformed["message"]["text"] = None
        self.assertIsNone(parse_message(malformed))

    def test_http_adapter_uses_json_plain_text_and_documented_update_filters(self):
        requests = []
        def opener(request, timeout):
            requests.append((request, timeout))
            return io.BytesIO(json.dumps({"ok": True, "result": []}).encode())
        api = TelegramAPI("1:offline_test", opener=opener)
        self.assertEqual(api.get_updates(17), [])
        payload = json.loads(requests[0][0].data)
        self.assertEqual(payload["offset"], 17)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertEqual(requests[0][1], 35)
        api.send_message(1001, "<hello> & שלום")
        sent = json.loads(requests[1][0].data)
        self.assertNotIn("parse_mode", sent)
        self.assertEqual(sent["text"], "<hello> & שלום")

    def test_http_error_never_leaks_token_or_untrusted_remote_description(self):
        token = "1:offline_test"
        def opener(request, timeout):
            body = json.dumps({"description": token, "parameters": {"retry_after": 7}}).encode()
            raise HTTPError(request.full_url, 429, token, {}, io.BytesIO(body))
        with self.assertRaises(TransportError) as raised:
            TelegramAPI(token, opener=opener).send_message(1001, "test")
        self.assertNotIn(token, str(raised.exception))
        self.assertEqual(raised.exception.retry_after, 7)
        self.assertFalse(raised.exception.permanent)

    def test_network_exception_is_redacted(self):
        def opener(request, timeout):
            raise URLError(request.full_url)
        with self.assertRaises(TransportError) as raised:
            TelegramAPI("1:offline_test", opener=opener).get_me()
        self.assertNotIn("offline_test", str(raised.exception))

    def test_untrusted_error_code_is_never_rendered_as_a_message(self):
        def opener(request, timeout):
            return io.BytesIO(json.dumps({"ok": False, "error_code": "1:offline_test"}).encode())
        with self.assertRaises(TransportError) as raised:
            TelegramAPI("1:offline_test", opener=opener).get_me()
        self.assertNotIn("offline_test", str(raised.exception))
        self.assertEqual(raised.exception.code, 0)

    def test_polling_persists_offsets_and_ignores_duplicate_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "poll.sqlite3")
            try:
                service, api = BotService(store, {9001}), FakeAPI([update(5), update(5), {"update_id": 6, "poll": {}}])
                runner = Runner(service, api)
                runner.poll_once()
                self.assertEqual(len(api.messages), 1)
                self.assertEqual(store.offset, 7)
                runner.poll_once()
                self.assertEqual(api.offsets, [0, 7])
                self.assertEqual(len(api.messages), 1)
            finally:
                store.close()

    def test_permanent_delivery_failure_is_visible_and_does_not_block_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "outbox.sqlite3")
            try:
                service, api = BotService(store, {9001}), FakeAPI()
                service.handle(parse_message(update(1)))
                service.handle(parse_message(update(2, user=1002)))
                with patch.object(api, "send_message", side_effect=[TransportError("forbidden", code=403, permanent=True), {}]):
                    Runner(service, api).flush()
                statuses = [r[0] for r in store.connection.execute("SELECT status FROM outbox ORDER BY id")]
                self.assertEqual(statuses, ["failed", "sent"])
            finally:
                store.close()

    def test_demo_is_deterministic_offline_and_contains_restart_and_duplicate(self):
        stream = io.StringIO()
        with patch.object(TelegramAPI, "_request", side_effect=AssertionError("Network mode used")):
            with contextlib.redirect_stdout(stream):
                transcript = run_demo(as_json=True)
        value = json.loads(stream.getvalue())
        self.assertTrue(value["synthetic"])
        self.assertFalse(value["network_used"])
        self.assertIn("restart", {v["event"] for v in transcript})
        self.assertEqual(sum(v["event"] == "duplicate_ignored" for v in transcript), 1)
        self.assertIn("נסגרה", transcript[-1]["text"])

    def test_live_mode_without_configuration_never_contacts_telegram(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(TelegramAPI, "get_me") as network:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["poll"]), 2)
            network.assert_not_called()

    def test_database_is_bound_to_one_live_bot(self):
        store = Store(":memory:")
        try:
            store.bind_runtime("telegram", 1)
            with self.assertRaisesRegex(ValueError, "different Telegram bot"):
                store.bind_runtime("telegram", 2)
            self.assertEqual(store.meta("bot_id"), "1")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
