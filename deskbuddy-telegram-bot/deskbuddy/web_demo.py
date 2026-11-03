"""Loopback-only browser demo of the real intake service.

The browser selects one of two synthetic actors. This is deliberately a local
role simulator, not an authentication interface or a hosted application.
"""
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

from .db import Store
from .models import Message
from .runner import Runner
from .service import BotService
from .transport import FakeAPI


ACTORS = {"customer": 1001, "admin": 9001}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
MAX_BODY_BYTES = 8192
WEB_DIRECTORY = Path(__file__).with_name("web")


class DemoError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class DemoApplication:
    """One short-lived local session, shared by the customer's two views."""

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="deskbuddy-web-")
        self._lock = threading.RLock()
        self.store = Store(
            Path(self._temporary.name) / "demo.sqlite3", check_same_thread=False)
        self.store.bind_runtime("offline")
        self.service = BotService(self.store, {ACTORS["admin"]})
        self.api = FakeAPI()
        self.runner = Runner(self.service, self.api)
        self.history = {role: deque(maxlen=100) for role in ACTORS}
        self._requests = {}
        self._message_number = 0
        for role in ACTORS:
            self._process(role, "/start", show_input=False)

    def close(self):
        self.store.close()
        self._temporary.cleanup()

    def _append_message(self, role: str, direction: str, text: str):
        self._message_number += 1
        self.history[role].append({
            "id": self._message_number, "direction": direction, "text": text})

    def _process(self, role: str, text: str, *, show_input: bool = True):
        actor = ACTORS[role]
        first_reply = len(self.api.messages)
        outcome = self.service.handle(Message(self.store.offset, actor, actor, text))
        if show_input:
            self._append_message(role, "customer", text)
        self.runner.flush()
        for reply in self.api.messages[first_reply:]:
            recipient = next(name for name, actor_id in ACTORS.items()
                             if actor_id == reply["chat_id"])
            self._append_message(recipient, "bot", reply["text"])
        return outcome

    def send(self, role: str, text: str, request_id: str):
        with self._lock:
            previous = self._requests.get(request_id)
            if previous:
                if previous != (role, text):
                    raise DemoError("מזהה ההודעה כבר שימש לתוכן אחר.", 409)
                return self._snapshot(role)
            if len(self._requests) >= 500:
                raise DemoError("הדגמה זו הגיעה למגבלת ההודעות. הפעילו אותה מחדש.", 429)
            self._process(role, text)
            self._requests[request_id] = (role, text)
            return self._snapshot(role)

    def snapshot(self, role: str):
        with self._lock:
            return self._snapshot(role)

    def _snapshot(self, role: str):
        actor = ACTORS[role]
        rows = self.store.recent_tickets(None if role == "admin" else actor)
        counts = self.store.ticket_counts(None if role == "admin" else actor)
        tickets = [{key: row[key] for key in
                    ("id", "name", "need", "contact", "status")}
                   for row in rows]
        conversation = self.store.conversation(actor)
        return {
            "demo": True, "role": role, "actor_id": actor,
            "messages": list(self.history[role]), "tickets": tickets,
            "draft_step": conversation[0] if conversation else None,
            "open_count": counts["open"], "closed_count": counts["closed"],
        }


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes


def json_response(value, status=200):
    return Response(status, "application/json; charset=utf-8",
                    json.dumps(value, ensure_ascii=False).encode("utf-8"))


class DemoRouter:
    """Small, testable route table with no general-purpose file serving."""

    def __init__(self, application: DemoApplication, port: int):
        self.application = application
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def dispatch(self, method: str, target: str, headers, body: bytes = b""):
        try:
            host = headers.get("Host", "")
            if host not in self.allowed_hosts:
                raise DemoError("כתובת מארח לא תקינה.", 403)
            if len(target) > 2048:
                raise DemoError("הכתובת ארוכה מדי.", 414)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                raise DemoError("נדרשת כתובת מקומית יחסית.", 400)
            if method == "GET":
                return self._get(parsed.path, parsed.query)
            if method != "POST":
                raise DemoError("שיטת הבקשה אינה נתמכת.", 405)
            if headers.get("Origin") != f"http://{host}":
                raise DemoError("אפשר לשלוח הודעות רק מתוך ההדגמה המקומית.", 403)
            if parsed.path != "/api/message" or parsed.query:
                raise DemoError("הכתובת לא נמצאה.", 404)
            if headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise DemoError("נדרשת בקשת JSON.", 415)
            if len(body) > MAX_BODY_BYTES:
                raise DemoError("ההודעה גדולה מדי.", 413)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise DemoError("בקשת JSON אינה תקינה.") from None
            return self._message(payload)
        except DemoError as error:
            return json_response({"error": str(error)}, error.status)

    def _get(self, path: str, query: str):
        if path in STATIC_FILES and not query:
            filename, content_type = STATIC_FILES[path]
            return Response(200, content_type, (WEB_DIRECTORY / filename).read_bytes())
        if path != "/api/state":
            raise DemoError("הכתובת לא נמצאה.", 404)
        parameters = parse_qs(query, keep_blank_values=True)
        if set(parameters) - {"role"} or len(parameters.get("role", [])) > 1:
            raise DemoError("פרטי הבקשה אינם תקינים.")
        role = parameters.get("role", ["customer"])[0]
        if role not in ACTORS:
            raise DemoError("בחרו מצב לקוח או מנהל.")
        return json_response(self.application.snapshot(role))

    def _message(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"role", "text", "request_id"}:
            raise DemoError("נדרשים מצב, הודעה ומזהה בקשה.")
        role, text, request_id = payload["role"], payload["text"], payload["request_id"]
        if not isinstance(role, str) or role not in ACTORS:
            raise DemoError("בחרו מצב לקוח או מנהל.")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or "\x00" in text:
            raise DemoError("כתבו הודעה של עד 2,000 תווים.")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
            raise DemoError("מזהה בקשה אינו תקין.")
        return json_response(self.application.send(role, text, request_id))


class DemoRequestHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        self._respond(self.server.router.dispatch("GET", self.path, self.headers))

    def do_POST(self):
        if self.headers.get("Transfer-Encoding"):
            self._respond(json_response({"error": "בקשה זו אינה נתמכת."}, 400))
            return
        length = self.headers.get("Content-Length", "")
        if not length.isascii() or not length.isdecimal():
            self._respond(json_response({"error": "חסר אורך הבקשה."}, 411))
            return
        if len(length) > 6 or int(length) > MAX_BODY_BYTES:
            self._respond(json_response({"error": "ההודעה גדולה מדי."}, 413))
            return
        try:
            body = self.rfile.read(int(length))
        except TimeoutError:
            self._respond(json_response({"error": "הבקשה לא הושלמה בזמן."}, 408))
            return
        self._respond(self.server.router.dispatch("POST", self.path, self.headers, body))

    def do_OPTIONS(self):
        self._respond(json_response({"error": "אין גישה ממקור חיצוני."}, 405))

    def _respond(self, response: Response):
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format, *args):
        # The demo does not log chat contents, query strings or identities.
        pass


def run_web_demo(port: int = 8812):
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    application = DemoApplication()
    server = None
    try:
        server = HTTPServer(("127.0.0.1", port), DemoRequestHandler)
        actual_port = server.server_address[1]
        server.router = DemoRouter(application, actual_port)
        print(f"DeskBuddy local demo: http://127.0.0.1:{actual_port}", flush=True)
        print("Synthetic data only. No Telegram token or external requests. Ctrl+C stops the demo.", flush=True)
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        application.close()
