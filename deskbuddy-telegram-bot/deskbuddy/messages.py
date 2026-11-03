"""Hebrew prompts and ticket text, separate from state and role checks."""

WELCOME = (
    "היי, אני דסקבאדי 🐾\nעוזר קטן שמסדר בקשות לעסק.\n"
    "/new לפתיחת בקשה\n/status למעקב\n/help לעזרה"
)
HELP = (
    "🐾 פותחים בקשה עם /new ועונים על כמה שאלות.\n"
    "/status מציג רק את הבקשות שלך.\n/cancel מבטל טיוטה שטרם נשלחה.\n"
    "נא לא לשלוח סיסמאות, פרטי תשלום או מידע רגיש."
)
RATE_LIMITED = "רגע קטן לנשום 🐾 נסו שוב בעוד דקה. הטיוטה נשמרה."
TEXT_TOO_LONG = "ההודעה ארוכה מדי או מכילה תו לא נתמך. שלחו עד 2,000 תווים."
PROMPT_NAME = "נעים להכיר 🐾 איך קוראים לך או לעסק? (2–80 תווים)"
PROMPT_NEED = "מה צריך לפתור? ספרו מה קורה היום ומה התוצאה הרצויה. (10–1,000 תווים)"
PROMPT_CONTACT = "איך נוח לחזור אליך? אפשר לכתוב דוא״ל, טלפון או ״כאן בצ׳אט״. (3–120 תווים)"


def confirmation(data: dict[str, str]) -> str:
    return (
        f"כמעט שם 🐾\nשם: {data['name']}\nבקשה: {data['need']}\n"
        f"דרך קשר: {data['contact']}\n\nלשליחה כתבו ״שלח״ או send. /cancel לביטול.\n"
        "הפרטים יועברו למנהלי העסק; עדכון סגירה יישלח כאן."
    )


def ticket_created(ticket_id: int) -> str:
    return (
        f"הבקשה נשלחה 🐾 המספר שלך הוא #{ticket_id}.\nלמעקב: /status {ticket_id}\n"
        "הבקשה עדיין אינה הזמנה או התחייבות למחיר ולמועד."
    )


def new_ticket_for_admin(ticket_id: int, data: dict[str, str]) -> str:
    return (
        f"🐾 בקשה חדשה #{ticket_id}\n{data['name']}\n{data['need'][:160]}\n"
        f"לפרטים: /ticket {ticket_id}"
    )


def ticket_details(ticket) -> str:
    status = "פתוחה" if ticket["status"] == "open" else "נסגרה"
    return (
        f"בקשה #{ticket['id']}\nשם: {ticket['name']}\nבקשה: {ticket['need']}\n"
        f"קשר: {ticket['contact']}\nמצב: {status}"
    )
