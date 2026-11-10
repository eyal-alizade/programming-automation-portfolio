"""Render readable reports and publish only into a new output directory."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .analysis import FIELD_LABELS

LIMITATION = "הדוח מבוסס על התצפיות שהוזנו בלבד. הכלי אינו מחפש ברשת, אינו פותח קישורים ואינו מאמת את זהות העסק או את נכונות המקורות. פרט שלא תועד הוא לא ידוע; הוא אינו הוכחה שהמידע חסר באינטרנט."
SYNTHETIC_NOTICE = "SYNTHETIC · נתוני הדגמה בדיוניים בלבד. העסק, פרטי הקשר והתצפיות נוצרו לתיק העבודות; לא בוצעה בדיקה של עסק אמיתי."
REAL_NOTICE = "תצפיות שסיפק עורך הדוח. סימון פרטי הייחוס כמאושרים הוא הצהרת מזין הנתונים; התוכנה אינה מאמתת הרשאה או אישור בעלים."


def _notice(data: dict) -> str:
    return SYNTHETIC_NOTICE if data["data_kind"] == "synthetic" else REAL_NOTICE


def _md(text: str) -> str:
    """Escape prose so supplied text does not become Markdown/embedded HTML."""
    escaped = html.escape(text, quote=False)
    for char in "\\`*_{}[]()#+!|":
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def markdown(result: dict) -> str:
    data = result["data"]
    source_map = {source["id"]: source for source in data["sources"]}
    lines = [
        f"# בדיקת נוכחות עסקית — {_md(data['business']['name'])}",
        "", _notice(data), "",
        f"נכללו {len(data['sources'])} מקורות שסופקו ו-{len(result['findings'])} ממצאים לבדיקה.",
        "", "## ממצאים והמשך טיפול", "",
    ]
    if not result["findings"]:
        lines.append("לא נמצאו הבדלים או קישורים שסומנו כלא תקינים בתצפיות שסופקו. זו אינה קביעה שאין בעיות ברשת.")
    for number, finding in enumerate(result["findings"], 1):
        lines.extend([
            f"### {number}. {_md(finding['title'])}", "",
            _md(finding["description"]), "",
            *[f"- תיעוד: {_md(item)}" for item in finding["evidence"]], "",
            f"המשך טיפול: {_md(finding['action'])}", "",
        ])
        for source_id in finding["source_ids"]:
            source = source_map[source_id]
            lines.append(f"מקור {_md(source_id)} — {_md(source['label'])}, תצפית {source['observed_on']}: <{source['url']}>")
        lines.append("")
    lines.extend(["## פרטים שלא תועדו", ""])
    if result["unknown_contacts"]:
        for item in result["unknown_contacts"]:
            lines.append(f"- {FIELD_LABELS[item['field']]}: לא תועד במקורות {_md(', '.join(item['source_ids']))}.")
    else:
        lines.append("כל שדות הקשר תועדו במקורות שסופקו. אין בכך אימות שהפרטים נכונים.")
    lines.extend(["", "## מקורות שנכללו", ""])
    for source in data["sources"]:
        lines.append(f"- {_md(source['id'])} · {_md(source['label'])} · {source['observed_on']} · <{source['url']}>")
    lines.extend(["", "## גבולות הבדיקה", "", LIMITATION, "", "אין בדוח אישור אבטחה, בדיקת רקע או מסקנה שאין מידע נוסף ברשת.", ""])
    return "\n".join(lines)


def html_report(result: dict) -> str:
    data = result["data"]
    e = html.escape
    sources = {source["id"]: source for source in data["sources"]}
    findings = []
    for number, finding in enumerate(result["findings"], 1):
        citations = " · ".join(
            f'<a href="{e(sources[source_id]["url"], quote=True)}">{e(source_id)} — {e(sources[source_id]["label"])}</a>'
            for source_id in finding["source_ids"]
        )
        evidence = "".join(f'<p class="evidence">{e(item)}</p>' for item in finding["evidence"])
        findings.append(f"""
        <article class="finding">
            <div class="finding-number">ממצא {number:02d}</div>
            <h3>{e(finding['title'])}</h3>
            <p>{e(finding['description'])}</p>
            {evidence}
            <p class="muted">מקורות: {citations}</p>
            <p class="action"><strong>המשך טיפול:</strong> {e(finding['action'])}</p>
        </article>""")
    if not findings:
        findings.append("<p>לא נמצאו הבדלים או קישורים שסומנו כלא תקינים בקלט. זו אינה קביעה שאין בעיות ברשת.</p>")
    rows = []
    for source in data["sources"]:
        values = []
        for field in FIELD_LABELS:
            value = source["contacts"][field]
            values.append(f'<bdi dir="ltr">{e(value)}</bdi>' if value is not None else "לא תועד")
        rows.append(f"<tr><td>{e(source['label'])}</td><td>{values[0]}</td><td>{values[1]}</td><td>{values[2]}</td></tr>")
    source_list = "".join(
        f'<li><strong>{e(source["id"])} · {e(source["label"])}</strong> — תצפית {source["observed_on"]}<br><a href="{e(source["url"], quote=True)}">{e(source["url"])}</a></li>'
        for source in data["sources"]
    )
    unknown = "".join(
        f"<li>{FIELD_LABELS[item['field']]}: לא תועד במקורות {e(', '.join(item['source_ids']))}.</li>"
        for item in result["unknown_contacts"]
    ) or "<li>כל שדות הקשר תועדו בקלט. אין בכך אימות שהפרטים נכונים.</li>"
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    dates = sorted({source["observed_on"] for source in data["sources"]})
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'">
    <title>בדיקת נוכחות עסקית — {e(data['business']['name'])}</title>
    <style>{css}</style>
</head>
<body>
<main>
    <header>
        <div class="eyebrow">בדיקת נוכחות עסקית · דוח תצפיות</div>
        <h1>{e(data['business']['name'])}</h1>
        <p>מה מופיע במקורות שסופקו, ואילו פרטים כדאי לבדוק ולעדכן.</p>
        <p class="muted">תאריכי התצפיות שסופקו: {e(' · '.join(dates))}</p>
        <div class="notice">{e(_notice(data))}</div>
        <div class="summary">
            <span><strong>{len(data['sources'])}</strong>מקורות שסופקו</span>
            <span><strong>{len(result['findings'])}</strong>ממצאים לבדיקה</span>
            <span><strong>ללא איסוף רשת</strong>ניתוח תצפיות שנרשמו מראש</span>
        </div>
    </header>
    <section aria-labelledby="findings-title">
        <h2 id="findings-title">ממצאים והמשך טיפול</h2>
{''.join(findings)}
    </section>
    <section aria-labelledby="contacts-title">
        <h2 id="contacts-title">פרטי הקשר כפי שתועדו</h2>
        <div class="table-wrap">
            <table><thead><tr><th>מקור</th><th>דוא״ל</th><th>טלפון</th><th>אתר</th></tr></thead>
            <tbody>{''.join(rows)}</tbody></table>
        </div>
        <h3>פרטים שלא תועדו</h3>
        <ul>{unknown}</ul>
    </section>
    <section aria-labelledby="sources-title">
        <h2 id="sources-title">מקורות שנכללו</h2>
        <ol class="source-list">{source_list}</ol>
    </section>
    <footer>
        <p>{e(LIMITATION)}</p>
        <p class="muted">אין בדוח אישור אבטחה, בדיקת רקע או מסקנה שאין מידע נוסף ברשת.</p>
    </footer>
</main>
</body>
</html>
"""


def write_reports(result: dict, output: Path) -> None:
    """Fail before touching an existing directory; a new directory is never reused."""
    documents = {
        "report.md": markdown(result),
        "report.html": html_report(result),
        "observations.json": json.dumps(result["data"], ensure_ascii=False, indent=2) + "\n",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    for name, content in documents.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
