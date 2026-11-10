"""Compare recorded values; do not infer that an unobserved value is absent."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .schema import CONTACT_FIELDS

FIELD_LABELS = {"email": "דוא״ל", "phone": "טלפון", "website": "כתובת אתר"}


def comparable(field: str, value: str) -> str:
    if field == "phone":
        return re.sub(r"\D", "", value)
    if field == "email":
        return value.casefold()
    parsed = urlsplit(value)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}" + (
        f"?{parsed.query}" if parsed.query else ""
    )


def review(data: dict) -> dict:
    findings = []
    unknown = []
    reference = data["business"]["contacts"]
    for field in CONTACT_FIELDS:
        recorded = [source for source in data["sources"] if source["contacts"][field] is not None]
        missing = [source["id"] for source in data["sources"] if source["contacts"][field] is None]
        if missing:
            unknown.append({"field": field, "source_ids": missing})
        expected = reference[field]
        if expected is not None:
            differing = [source for source in recorded if comparable(field, source["contacts"][field]) != comparable(field, expected)]
            if differing:
                findings.append({
                    "kind": "reference_difference",
                    "title": f"{FIELD_LABELS[field]} שונה מפרטי הייחוס",
                    "description": f"פרטי הייחוס שסומנו כמאושרים: {expected}. יש לאשר את התיקון מול בעל העסק לפני שינוי עמוד.",
                    "action": "לוודא שהמקור אכן מתייחס לעסק ולעדכן את הפרט לאחר אישור בעל העסק.",
                    "field": field,
                    "source_ids": [source["id"] for source in differing],
                    "evidence": [f"{source['id']}: {source['contacts'][field]}" for source in differing],
                })
        elif len({comparable(field, source["contacts"][field]) for source in recorded}) > 1:
            findings.append({
                "kind": "source_disagreement",
                "title": f"{FIELD_LABELS[field]} אינו אחיד בין המקורות",
                "description": "אין בקלט פרט ייחוס מאושר לשדה הזה. ההבדל דורש בירור ואינו קובע איזה מקור נכון.",
                "action": "לבקש מבעל העסק את הפרט העדכני לפני המלצה על שינוי.",
                "field": field,
                "source_ids": [source["id"] for source in recorded],
                "evidence": [f"{source['id']}: {source['contacts'][field]}" for source in recorded],
            })
    for source in data["sources"]:
        for link in source["links"]:
            if link["state"] == "broken":
                findings.append({
                    "kind": "reported_broken_link",
                    "title": "קישור שסומן כלא תקין בתצפית",
                    "description": link["note"],
                    "action": "לבדוק שוב את הקישור במועד הטיפול; אם התקלה נמשכת, לתקן את הכתובת או להסיר את הקישור.",
                    "field": "link",
                    "source_ids": [source["id"]],
                    "evidence": [link["url"]],
                })
    return {"data": data, "findings": findings, "unknown_contacts": unknown}
