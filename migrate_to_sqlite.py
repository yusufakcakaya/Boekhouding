import json
from services.database import get_connection

# JSON yükle
with open("data/transactions.json", "r", encoding="utf-8") as f:
    transactions = json.load(f)

conn = get_connection()
cursor = conn.cursor()

for tx in transactions:
    cursor.execute("""
        INSERT OR IGNORE INTO transactions (
            id,
            booking_date,
            amount,
            counterparty,
            description,
            campus_code,
            category_code,
            account_name,
            upload_timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tx.get("ID"),
        tx.get("BookingDate"),
        tx.get("Amount"),
        tx.get("CounterpartyName"),
        tx.get("Description"),
        tx.get("CampusCode"),
        tx.get("CategoryCode"),
        tx.get("AccountName"),
        tx.get("UploadTimestamp"),
    ))

conn.commit()
conn.close()

print("Migration completed successfully.")