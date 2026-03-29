import sqlite3
import os
import datetime
import subprocess

DB_PATH = os.environ.get("DB_PATH", "database.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

BACKUP_DIR = "/var/data/backups"

def create_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{BACKUP_DIR}/backup_{timestamp}.sql"

    command = f"sqlite3 {DB_PATH} .dump > {backup_file}"
    subprocess.call(command, shell=True)

    return backup_file

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            booking_date TEXT,
            amount REAL,
            counterparty TEXT,
            description TEXT,
            campus_code TEXT,
            category_code TEXT,
            account_name TEXT,
            upload_timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()


