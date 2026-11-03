# סידור קבצים אוטומטי

תיקייה עם קבצים מסוגים שונים, במקום למיין אותם אחד־אחד.
הכלי מציג את השינויים המתוכננים ומעביר את הקבצים לתיקיות לפי סוג או חודש שינוי.
אפשר להחזיר קבצים שלא השתנו למיקום המקורי.

## דוגמה

| לפני | אחרי |
| --- | --- |
| `meeting.txt` | `txt/meeting.txt` |
| `orders.csv` | `csv/orders.csv` |
| `settings.json` | `json/settings.json` |

קובץ שכבר קיים ביעד לא נדרס. תיקיות משנה נשארות במקומן.
חמשת הקבצים בדוגמה נוצרו לצורך תיק העבודות.
אפשר לקרוא את [פלט התצוגה המקדימה מהרצה בפועל](examples/preview.txt).

## הפעלה

צריך Python 3.11 ומעלה, ללא התקנת ספריות. מתוך תיקיית הפרויקט:

```sh
python sample_data.py demo-input
python organizer.py organize demo-input demo-output
```

בשלב הזה מוצגת תצוגה מקדימה בלבד:

```text
WOULD MOVE meeting.txt -> txt/meeting.txt
WOULD MOVE orders.csv -> csv/orders.csv
Planned: 5 | Skipped: 1
```

להעברה בפועל:

```sh
python organizer.py organize demo-input demo-output --apply
```

הפקודה מדפיסה נתיב לקובץ מניפסט שמתעד את ההעברות. מעתיקים את הנתיב במקום
`<manifest-path>` כדי לבדוק החזרה, ואז מוסיפים `--apply` כדי לבצע אותה:

```sh
python organizer.py undo "<manifest-path>"
python organizer.py undo "<manifest-path>" --apply
```

למיון לפי חודש השינוי ב־UTC משתמשים ב־`--by date`.
ב־Windows אפשר להשתמש ב־`py -3.11` במקום `python`.

## מה חשוב לדעת

- המקור והיעד צריכים להיות תיקיות נפרדות באותה מערכת קבצים, עם תמיכה בקישורים קשיחים.
- קישורים סמליים אינם מועברים. נבדקים גם רכיבי נתיב שמופיעים לפני `..`.
- החזרה מסרבת לגעת בקובץ ששונה, נעלם, או שכבר יש קובץ במיקומו המקורי.
- יש להריץ מופע אחד בכל פעם ולעצור תוכנות שמשנות את הקבצים בזמן הפעולה.
- המניפסט מתעד מיקומים, לא מגבה תוכן. שומרים אותו בתיקייתו המקורית ולא משתפים אותו: יש בו נתיבים מקומיים.

## Developer notes

`organizer.py` contains path validation, planning, exclusive moves and journal-based undo.
`sample_data.py` creates a new isolated input directory and refuses existing ones.

Moves use exclusive hard-link creation followed by unlinking the source. SHA-256,
size and modification time are checked before moving and restoring. Each journal
is saved before moving and updated atomically after each result. The batch itself
is not atomic: interruption can leave completed moves or two names for one file.
Undo refuses a duplicate original path; inspect it manually. Empty directories remain.

Extension grouping is case-insensitive. Missing extensions use `no-extension`;
unusual extensions use `other-extension`. Names containing `:` or `\` are skipped.
Hidden regular files are included. Date grouping uses UTC modification time.

```sh
python -m unittest discover -s tests -v
```

25 temporary-directory tests cover dry runs, collisions, symlinks, root validation,
changed files, undo and interrupted journal recovery. Linux was tested; Windows
and macOS need verification on the intended filesystem.
Exit codes: `0` completed, including planned skips; `1` move/restore refusals;
`2` validation or I/O error.

אייל אליזאדה / Eyal Alizade · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
