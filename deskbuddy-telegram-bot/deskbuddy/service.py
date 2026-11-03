"""Transport-independent intake state machine and authorization rules."""
import time
from .db import Store
from .models import Message, Outcome


WELCOME = "היי, אני דסקבאדי 🐾\nעוזר קטן שמסדר בקשות לעסק.\n/new לפתיחת בקשה\n/status למעקב\n/help לעזרה"
HELP = "🐾 פותחים בקשה עם /new ועונים על כמה שאלות.\n/status מציג רק את הבקשות שלך.\n/cancel מבטל טיוטה שטרם נשלחה.\nנא לא לשלוח סיסמאות, פרטי תשלום או מידע רגיש."


class BotService:
    def __init__(self, store: Store, admin_ids, clock=time.time, rate_limit=12):
        ids = frozenset(admin_ids)
        if not ids or len(ids) > 20 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("Configure between 1 and 20 positive administrator IDs")
        if not 1 <= rate_limit <= 100:
            raise ValueError("Rate limit must be between 1 and 100")
        self.store, self.admin_ids, self.clock, self.rate_limit = store, ids, clock, rate_limit

    def ignore(self, update_id):
        if type(update_id) is not int or update_id < 0:
            return
        with self.store.transaction():
            if not self.store.seen(update_id):
                self.store.mark_processed(update_id, self.clock())

    def handle(self, message: Message):
        if (type(message.update_id) is not int or message.update_id < 0 or
                type(message.user_id) is not int or message.user_id <= 0 or
                type(message.chat_id) is not int or message.chat_id == 0 or
                (message.chat_type == "private" and message.chat_id < 0)):
            raise ValueError("Invalid message identifier")
        now = self.clock()
        with self.store.transaction():
            if self.store.seen(message.update_id):
                return Outcome(duplicate=True)
            replies = []
            limited = False
            if message.chat_type == "private":
                allowed, warn = self.store.allow_message(message.user_id, now, self.rate_limit)
                if not allowed:
                    limited = True
                    if warn:
                        replies.append((message.chat_id, "רגע קטן לנשום 🐾 נסו שוב בעוד דקה. הטיוטה נשמרה."))
                elif len(message.text) > 2000 or "\x00" in message.text:
                    replies.append((message.chat_id, "ההודעה ארוכה מדי או מכילה תו לא נתמך. שלחו עד 2,000 תווים."))
                else:
                    replies = self._respond(message, now)
            for ordinal, (chat_id, text) in enumerate(replies):
                self.store.enqueue(message.update_id, ordinal, chat_id, text)
            self.store.mark_processed(message.update_id, now)
            return Outcome(replies_queued=len(replies), rate_limited=limited)

    def _respond(self, msg, now):
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        command = parts[0].split("@")[0].lower() if parts and text.startswith("/") else ""
        argument = parts[1].strip() if len(parts) == 2 else ""
        reply = lambda value: [(msg.chat_id, value)]
        if command == "/start":
            return reply(WELCOME + ("\nלניהול: /queue, /ticket, /close" if msg.user_id in self.admin_ids else ""))
        if command == "/help":
            return reply(HELP + ("\nניהול: /queue [עמוד], /ticket מספר, /close מספר [הערה]" if msg.user_id in self.admin_ids else ""))
        if command == "/cancel":
            had_draft = self.store.conversation(msg.user_id) is not None
            self.store.cancel_conversation(msg.user_id)
            return reply("הטיוטה בוטלה 🐾 אפשר להתחיל מחדש עם /new." if had_draft else "אין טיוטה לביטול. בקשה שנשלחה נשארת במעקב עם /status.")
        if command in {"/queue", "/ticket", "/close"}:
            if msg.user_id not in self.admin_ids:
                return reply("הפקודה זמינה למנהלי העסק בלבד. לבקשות שלך: /status.")
            return self._admin(msg, command, argument, now)
        if command == "/status":
            ticket_id = self._positive_id(argument) if argument else None
            if argument and ticket_id is None:
                return reply("לדוגמה: /status 12, או /status לשלוש הבקשות האחרונות שלך.")
            tickets = self.store.user_tickets(msg.user_id, ticket_id)
            if not tickets:
                return reply("לא נמצאו בקשות שלך. אפשר לפתוח בקשה עם /new.")
            return reply("🐾 הבקשות שלך:\n" + "\n".join(
                f"#{r['id']} — {'פתוחה' if r['status']=='open' else 'נסגרה'} — {r['need'][:80]}" for r in tickets))
        if command == "/new":
            if self.store.conversation(msg.user_id):
                return reply("כבר יש טיוטה פתוחה 🐾 אפשר להמשיך לענות או לבטל עם /cancel.")
            if self.store.open_count(msg.user_id) >= 3:
                return reply("כבר יש לך שלוש בקשות פתוחות. אפשר לבדוק אותן עם /status.")
            self.store.save_conversation(msg.user_id, "name", {}, now)
            return reply("נעים להכיר 🐾 איך קוראים לך או לעסק? (2–80 תווים)")
        if command:
            return reply("לא הכרתי את הפקודה הזו. /help מציג את האפשרויות 🐾")
        conversation = self.store.conversation(msg.user_id)
        if not conversation:
            return reply("אני כאן כדי לסדר בקשה לעסק 🐾 מתחילים עם /new.")
        step, data = conversation
        if step == "name":
            if not 2 <= len(text) <= 80:
                return reply("מה השם? אפשר להזין בין 2 ל־80 תווים.")
            data["name"] = text
            self.store.save_conversation(msg.user_id, "need", data, now)
            return reply("מה צריך לפתור? ספרו מה קורה היום ומה התוצאה הרצויה. (10–1,000 תווים)")
        if step == "need":
            if not 10 <= len(text) <= 1000:
                return reply("כדי להבין את הבקשה, כתבו בין 10 ל־1,000 תווים.")
            data["need"] = text
            self.store.save_conversation(msg.user_id, "contact", data, now)
            return reply("איך נוח לחזור אליך? אפשר לכתוב דוא״ל, טלפון או ״כאן בצ׳אט״. (3–120 תווים)")
        if step == "contact":
            if not 3 <= len(text) <= 120:
                return reply("דרך קשר צריכה להיות בין 3 ל־120 תווים. אין צורך בפרטים רגישים.")
            data["contact"] = text
            self.store.save_conversation(msg.user_id, "confirm", data, now)
            return reply(f"כמעט שם 🐾\nשם: {data['name']}\nבקשה: {data['need']}\nדרך קשר: {data['contact']}\n\nלשליחה כתבו ״שלח״ או send. /cancel לביטול.\nהפרטים יועברו למנהלי העסק; עדכון סגירה יישלח כאן.")
        if text.casefold() not in {"שלח", "אישור", "send", "yes"}:
            return reply("כדי לשלוח כתבו ״שלח״ או send. לביטול: /cancel.")
        if self.store.open_count(msg.user_id) >= 3:
            return reply("כבר יש שלוש בקשות פתוחות. הטיוטה נשמרה; אפשר לבדוק /status.")
        ticket_id = self.store.create_ticket(msg, data, now)
        self.store.cancel_conversation(msg.user_id)
        replies = reply(f"הבקשה נשלחה 🐾 המספר שלך הוא #{ticket_id}.\nלמעקב: /status {ticket_id}\nהבקשה עדיין אינה הזמנה או התחייבות למחיר ולמועד.")
        for admin_id in sorted(self.admin_ids):
            replies.append((admin_id, f"🐾 בקשה חדשה #{ticket_id}\n{data['name']}\n{data['need'][:160]}\nלפרטים: /ticket {ticket_id}"))
        return replies

    @staticmethod
    def _positive_id(text):
        return int(text) if text.isascii() and text.isdecimal() and 1 <= len(text) <= 12 and int(text) > 0 else None

    def _admin(self, msg, command, argument, now):
        reply = lambda text: [(msg.chat_id, text)]
        if command == "/queue":
            page = self._positive_id(argument) if argument else 1
            if page is None or page > 10000:
                return reply("שימוש: /queue [עמוד חיובי]")
            rows = self.store.queue(page)
            if not rows:
                return reply("התור כאן ריק 🐾")
            return reply(f"🐾 בקשות פתוחות — עמוד {page}\n" + "\n".join(
                f"#{r['id']} · {r['name'][:30]} · {r['need'][:70]}" for r in rows) + f"\nעמוד נוסף: /queue {page+1}")
        split = argument.split(maxsplit=1)
        ticket_id = self._positive_id(split[0]) if split else None
        if ticket_id is None:
            return reply(f"שימוש: {command} מספר בקשה" + (" [הערת סגירה]" if command == "/close" else ""))
        ticket = self.store.ticket(ticket_id)
        if ticket is None:
            return reply("הבקשה לא נמצאה.")
        if command == "/ticket":
            return reply(f"בקשה #{ticket_id}\nשם: {ticket['name']}\nבקשה: {ticket['need']}\nקשר: {ticket['contact']}\nמצב: {'פתוחה' if ticket['status']=='open' else 'נסגרה'}")
        note = split[1].strip() if len(split) == 2 else ""
        if len(note) > 300:
            return reply("הערת סגירה יכולה להכיל עד 300 תווים.")
        if ticket["status"] == "closed":
            return reply("הבקשה כבר סגורה. לא נשלח עדכון נוסף.")
        self.store.close_ticket(ticket_id, note, now)
        return reply(f"בקשה #{ticket_id} נסגרה 🐾") + [
            (ticket["chat_id"], f"עדכון מדסקבאדי 🐾 בקשה #{ticket_id} נסגרה." + (f"\n{note}" if note else ""))]
