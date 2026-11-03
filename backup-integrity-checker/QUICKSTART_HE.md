# בדיקת תקינות גיבוי — התחלה מהירה

פרויקט תיק עבודות אישי של אייל אליזאדה. הדוגמאות סינתטיות בלבד; זה אינו פרויקט לקוח או שירות גיבוי מוסמך.

דרוש Python 3.11 ומעלה, ללא ספריות חיצוניות. מתוך תיקיית הפרויקט:

```sh
python backup_checker.py demo --output-dir demo-output
python backup_checker.py verify demo-output/sample-backup.zip --source demo-output/source
python -m unittest discover -s tests -v
```

לגיבוי תיקייה משלך:

```sh
python backup_checker.py backup documents documents-backup.zip
python backup_checker.py verify documents-backup.zip --source documents
```

קובץ הגיבוי חייב להיות מחוץ לתיקיית המקור ואסור שיהיה קיים מראש. קישורים סמליים אינם מועתקים. יש לעצור כתיבה לקבצי המקור בזמן הגיבוי. הבדיקה אינה מחלצת או מריצה קבצים.

תוצאה עם `status: ok` פירושה שהקבצים מתאימים לרשימת הבדיקה שבארכיון. הוספת `--source` בודקת גם שהמקור הנוכחי מתאים. קוד יציאה 0 מציין הצלחה, 1 כישלון בדיקה, ו־2 שגיאת שימוש או קלט/פלט.

הגיבוי אינו מוצפן והרשימה אינה חתומה. שינוי מתואם של הקבצים ושל הרשימה יכול לעבור בדיקה; זו אינה הוכחת מקוריות. אין פקודת שחזור ואין הבטחת התאוששות מאסון. פירוט המגבלות נמצא ב־README באנגלית.

יצירת קשר: [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
