"""Pure business rules used by handlers and tests."""

from __future__ import annotations

import random
import re
import unicodedata
from typing import Iterable


SUPPORTED_TARGETS = {
    "text": "Text Messages",
    "photo": "Photo Messages",
    "video": "Video Messages",
    "document": "Document Messages",
    "audio": "Audio Messages",
}
DEFAULT_TARGETS = tuple(SUPPORTED_TARGETS)
ALLOWED_DELAYS = {0, 5, 10, 30, 60, 300}
ALLOWED_REACTION_DELAYS = {0, 2, 5, 10, 30}
JOIN_MODES = {"auto", "delayed", "decline", "manual"}


def is_valid_emoji(value: str) -> bool:
    """Accept a single normal Unicode emoji sequence, not arbitrary text."""
    value = value.strip()
    if not value or len(value) > 16 or any(ch.isspace() for ch in value):
        return False
    if any(ch.isalnum() or ch in "_-." for ch in value):
        return False
    has_symbol = any(
        unicodedata.category(ch) in {"So", "Sk"} for ch in value
    )
    return has_symbol


def parse_reaction_input(text: str) -> tuple[str, int] | None:
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    emoji, percentage = parts
    if not is_valid_emoji(emoji):
        return None
    try:
        weight = int(percentage)
    except ValueError:
        return None
    if not 0 < weight <= 100:
        return None
    return emoji, weight


def validate_reaction_set(reactions: Iterable[dict]) -> tuple[bool, str]:
    total = sum(int(item.get("percentage", 0)) for item in reactions)
    if total > 100:
        return False, f"Total percentage is {total}%. It cannot exceed 100%."
    if total < 100:
        return False, (
            f"Total percentage is {total}%.\n\n"
            f"Please add another {100 - total}%."
        )
    return True, "100% ✅"


def choose_weighted_reaction(reactions: Iterable[dict], rng=None) -> str | None:
    items = [
        (str(item["emoji"]), int(item["percentage"]))
        for item in reactions
        if item.get("emoji") and int(item.get("percentage", 0)) > 0
    ]
    if not items:
        return None
    chooser = rng or random
    return chooser.choices(
        [emoji for emoji, _ in items],
        weights=[weight for _, weight in items],
        k=1,
    )[0]


def message_target(message) -> str | None:
    if getattr(message, "text", None):
        return "text"
    for target in ("photo", "video", "document", "audio"):
        if getattr(message, target, None):
            return target
    return None


def targets_match(message, targets: Iterable[str]) -> bool:
    target = message_target(message)
    selected = set(targets)
    return target is not None and (
        "all" in selected or target in selected
    )


def render_welcome(template: str, user, chat_title: str) -> str:
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    username = getattr(user, "username", "") or ""
    values = {
        "{first_name}": first,
        "{last_name}": last,
        # Keep the placeholder value bare so templates can choose whether to
        # render an @ prefix without accidentally producing @@username.
        "{username}": username,
        "{user_id}": str(getattr(user, "id", "")),
        "{chat_title}": chat_title,
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template


def normalize_chat_reference(value: str) -> str | int | None:
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"-100\d{5,}", value):
        return int(value)
    if value.startswith("https://t.me/"):
        value = value.removeprefix("https://t.me/").split("/", 1)[0]
    if re.fullmatch(r"@[A-Za-z0-9_]{4,}", value):
        return value
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", value):
        return "@" + value
    return None