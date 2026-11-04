import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from deskbuddy.__main__ import main
from deskbuddy.db import Store
from deskbuddy.menus import ADMIN_COMMANDS, CUSTOMER_COMMANDS, configure_command_menu
from deskbuddy.models import Message
from deskbuddy.runner import Runner
from deskbuddy.service import BotService
from deskbuddy.transport import TelegramAPI, TransportError


class CommandMenuTests(unittest.TestCase):
    def test_customer_scope_excludes_admin_commands_and_admins_get_the_complete_list(self):
        api = Mock(spec=TelegramAPI)
        self.assertTrue(configure_command_menu(api, {9002, 9001}))
        calls = api.set_my_commands.call_args_list
        self.assertEqual(calls[0].args, (CUSTOMER_COMMANDS, {"type": "all_private_chats"}))
        self.assertEqual(calls[1].args, (ADMIN_COMMANDS, {"type": "chat", "chat_id": 9001}))
        self.assertEqual(calls[2].args, (ADMIN_COMMANDS, {"type": "chat", "chat_id": 9002}))
        self.assertEqual({item["command"] for item in CUSTOMER_COMMANDS}, {"new", "status", "cancel", "help"})
        self.assertEqual({item["command"] for item in ADMIN_COMMANDS},
                         {"new", "status", "cancel", "help", "queue", "ticket", "close"})
        api.set_chat_menu_button.assert_called_once_with()

    def test_menu_errors_are_redacted_and_do_not_retry_or_stop_the_bot(self):
        secret = "https://api.telegram.org/bot1:synthetic_secret/setMyCommands"
        for method in ("set_my_commands", "set_chat_menu_button"):
            with self.subTest(method=method):
                api = Mock(spec=TelegramAPI)
                getattr(api, method).side_effect = TransportError(secret)
                with self.assertLogs("deskbuddy.menus", level="WARNING") as captured:
                    self.assertFalse(configure_command_menu(api, {9001}))
                self.assertNotIn("synthetic_secret", "\n".join(captured.output))
                self.assertEqual(getattr(api, method).call_count, 1)

    def test_http_payloads_use_documented_scopes_and_a_short_timeout(self):
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url.rsplit("/", 1)[1], json.loads(request.data), timeout))
            return io.BytesIO(b'{"ok": true, "result": true}')

        api = TelegramAPI("1:synthetic_test", opener=opener)
        configure_command_menu(api, {9001})
        self.assertEqual(requests, [
            ("setMyCommands", {"commands": CUSTOMER_COMMANDS, "scope": {"type": "all_private_chats"}}, 5),
            ("setMyCommands", {"commands": ADMIN_COMMANDS, "scope": {"type": "chat", "chat_id": 9001}}, 5),
            ("setChatMenuButton", {"menu_button": {"type": "commands"}}, 5),
        ])

    def test_rate_limited_menu_waits_before_polling_continues_and_logs_no_remote_text(self):
        for method in ("set_my_commands", "set_chat_menu_button"):
            with self.subTest(method=method):
                events = []
                api = Mock(spec=TelegramAPI)
                api.get_updates.side_effect = lambda offset: events.append("getUpdates") or []

                def limited(*args):
                    events.append("limited")
                    raise TransportError("https://api.telegram.org/bot1:synthetic_secret/menu", code=429, retry_after=60)

                getattr(api, method).side_effect = limited
                with self.assertLogs("deskbuddy.menus", level="WARNING") as captured:
                    self.assertFalse(configure_command_menu(
                        api, {9001}, sleep=lambda delay: events.append(("sleep", delay))))
                store = Store(":memory:")
                try:
                    self.assertEqual(Runner(BotService(store, {9001}), api).poll_once(), 0)
                finally:
                    store.close()
                self.assertEqual(events, ["limited", ("sleep", 60), "getUpdates"])
                self.assertEqual(getattr(api, method).call_count, 1)
                self.assertNotIn("synthetic_secret", "\n".join(captured.output))

    def test_unconfirmed_registration_and_network_errors_are_transport_errors(self):
        def refused(request, timeout):
            return io.BytesIO(b'{"ok": true, "result": false}')

        def unavailable(request, timeout):
            raise URLError(request.full_url)

        for opener in (refused, unavailable):
            api = TelegramAPI("1:synthetic_test", opener=opener)
            for operation in (lambda: api.set_my_commands(CUSTOMER_COMMANDS, {"type": "all_private_chats"}),
                              api.set_chat_menu_button):
                with self.subTest(opener=opener.__name__), self.assertRaises(TransportError) as caught:
                    operation()
                self.assertNotIn("synthetic_test", str(caught.exception))

    def test_selecting_admin_commands_without_arguments_shows_usage_and_changes_no_ticket(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        service = BotService(store, {9001})
        for identifier, text in enumerate(("/ticket", "/close"), start=1):
            service.handle(Message(identifier, 9001, 9001, text))
        replies = store.pending()
        self.assertEqual(len(replies), 2)
        self.assertIn("שימוש: /ticket מספר בקשה", replies[0]["text"])
        self.assertIn("שימוש: /close מספר בקשה", replies[1]["text"])
        self.assertEqual(store.recent_tickets(), [])

    def test_poll_entrypoint_configures_menu_and_keeps_running_after_setup_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                api = Mock(spec=TelegramAPI)
                api.get_me.return_value = {"id": 123456}
                if failure:
                    api.set_my_commands.side_effect = TransportError("synthetic unavailable")
                with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1:synthetic_test", "DESKBUDDY_ADMIN_IDS": "9001"}), \
                        patch("deskbuddy.__main__.TelegramAPI", return_value=api), \
                        patch.object(Runner, "run_forever") as run, \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["poll", "--db", str(Path(temporary) / "live.sqlite3")]), 0)
                run.assert_called_once()
                self.assertEqual(api.set_my_commands.call_args_list[0].args[1], {"type": "all_private_chats"})

    def test_poll_startup_honors_pending_reply_retry_delay_before_any_more_telegram_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "live.sqlite3"
            store = Store(path)
            try:
                store.bind_runtime("telegram", 123456)
                BotService(store, {9001}).handle(Message(1, 1001, 1001, "/help"))
            finally:
                store.close()

            events = []

            def send(chat_id, text):
                events.append("send")
                if events.count("send") == 1:
                    raise TransportError("synthetic rate limit", code=429, retry_after=60)
                self.assertIn(("sleep", 60), events)
                return {}

            def poll(runner):
                events.append("poll")
                self.assertEqual(len(runner.store.pending()), 1)
                self.assertEqual(runner.flush(), 0)

            api = Mock(spec=TelegramAPI)
            api.get_me.return_value = {"id": 123456}
            api.send_message.side_effect = send
            api.set_my_commands.side_effect = lambda *args: events.append("commands")
            api.set_chat_menu_button.side_effect = lambda: events.append("button")
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1:synthetic_test", "DESKBUDDY_ADMIN_IDS": "9001"}), \
                    patch("deskbuddy.__main__.TelegramAPI", return_value=api), \
                    patch("deskbuddy.__main__.time.sleep", side_effect=lambda delay: events.append(("sleep", delay))), \
                    patch.object(Runner, "run_forever", autospec=True, side_effect=poll):
                self.assertEqual(main(["poll", "--db", str(path)]), 0)
            self.assertEqual(events, ["send", ("sleep", 60), "commands", "commands", "button", "poll", "send"])
            reopened = Store(path)
            try:
                self.assertEqual(reopened.pending(), [])
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
