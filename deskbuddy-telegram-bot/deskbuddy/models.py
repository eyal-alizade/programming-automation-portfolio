from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Message:
    update_id: int
    user_id: int
    chat_id: int
    text: str
    chat_type: str = "private"


@dataclass(frozen=True, slots=True)
class Outcome:
    duplicate: bool = False
    replies_queued: int = 0
    rate_limited: bool = False
