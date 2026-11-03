"""Polling orchestration and durable outgoing-message delivery."""
import logging
import time
from .transport import TransportError, parse_message


log = logging.getLogger(__name__)


class Runner:
    def __init__(self, service, api):
        self.service, self.store, self.api = service, service.store, api

    def flush(self):
        """Return a retry delay if delivery is temporarily unavailable."""
        for row in self.store.pending():
            try:
                self.api.send_message(row["chat_id"], row["text"])
            except TransportError as error:
                if error.permanent:
                    self.store.delivery_result(row["id"], "failed", error.code)
                    log.warning("Outbox %s failed permanently (code %s)", row["id"], error.code)
                    continue
                return error.retry_after or 2
            self.store.delivery_result(row["id"], "sent")
        return 0

    def poll_once(self):
        delay = self.flush()
        if delay:
            return delay
        updates = self.api.get_updates(self.store.offset)
        valid = [u for u in updates if isinstance(u, dict) and type(u.get("update_id")) is int and u["update_id"] >= 0]
        for update in sorted(valid, key=lambda u: u["update_id"]):
            message = parse_message(update)
            if message:
                self.service.handle(message)
            else:
                self.service.ignore(update["update_id"])
            delay = self.flush()
            if delay:
                return delay
        return 0

    def run_forever(self, sleep=time.sleep):
        failures = 0
        while True:
            try:
                delay = self.poll_once()
                failures = 0
            except TransportError as error:
                if error.permanent:
                    raise
                failures += 1
                delay = error.retry_after or min(2 ** min(failures, 5), 30)
                log.warning("Telegram temporarily unavailable; retrying in %s seconds", delay)
            if delay:
                sleep(delay)
