# איחוד קובצי מכירות לדוח אחד

מקבלים כמה קובצי מכירות ורוצים סיכום אחד, בלי להעתיק שורות ולבנות נוסחאות.
הכלי מאחד את הקבצים, מסיר שורות זהות ומפיק סיכום כמויות וסכומים לפי פריט.
קובצי המקור נשארים כפי שהם.

## הדוגמה שבפרויקט

שני קובצי הדוגמה כוללים **6 רשומות**. התוצאה היא **5 רשומות**, כפילות מדויקת
אחת שהוסרה וסכום של **185.10 ₪**. הנתונים נוצרו לתיק העבודות.
אפשר לפתוח [סיכום לפי פריט](examples/summary.csv) או [דוח טקסט](examples/report.txt)
שנוצרו בהרצה בפועל.

| קובץ תוצאה | מה יש בו |
| --- | --- |
| `consolidated.csv` | כל הרשומות שאוחדו, עם סכום לכל שורה ומקור הרשומה |
| `summary.csv` | כמות וסכום לכל פריט |
| `report.txt` | סיכום קצר לקריאה |
| `report.json` | אותו סיכום לשילוב עם תוכנות אחרות |

## הפעלה

צריך Python 3.11 ומעלה, ללא ספריות נוספות. מתוך תיקיית הפרויקט:

```sh
python -m csv_report_builder samples/sales-a.csv samples/sales-b.csv --output demo-output --currency ILS
```

```text
Exported 5 rows; removed 1 exact duplicates.
Grand total: 185.10 ILS
Reports: demo-output
```

פותחים את `summary.csv` בגיליון או את `report.txt` בעורך טקסט.
התיקייה צריכה להיות חדשה. החלפה מכוונת של ארבעת קובצי הדוח דורשת `--overwrite`;
קבצים אחרים באותה תיקייה נשמרים.

## מבנה הקלט

| עמודה | תוכן | שם חלופי נתמך |
| --- | --- | --- |
| `date` | תאריך תקין, `YYYY-MM-DD` | `sale_date` |
| `item` | שם פריט | `product` |
| `quantity` | כמות שלמה וחיובית | `qty` |
| `unit_price` | מחיר לא שלילי, עד שתי ספרות אחרי הנקודה | `price` |

עמודות נוספות נשמרות. נתמכים פסיק, נקודה־פסיק, טאב וקו אנכי, בקידוד UTF-8
עם או בלי BOM. בשורה לא תקינה הפעולה נעצרת לפני פרסום הדוחות ומציינת את מקומה.

כפילות פירושה שכל ערכי השורה זהים. אם שתי שורות זהות הן מכירות נפרדות, מוסיפים
`--keep-duplicates`. כל הקבצים חייבים להשתמש באותו מטבע; `--currency` מסמן אותו
ואינו מבצע המרה. אין תמיכה בהחזרות עם כמויות או מחירים שליליים.

## Developer notes

`csv_report_builder.py` separates parsing/validation, deduplication, Decimal
aggregation, CSV/text formatting and staged publication. The code keeps rows and
duplicate keys in memory, so memory use grows with input size.

Headers are case-insensitive; whitespace becomes underscores before aliases are
applied. Duplicate/reserved headers fail validation. Deduplication compares exact
decoded cells under those names, including extras; `1.0` and `1.00` differ.
Rows are counted as parsed CSV records, not physical lines inside quoted fields.
Provenance uses filenames and input indices, without local directory paths.

Money uses input-sized Decimal precision and JSON decimal strings. Numeric input
supports at most 100 integer digits. CSV exports use UTF-8 BOM and prefix potential
spreadsheet formulas in text, headers and filenames with an apostrophe.
Use `--delimiter comma|semicolon|tab|pipe` if automatic detection is ambiguous.

Inputs cannot be overwritten by report filenames; output symlinks are refused.
Outputs are staged before publication. Each replacement is atomic, but the four
files are not one transaction: an overwrite I/O failure may leave mixed versions.
Empty inputs fail; a header-only file may accompany files containing records.

```sh
python -m unittest discover -s tests -v
```

14 temporary-file tests cover CSV parsing, exact arithmetic, duplicate handling,
validation, formula protection and existing-file preservation. Spreadsheet display
on Windows has not been verified.

אייל אליזאדה / Eyal Alizade · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
