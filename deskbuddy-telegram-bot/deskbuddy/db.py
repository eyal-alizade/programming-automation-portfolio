"""SQLite storage. State changes and outgoing replies share one transaction."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


class Store:
    def __init__(self, path: str | Path, *, check_same_thread: bool = True):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            path, timeout=5, isolation_level=None, check_same_thread=check_same_thread)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.close()
            raise ValueError("Unsupported database version; use a compatible release.")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed_updates (
                update_id INTEGER PRIMARY KEY, processed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                user_id INTEGER PRIMARY KEY, step TEXT NOT NULL,
                data TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL, name TEXT NOT NULL,
                need TEXT NOT NULL, contact TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('open', 'closed')),
                created_at REAL NOT NULL, closed_at REAL, closure_note TEXT
            );
            CREATE INDEX IF NOT EXISTS tickets_owner ON tickets(user_id, id);
            CREATE TABLE IF NOT EXISTS rate_limits (
                user_id INTEGER PRIMARY KEY, window_start REAL NOT NULL,
                count INTEGER NOT NULL, warned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, update_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'sent', 'failed')),
                failure_code INTEGER,
                UNIQUE(update_id, ordinal)
            );
            PRAGMA user_version = 1;
        """)

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self):
        self.connection.close()

    def meta(self, key, default=None):
        row = self.connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def bind_runtime(self, mode, bot_id=None):
        """Offline synthetic data can never accidentally be sent by a live bot."""
        if mode not in {"offline", "telegram"}:
            raise ValueError("Unknown runtime mode")
        with self.transaction():
            existing = self.meta("runtime")
            if existing and existing != mode:
                raise ValueError("Use separate databases for offline and Telegram runs.")
            old_bot = self.meta("bot_id")
            if old_bot is not None and str(bot_id) != old_bot:
                raise ValueError("This database belongs to a different Telegram bot.")
            self.set_meta("runtime", mode)
            if bot_id is not None:
                self.set_meta("bot_id", bot_id)

    @property
    def offset(self):
        return int(self.meta("next_offset", 0))

    def seen(self, update_id):
        return self.connection.execute(
            "SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)).fetchone() is not None

    def mark_processed(self, update_id, now):
        self.connection.execute("INSERT INTO processed_updates VALUES (?,?)", (update_id, now))
        self.set_meta("next_offset", max(self.offset, update_id + 1))

    def conversation(self, user_id):
        row = self.connection.execute(
            "SELECT step,data FROM conversations WHERE user_id=?", (user_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save_conversation(self, user_id, step, data, now):
        self.connection.execute(
            "INSERT INTO conversations VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE "
            "SET step=excluded.step,data=excluded.data,updated_at=excluded.updated_at",
            (user_id, step, json.dumps(data, ensure_ascii=False), now))

    def cancel_conversation(self, user_id):
        self.connection.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))

    def allow_message(self, user_id, now, limit, window=60):
        row = self.connection.execute("SELECT * FROM rate_limits WHERE user_id=?", (user_id,)).fetchone()
        if row is None or now - row["window_start"] >= window or now < row["window_start"]:
            self.connection.execute(
                "INSERT INTO rate_limits VALUES (?,?,1,0) ON CONFLICT(user_id) DO UPDATE "
                "SET window_start=excluded.window_start,count=1,warned=0", (user_id, now))
            return True, False
        if row["count"] >= limit:
            warn = not row["warned"]
            self.connection.execute("UPDATE rate_limits SET warned=1 WHERE user_id=?", (user_id,))
            return False, warn
        self.connection.execute("UPDATE rate_limits SET count=count+1 WHERE user_id=?", (user_id,))
        return True, False

    def create_ticket(self, message, data, now):
        cursor = self.connection.execute(
            "INSERT INTO tickets(user_id,chat_id,name,need,contact,status,created_at) "
            "VALUES (?,?,?,?,?,'open',?)",
            (message.user_id, message.chat_id, data["name"], data["need"], data["contact"], now))
        return cursor.lastrowid

    def ticket(self, ticket_id):
        return self.connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()

    def open_count(self, user_id):
        return self.connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (user_id,)).fetchone()[0]

    def user_tickets(self, user_id, ticket_id=None):
        if ticket_id is not None:
            return self.connection.execute(
                "SELECT * FROM tickets WHERE user_id=? AND id=?", (user_id, ticket_id)).fetchall()
        return self.connection.execute(
            "SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 3", (user_id,)).fetchall()

    def queue(self, page=1):
        return self.connection.execute(
            "SELECT * FROM tickets WHERE status='open' ORDER BY id LIMIT 10 OFFSET ?",
            ((page - 1) * 10,)).fetchall()

    def recent_tickets(self, user_id=None, limit=20):
        """Read the small ticket list used by the local demo."""
        if user_id is not None:
            return self.connection.execute(
                "SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        return self.connection.execute(
            "SELECT * FROM tickets ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def ticket_counts(self, user_id=None):
        where = " WHERE user_id=?" if user_id is not None else ""
        parameters = (user_id,) if user_id is not None else ()
        rows = self.connection.execute(
            "SELECT status,COUNT(*) AS total FROM tickets" + where + " GROUP BY status",
            parameters).fetchall()
        counts = {"open": 0, "closed": 0}
        counts.update({row["status"]: row["total"] for row in rows})
        return counts

    def close_ticket(self, ticket_id, note, now):
        self.connection.execute(
            "UPDATE tickets SET status='closed',closure_note=?,closed_at=? WHERE id=?",
            (note, now, ticket_id))

    def enqueue(self, update_id, ordinal, chat_id, text):
        if not 1 <= len(text) <= 4096:
            raise ValueError("Outgoing message length is outside Telegram limits")
        self.connection.execute(
            "INSERT INTO outbox(update_id,ordinal,chat_id,text) VALUES (?,?,?,?)",
            (update_id, ordinal, chat_id, text))

    def pending(self, limit=100):
        return self.connection.execute(
            "SELECT * FROM outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()

    def delivery_result(self, message_id, status, code=None):
        if status not in {"sent", "failed"}:
            raise ValueError("Invalid outbox status")
        self.connection.execute(
            "UPDATE outbox SET status=?,failure_code=? WHERE id=?", (status, code, message_id))
