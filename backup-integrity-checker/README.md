# גיבוי תיקייה ובדיקת הקבצים

יוצר קובץ ZIP מתיקייה ובודק שכל קובץ בגיבוי תואם לתוכן שנשמר.
אפשר להשוות גם לתיקיית המקור כדי לדעת אם הקבצים השתנו מאז הגיבוי.

## דוגמה והפעלה

צריך Python 3.11 ומעלה. אין ספריות להתקין. מתוך תיקיית הפרויקט:

```sh
python backup_checker.py demo --output-dir demo-output
```

הפקודה יוצרת שלושה קבצים לדוגמה, מגבה אותם ובודקת את הגיבוי מול המקור.
הסיכום שיודפס כולל `files: 3`, `source_matches: true` ו־`status: ok`.
הדוגמה משתמשת בנתונים שנוצרו לתיק העבודות.
נשמר גם [סיכום מהרצה בפועל](examples/demo-result.json).

לבדיקה חוזרת:

```sh
python backup_checker.py verify demo-output/sample-backup.zip --source demo-output/source
```

לגיבוי תיקייה משלך:

```sh
python backup_checker.py backup documents documents-backup.zip
python backup_checker.py verify documents-backup.zip --source documents
```

שם קובץ הגיבוי חייב להיות חדש ומחוץ לתיקיית המקור. התיקייה שמכילה אותו צריכה
להיות קיימת. לבדיקה בלי השוואת המקור משמיטים את `--source`.
ב־Windows אפשר להשתמש ב־`py -3.11` במקום `python`.

## מה נבדק

- רשימת הקבצים, גודלם והתוכן שלהם לפי SHA-256.
- קובץ חסר, נוסף או שונה גורם לבדיקה להיכשל.
- הבדיקה קוראת את הארכיון בלי לחלץ או להריץ קבצים.
- קישורים סמליים במקור מדולגים ומופיעים בסיכום.

יש לעצור כתיבה לקבצי המקור בזמן הגיבוי. הגיבוי אינו מוצפן, והרשימה שבו אינה
חתומה: שינוי מתואם של התוכן והרשימה יכול לעבור בדיקה. הכלי שומר קבצים רגילים;
הוא לא שומר הרשאות, זמני קבצים או תיקיות ריקות, ואין בו פקודת שחזור.

## Developer notes

`backup_checker.py` separates tree scanning, stable file reads, ZIP capture,
manifest parsing and verification. Source identity is compared before and after
reading, then the tree is scanned again. Files stream in 1 MiB chunks.

The archive stores `manifest.json` and `files/<relative-path>`. Verification checks
exact member membership, CRC, size and SHA-256. It rejects traversal paths,
duplicate names, symlink/special entries, encrypted members and unsupported
compression. Nothing is extracted. The unsigned manifest detects damage relative
to its recorded contents; it does not authenticate the archive.

Default verification limits: 100,000 files, 10 GiB of file contents, 16 MiB manifest.
Override the first two with `--max-files` and `--max-bytes`. ZIP central-directory
parsing precedes those application limits. Creation refuses over 100,000 files.

Capture uses exclusive output creation and removes a partial output on a handled
failure. A forced kill can leave an incomplete ZIP. Source capture is not an
atomic snapshot; pause writers. Directory-FD/no-follow opens are used where
supported, with best-effort checks elsewhere. Hostile concurrent changes are
outside the supported model.

```sh
python -m unittest discover -s tests -v
```

Temporary-directory tests cover integrity changes, source drift, overwrite
protection, malformed archives, symlinks, limits and 12 MiB streaming.
Exit codes: `0` success, `1` verification failure, `2` usage or I/O error.
Commands emit one JSON result; errors go to stderr.

אייל אליזאדה / Eyal Alizade · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
