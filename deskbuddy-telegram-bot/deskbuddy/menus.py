"""Register Telegram's command list; permissions remain in BotService."""
import logging
import time

from .transport import TransportError


log = logging.getLogger(__name__)

CUSTOMER_COMMANDS = [
    {"command": "new", "description": "פתיחת בקשה חדשה"},
    {"command": "status", "description": "מצב הבקשות שלי"},
    {"command": "cancel", "description": "ביטול טיוטה שטרם נשלחה"},
    {"command": "help", "description": "עזרה והוראות שימוש"},
]
ADMIN_COMMANDS = CUSTOMER_COMMANDS + [
    {"command": "queue", "description": "תור הבקשות הפתוחות"},
    {"command": "ticket", "description": "פרטי בקשה — יש להוסיף מספר"},
    {"command": "close", "description": "סגירת בקשה — יש להוסיף מספר"},
]


def configure_command_menu(api, admin_ids, *, sleep=time.sleep):
    """A menu failure must not stop intake or discard pending replies."""
    try:
        api.set_my_commands(CUSTOMER_COMMANDS, {"type": "all_private_chats"})
        for admin_id in sorted(admin_ids):
            api.set_my_commands(ADMIN_COMMANDS, {"type": "chat", "chat_id": admin_id})
        api.set_chat_menu_button()
    except TransportError as error:
        # Do not log exception text or remote responses: they can contain secrets.
        log.warning("Command menu setup failed; continuing with typed commands. Restart to retry.")
        if error.retry_after:
            sleep(error.retry_after)
        return False
    return True
