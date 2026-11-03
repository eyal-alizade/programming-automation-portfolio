# איחוד קובצי מכירות

מתוך תיקיית הפרויקט, עם Python 3.11 ומעלה:

```sh
python -m csv_report_builder samples/sales-a.csv samples/sales-b.csv --output demo-output --currency ILS
```

הדוגמה מפיקה חמש רשומות וסכום של **185.10 ₪**, אחרי הסרת שורה זהה אחת.
פותחים את `demo-output/summary.csv` או `demo-output/report.txt` כדי לקרוא את הסיכום.
התיקייה צריכה להיות חדשה. קובצי המקור אינם משתנים.

למבנה הקלט, אפשרויות הכפילויות ופרטי ההפעלה: [README](README.md).
