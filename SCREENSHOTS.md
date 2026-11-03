# צילומי ההדגמה

התמונות צולמו מתוך התוכנות שפעלו מקומית ב־3 בנובמבר 2026. כל השמות, הפניות, המוצרים וההזמנות הם נתוני הדגמה. אלה צילומי מסך, ללא יצירת תמונות או ציור של מסכים.

| תמונה | מה פעל בזמן הצילום |
| --- | --- |
| [לקוח DeskBuddy](deskbuddy-telegram-bot/docs/screenshots/deskbuddy.jpg) | הלקוח השלים את שלבי הבקשה, אישר אותה וקיבל מספר למעקב. |
| [מנהל DeskBuddy](deskbuddy-telegram-bot/docs/screenshots/deskbuddy-admin.jpg) | אותה בקשה הופיעה בתור המנהל, ונפתחו פרטיה. |
| [מלאי StockPilot](stockpilot-api/docs/screenshots/inventory.jpg) | חמישה מוצרים והזמנת הדגמה פעילה, מתוך מסד הנתונים הזמני. |
| [הזמנות StockPilot](stockpilot-api/docs/screenshots/orders.jpg) | הזמנה נוספת הורידה שתי יחידות; ביטולה החזיר אותן והשאיר היסטוריה. |

DeskBuddy מפעיל את שירות הבוט ו־SQLite דרך ממשק מקומי ותחליף ל־Telegram. לא בוצעה בשלב הזה בדיקה מול בוט Telegram חי. StockPilot מפעיל את ה־API ואת כללי המלאי בפועל. בשתי ההדגמות מסדי הנתונים זמניים ונמחקים עם סגירת השרת.

הוראות ההפעלה מופיעות ב־README של כל פרויקט. הבדיקות המקומיות מסוכמות ב־[verification.json](verification.json); אין בכך אישור להרצה ב־Windows או לתוצאות GitHub Actions.
