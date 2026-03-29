import uuid
import json
import pandas as pd
import numpy as np
import os
from services.database import get_connection

DATA_DIR = "data"
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.json")

def ensure_data_dir():
    """Veri klasörünü ve gerekli JSON dosyalarını oluşturur."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    if not os.path.exists(TRANSACTIONS_FILE):
        with open(TRANSACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)

    if not os.path.exists(CATEGORIES_FILE):
        default = {
            "CampusCodes": {
                "H": "Hoboken",
                "D": "Deurne",
                "JR": "Jean Ray",
                "K": "Karteuzerlaan"
            },
            "CategoryCodes": {}
        }
        with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4)

def load_transactions():
    conn = get_connection()
    
    query = """
        SELECT 
            id AS ID,
            booking_date AS BookingDate,
            amount AS Amount,
            counterparty AS CounterpartyName,
            description AS Description,
            campus_code AS CampusCode,
            category_code AS CategoryCode,
            account_name AS AccountName,
            upload_timestamp AS UploadTimestamp
        FROM transactions
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    return df

    # -------------------------------
    # ZORUNLU KOLONLARI GARANTİ ET
    # -------------------------------


 
    
def save_single_transaction_df(tx_id, campus_code, category_code):
    """Saves campus + category changes for a single transaction."""
    df = load_transactions()

    if tx_id not in df["ID"].values:
        raise ValueError(f"Transactie met ID '{tx_id}' niet gevonden.")

    # Campus doğrulaması
    from services.split_engine import VALID_CODES, SplitError
    
    if campus_code and campus_code.upper() not in VALID_CODES:
        raise SplitError(
            f"Campuscode '{campus_code}' is ongeldig. Geldige codes zijn: {', '.join(VALID_CODES)}."
        )

    # Güncelle
    df.loc[df["ID"] == tx_id, "CampusCode"] = campus_code.upper()
    df.loc[df["ID"] == tx_id, "CategoryCode"] = category_code

    save_transactions(df)
    return True





def save_transactions(df):
    df = df.replace({np.nan: None, "": None})

    conn = get_connection()
    cursor = conn.cursor()

    # Önce tabloyu temizliyoruz (JSON gibi full replace mantığı)
    cursor.execute("DELETE FROM transactions")

    for _, row in df.iterrows():
        cursor.execute("""
            INSERT INTO transactions (
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
            row.get("ID"),
            row.get("BookingDate"),
            row.get("Amount"),
            row.get("CounterpartyName"),
            row.get("Description"),
            row.get("CampusCode"),
            row.get("CategoryCode"),
            row.get("AccountName"),
            row.get("UploadTimestamp"),
        ))

    conn.commit()
    conn.close()


def get_transaction_by_id_df(tx_id):
    df = load_transactions()
    row = df[df["ID"] == tx_id]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def delete_transaction_df(tx_id):
    df = load_transactions()
    df = df[df["ID"] != tx_id]
    save_transactions(df)
    return True


def add_transaction_df(new_tx_data):
    df = load_transactions()
    df = pd.concat([df, pd.DataFrame([new_tx_data])], ignore_index=True)
    save_transactions(df)
    return True
