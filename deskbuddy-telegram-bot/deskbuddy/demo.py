"""Synthetic offline scenarios, using the same core and outbox as polling."""
import json
from pathlib import Path
import tempfile
from .db import Store
from .models import Message
from .runner import Runner
from .service import BotService
from .transport import FakeAPI


DEMO_ADMIN, DEMO_CLIENT = 9001, 1001


def run_demo(database=None, as_json=False):
    """Print a restart, role check, ticket creation, duplicate update and closure."""
    with tempfile.TemporaryDirectory(prefix="deskbuddy-demo-") as temp:
        path = Path(database) if database else Path(temp) / "demo.sqlite3"
        if path.exists():
            raise ValueError("The scripted demo requires a new database; use simulate to resume one.")
        store = Store(path)
        api = FakeAPI()
        transcript = []
        clock_value = [1_700_000_000.0]
        store.bind_runtime("offline")
        service = BotService(store, {DEMO_ADMIN}, clock=lambda: clock_value[0])
        runner = Runner(service, api)
        next_id = 1

        def send(user, text, update_id=None):
            nonlocal next_id
            identifier = next_id if update_id is None else update_id
            if update_id is None:
                next_id += 1
            clock_value[0] += 1
            transcript.append({"event": "input", "user_id": user, "update_id": identifier, "text": text})
            before = len(api.messages)
            outcome = service.handle(Message(identifier, user, user, text))
            runner.flush()
            transcript.extend({"event": "reply", **r} for r in api.messages[before:])
            if outcome.duplicate:
                transcript.append({"event": "duplicate_ignored", "update_id": identifier})

        try:
            send(DEMO_CLIENT, "/start")
            send(DEMO_CLIENT, "/queue")
            send(DEMO_CLIENT, "/new")
            send(DEMO_CLIENT, "מאפיית הדגמה")
            store.close()
            transcript.append({"event": "restart", "text": "Database reopened; the draft resumes at the need step."})
            store = Store(path)
            store.bind_runtime("offline")
            service = BotService(store, {DEMO_ADMIN}, clock=lambda: clock_value[0])
            runner = Runner(service, api)
            send(DEMO_CLIENT, "נרצה כלי שמרכז הזמנות יומיות ומפיק רשימת הכנה למטבח.")
            send(DEMO_CLIENT, "כאן בצ׳אט — נתוני הדגמה בלבד")
            confirm_id = next_id
            send(DEMO_CLIENT, "שלח")
            send(DEMO_CLIENT, "שלח", update_id=confirm_id)
            send(DEMO_ADMIN, "/queue")
            send(DEMO_ADMIN, "/ticket 1")
            send(DEMO_ADMIN, "/close 1 ההדגמה הושלמה; זו אינה בקשת לקוח אמיתית.")
            send(DEMO_CLIENT, "/status")
        finally:
            store.close()
        if as_json:
            print(json.dumps({"synthetic": True, "network_used": False, "transcript": transcript}, ensure_ascii=False, indent=2))
        else:
            print("DeskBuddy offline demo — synthetic identities and requests; no network calls.\n")
            for event in transcript:
                if event["event"] == "input":
                    print(f"USER {event['user_id']}: {event['text']}")
                elif event["event"] == "reply":
                    print(f"BOT → {event['chat_id']}: {event['text']}\n")
                elif event["event"] == "restart":
                    print(f"[RESTART] {event['text']}\n")
                else:
                    print(f"[DUPLICATE UPDATE {event['update_id']} IGNORED]\n")
        return transcript


def simulate(database=None):
    with tempfile.TemporaryDirectory(prefix="deskbuddy-simulator-") as temp:
        store = Store(database or Path(temp) / "simulator.sqlite3")
        try:
            store.bind_runtime("offline")
            service, api = BotService(store, {DEMO_ADMIN}), FakeAPI()
            runner, actor = Runner(service, api), DEMO_CLIENT
            print("Offline simulator. Synthetic admin=9001, client=1001. No network calls.")
            print(":as 9001 switches to admin; :as 1001 switches to client; :quit exits.")
            if database:
                print("Local state persists in the database you selected.")
            runner.flush()
            cursor = 0
            while True:
                try:
                    text = input(f"{actor}> ")
                except EOFError:
                    break
                if text == ":quit":
                    break
                if text.startswith(":as "):
                    selected = text[4:].strip()
                    if selected.isascii() and selected.isdecimal() and 0 < int(selected) < 2**52:
                        actor = int(selected)
                    else:
                        print("Choose a positive synthetic numeric ID.")
                    continue
                service.handle(Message(store.offset, actor, actor, text))
                runner.flush()
                for row in api.messages[cursor:]:
                    print(f"BOT → {row['chat_id']}: {row['text']}")
                cursor = len(api.messages)
        finally:
            store.close()
