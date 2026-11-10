"""Validate an explicit observation file; this module never opens source URLs."""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

CONTACT_FIELDS = ("email", "phone", "website")
MAX_INPUT_BYTES = 2_000_000


class InputError(ValueError):
    """A supplied observation cannot be used in a report."""


def _object(value: object, location: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{location}: expected an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise InputError(f"{location}: missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise InputError(f"{location}: unknown fields: {', '.join(sorted(extra))}")
    return value


def _text(value: object, location: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InputError(f"{location}: expected non-empty text up to {limit} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputError(f"{location}: text must be valid Unicode") from exc
    if any(ord(char) < 32 for char in value):
        raise InputError(f"{location}: control characters are not allowed")
    return value.strip()


def public_url(value: object, location: str) -> str:
    """Accept public-shaped HTTP(S) links, without claiming to verify DNS or reachability."""
    url = _text(value, location, 2048)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise InputError(f"{location}: invalid URL") from exc
    if parsed.scheme not in {"https", "http"} or not host or parsed.username is not None or parsed.password is not None:
        raise InputError(f"{location}: use an HTTP(S) URL without embedded credentials")
    if any(char in url for char in '\\<>"') or any(char.isspace() for char in url) or port not in {None, 80, 443}:
        raise InputError(f"{location}: invalid URL or nonstandard port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise InputError(f"{location}: invalid hostname") from exc
        labels = ascii_host.split(".")
        if (
            len(labels) < 2
            or len(ascii_host) > 253
            or all(label.isdigit() for label in labels)
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or ascii_host.endswith((".local", ".localhost", ".internal", ".lan"))
        ):
            raise InputError(f"{location}: expected a public hostname")
    else:
        if not address.is_global:
            raise InputError(f"{location}: local/private IP addresses are not allowed")
    return url


def _contacts(value: object, location: str) -> dict[str, str | None]:
    contacts = _object(value, location, set(), set(CONTACT_FIELDS))
    result = {}
    for field in CONTACT_FIELDS:
        item = contacts.get(field)
        if item is None:
            result[field] = None
            continue
        text = _text(item, f"{location}.{field}", 300 if field != "website" else 2048)
        if field == "email" and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", text):
            raise InputError(f"{location}.email: invalid email address")
        if field == "phone" and (not re.fullmatch(r"[+()\d .-]+", text) or not 7 <= len(re.sub(r"\D", "", text)) <= 15):
            raise InputError(f"{location}.phone: expected 7–15 digits and optional phone formatting")
        if field == "website":
            text = public_url(text, f"{location}.website")
        result[field] = text
    return result


def validate(data: object) -> dict:
    root = _object(data, "input", {"version", "data_kind", "business", "sources"})
    if type(root["version"]) is not int or root["version"] != 1:
        raise InputError("input.version: only version 1 is supported")
    if not isinstance(root["data_kind"], str) or root["data_kind"] not in {"synthetic", "public_observations"}:
        raise InputError("input.data_kind: use synthetic or public_observations")
    business = _object(root["business"], "business", {"name", "owner_confirmed_reference", "contacts"})
    if business["owner_confirmed_reference"] is not True:
        raise InputError("business.owner_confirmed_reference: must be true; confirm the reference with the owner")
    clean_business = {
        "name": _text(business["name"], "business.name", 120),
        "owner_confirmed_reference": True,
        "contacts": _contacts(business["contacts"], "business.contacts"),
    }
    sources = root["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 50:
        raise InputError("sources: expected 1–50 observations")
    seen = set()
    clean_sources = []
    for index, source in enumerate(sources, 1):
        location = f"sources[{index}]"
        source = _object(source, location, {"id", "label", "url", "observed_on", "contacts", "links"})
        source_id = _text(source["id"], f"{location}.id", 40)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", source_id) or source_id in seen:
            raise InputError(f"{location}.id: use a unique identifier containing letters, digits, _ or -")
        seen.add(source_id)
        observed_on = _text(source["observed_on"], f"{location}.observed_on", 10)
        try:
            parsed_date = date.fromisoformat(observed_on)
        except ValueError as exc:
            raise InputError(f"{location}.observed_on: expected YYYY-MM-DD") from exc
        if observed_on != parsed_date.isoformat() or parsed_date > date.today():
            raise InputError(f"{location}.observed_on: use an ISO date that is not in the future")
        links = source["links"]
        if not isinstance(links, list) or len(links) > 20:
            raise InputError(f"{location}.links: expected a list of at most 20 link observations")
        clean_links = []
        for number, link in enumerate(links, 1):
            link_location = f"{location}.links[{number}]"
            link = _object(link, link_location, {"url", "state", "note"})
            if not isinstance(link["state"], str) or link["state"] not in {"working", "broken", "not_checked"}:
                raise InputError(f"{link_location}.state: use working, broken or not_checked")
            clean_links.append({
                "url": public_url(link["url"], f"{link_location}.url"),
                "state": link["state"],
                "note": _text(link["note"], f"{link_location}.note", 500),
            })
        clean_sources.append({
            "id": source_id,
            "label": _text(source["label"], f"{location}.label", 160),
            "url": public_url(source["url"], f"{location}.url"),
            "observed_on": observed_on,
            "contacts": _contacts(source["contacts"], f"{location}.contacts"),
            "links": clean_links,
        })
    return {"version": 1, "data_kind": root["data_kind"], "business": clean_business, "sources": clean_sources}


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _text(key, "JSON key")
        if key in result:
            raise InputError(f"JSON contains a duplicate key: {key}")
        result[key] = value
    return result


def load(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise InputError("Input must be a regular JSON file, not a symbolic link")
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError(f"Input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Invalid UTF-8 JSON: {exc}") from exc
    return validate(data)
