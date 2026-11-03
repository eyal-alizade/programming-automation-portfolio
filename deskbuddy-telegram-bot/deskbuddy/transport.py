"""Small Telegram HTTP adapter. No network is used by the offline transports."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .models import Message


class TransportError(Exception):
    def __init__(self, message, *, code=None, retry_after=None, permanent=False):
        super().__init__(message)
        self.code, self.retry_after, self.permanent = code, retry_after, permanent


def parse_message(update):
    """Only ordinary, human-authored private text messages enter the core."""
    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
        return None
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    sender, chat = msg.get("from"), msg.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    if chat.get("type") != "private" or sender.get("is_bot") or not isinstance(msg.get("text"), str):
        return None
    if any(type(v) is not int or v <= 0 for v in (sender.get("id"), chat.get("id"))):
        return None
    return Message(update["update_id"], sender["id"], chat["id"], msg["text"])


class TelegramAPI:
    MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, token, opener=urlopen):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("TELEGRAM_BOT_TOKEN is missing or has an invalid format")
        self._token, self._opener = token, opener

    def _request(self, method, payload, timeout=35):
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener(request, timeout=timeout) as response:
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            try:
                body = error.read(16384)
            except OSError:
                body = b""
            finally:
                error.close()
            self._raise_error(method, error.code, body)
        except (URLError, OSError, TimeoutError):
            # Do not expose exception strings: urllib errors can include the token-bearing URL.
            raise TransportError("Telegram connection unavailable") from None
        if len(body) > self.MAX_RESPONSE_BYTES:
            raise TransportError("Telegram response exceeded the size limit")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise TransportError("Telegram returned an invalid JSON response") from None
        if not isinstance(value, dict) or value.get("ok") is not True:
            code = value.get("error_code", 0) if isinstance(value, dict) else 0
            self._raise_error(method, code, body)
        return value.get("result")

    @staticmethod
    def _raise_error(method, code, body):
        if type(code) is not int or not 100 <= code <= 599:
            code = 0
        retry_after = None
        if code == 429:
            try:
                parsed = json.loads(body)
                candidate = parsed.get("parameters", {}).get("retry_after")
                if type(candidate) is int and candidate > 0:
                    retry_after = candidate
            except (ValueError, UnicodeError, AttributeError):
                pass
        raise TransportError(
            f"Telegram rejected {method} (HTTP {code})", code=code,
            retry_after=retry_after, permanent=code in {400, 401, 403, 404, 409}) from None

    def get_me(self):
        result = self._request("getMe", {})
        if not isinstance(result, dict) or type(result.get("id")) is not int:
            raise TransportError("Telegram returned invalid bot information")
        return result

    def get_updates(self, offset, timeout=25):
        result = self._request("getUpdates", {
            "offset": offset, "timeout": timeout, "limit": 50, "allowed_updates": ["message"]},
            timeout=timeout + 10)
        if not isinstance(result, list):
            raise TransportError("Telegram returned an invalid update list")
        return result

    def send_message(self, chat_id, text):
        return self._request("sendMessage", {"chat_id": chat_id, "text": text})


class FakeAPI:
    """In-memory transport for demos/tests. It never constructs HTTP requests."""
    def __init__(self, updates=()):
        self.updates, self.messages, self.offsets = list(updates), [], []
        self.failure = None

    def get_updates(self, offset, timeout=25):
        self.offsets.append(offset)
        return [u for u in self.updates if u["update_id"] >= offset][:50]

    def send_message(self, chat_id, text):
        if self.failure:
            raise self.failure
        self.messages.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.messages)}
