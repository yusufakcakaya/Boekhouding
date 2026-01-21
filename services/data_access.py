import uuid
import json
import pandas as pd
import numpy as np
import os

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
    ensure_data_dir()

    try:
        with open(TRANSACTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = []

    df = pd.DataFrame(data)

    # -------------------------------
    # ZORUNLU KOLONLARI GARANTİ ET
    # -------------------------------
    required_cols = [
        "ID",
        "BookingDate",
        "Amount",
        "CounterpartyName",
        "Description",
        "CampusCode",
        "CategoryCode",
        "AccountName",
        "UploadTimestamp"
    ]

    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    return df

 
    
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
    df.replace({np.nan: None, "": None}, inplace=True)
    with open(TRANSACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(df.to_dict("records"), f, indent=4)


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
