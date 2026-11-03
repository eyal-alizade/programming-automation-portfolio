# StockPilot — התחלה מהירה

מסך בעברית לניהול מלאי והזמנות. דרוש Python 3.11 ומעלה.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python demo_server.py --port 8811
```

פותחים את [מסך ההדגמה](http://127.0.0.1:8811). ב־Windows משתמשים ב־`.venv\Scripts\python.exe` במקום `.venv/bin/python`.

אפשר לחפש מוצר, לעדכן מלאי, לפתוח הזמנה ולבטל אותה. הכמויות מתעדכנות בפועל במערכת, ויומן התנועות מציג את השינויים. ניסיון להזמין כמות שאינה זמינה נדחה ללא שינוי חלקי.

המסך מציג נתוני הדגמה בלבד. כל הפעלה יוצרת מסד נתונים זמני ומפתח זמני; אין צורך במפתח אישי ואין שימוש במסד נתונים קיים. הנתונים נמחקים עם סגירת השרת באמצעות `Ctrl+C`.

להרצת הבדיקות:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

הוראות API ומבנה הקוד נמצאים ב־[README](README.md).

אייל אליזאדה · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
