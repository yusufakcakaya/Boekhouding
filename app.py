# ============================================
# IMPORTS & GLOBAL CONFIG
# ============================================

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
import numpy as np

import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from io import BytesIO
import tempfile
import os
import json
import uuid
import hashlib
import pandas as pd
from datetime import datetime, timedelta
import re
from services.split_engine import SplitError
from services.data_access import ensure_data_dir

from flask import (
    Flask, request, render_template, redirect, url_for,
    flash, jsonify, session, send_file
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from functools import wraps
from werkzeug.security import check_password_hash

# Internal modules
from auth import User, initialize_users
from services.data_access import (
     save_transactions,load_transactions,
    delete_transaction_df, save_single_transaction_df
)

# ============================================
# GLOBAL VARIABLES
# ============================================

app = Flask(__name__)
app.secret_key = "super_secret_key"

DATA_DIR = "data"
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.json")

REAL_CAMPUSES = ["H", "D", "JR", "K"]
VALID_CODES = ["H", "D", "JR", "K"]



BANK_COLUMNS = {
    "Boekingsdatum": "BookingDate",
    "Bedrag": "Amount",
    "Naam tegenpartij bevat": "CounterpartyName",
    "Mededelingen": "Description"
}

ACCOUNT_MAPPING = {
    "ANTWERPEN": "Antwerpen - Bankrekening",
    "GENT": "Gent - Bankrekening"
}

MASTER_CODE = "Lucerna2024!"


# ============================================
# ENSURE DATA DIR
# ============================================

# ============================================
# DATA DIR + CATEGORY HANDLING
# ============================================



def parse_campus_string(raw: str):
    if not raw:
        raise SplitError("Campus veld is leeg.")

    # Normalize input
    text = raw.upper().replace(",", " ").strip()
    tokens = [t for t in text.split() if t.strip()]

    result = []
    total_percent = 0
    percent_items = []
    plain_items = []

    for t in tokens:

        # ---- %40H - 40H - H40 - H%40 ----
        m = re.match(r"%?(\d+)%?([A-Z]+)$", t)
        if m:
            percent = int(m.group(1))
            code = m.group(2)

            if code not in VALID_CODES:
                raise SplitError(f"Ongeldige campuscode: '{code}'")

            total_percent += percent
            percent_items.append({"code": code, "percent": percent})
            continue

        # ---- Yüzdesiz kampüs (H, D, JR, K) ----
        if t in VALID_CODES:
            plain_items.append({"code": t})
            continue

        raise SplitError(f"Ongeldig campus token: '{t}'")

    # ---- Eğer yüzde %100'ü geçerse hata ----
    if total_percent > 100:
        raise SplitError(f"Percentages som {total_percent}, moet ≤ 100 zijn.")

    # ---- Yüzde yok → hepsini eşit dağıt ----
    if total_percent == 0:
        count = len(plain_items)
        if count == 0:
            raise SplitError("Geen geldige campuscodes gevonden.")
        equal_percent = round(100 / count, 5)
        return [{"code": p["code"], "percent": equal_percent} for p in plain_items]

    # ---- Eğer bazı yüzdeler varsa → kalanını yüzdesizlere eşit dağıt ----
    remaining = 100 - total_percent

    if plain_items:
        per_plain = round(remaining / len(plain_items), 5)
        for p in plain_items:
            percent_items.append({"code": p["code"], "percent": per_plain})
    else:
        # Tüm yüzdeler verilmiş → tam 100 olmalı
        if total_percent != 100:
            raise SplitError(f"Percentages moeten optellen tot 100. Nu: {total_percent}")

    return percent_items


def get_latest_date(df):
    """DataFrame’deki en son tarihi döndürür."""
    if df.empty:
        return None
    try:
        ds = pd.to_datetime(df["BookingDate"], format="%d-%m-%Y", errors="coerce")
        latest = ds.max()
        return latest if not pd.isna(latest) else None
    except:
        return None
    
def load_categories(): 
    """Kategorileri JSON'dan yükler.""" 
    ensure_data_dir() 
    try: 
        with open(CATEGORIES_FILE, "r", encoding="utf-8") as f: return json.load(f) 
    except: 
        return {"CampusCodes": {}, "CategoryCodes": {}}

def fill_missing_months(series):
    """
    Eksik ayları sıfır ile tamamlar.
    (2025-09, 2025-10 var ama 2025-11 yoksa → 0 ekler)
    """
    if series.empty:
        return series

    idx = pd.period_range(series.index.min(), series.index.max(), freq="M").astype(str)
    return series.reindex(idx, fill_value=0)

def calculate_kpis(df):
    kpi = {
        "growth": 0,
        "expense_ratio": 0,
        "top_campus": "-",
        "biggest_cost": "-"
    }

    if df.empty:
        return kpi

    # --- Growth ---
    df["Month"] = pd.to_datetime(df["BookingDate"], dayfirst=True).dt.to_period("M").astype(str)
    monthly = df.groupby("Month")["Amount"].sum()
    monthly = fill_missing_months(monthly)

    if len(monthly) >= 2:
        last = monthly.iloc[-1]
        prev = monthly.iloc[-2]
        if prev != 0:
            kpi["growth"] = round(((last - prev) / abs(prev)) * 100, 2)

    # --- Expense Ratio ---
    inc = df[df.Amount > 0].Amount.sum()
    exp = abs(df[df.Amount < 0].Amount.sum())
    if inc > 0:
        kpi["expense_ratio"] = round((exp / inc) * 100, 2)

    # --- Most Active Campus ---
    by_campus = df.groupby("CampusCode")["Amount"].count()
    if not by_campus.empty:
        kpi["top_campus"] = by_campus.idxmax()

    # --- Biggest Cost Category ---
    costs = df[df.Amount < 0]
    if not costs.empty:
        cat_sum = abs(costs.groupby("CategoryCode")["Amount"].sum())
        if not cat_sum.empty:
            kpi["biggest_cost"] = cat_sum.idxmax()

    return kpi

def distribute_amount(amount: float, parsed):
    rows = []
    for entry in parsed:
        portion = amount * (entry["percent"] / 100)
        rows.append({
            "code": entry["code"],
            "amount": round(portion, 2)
        })
    return rows

def save_single_transaction_df(tx_id, parsed_campus, category):
    """
    campus_raw → kullanıcı girdisi (örn: 'H %10D')
    category   → kategori kodu
    """
    df = load_transactions()

    row = df[df["ID"] == tx_id].copy()
    if row.empty:
        return

    base_amount = float(row.iloc[0]["Amount"])

    # Eski satırı sil
    df = df[df["ID"] != tx_id]

   

    # 🔥 2) Tutarı yüzdelere göre dağıt
    distributed_rows = distribute_amount(base_amount, parsed_campus)

    # 🔥 3) Yeni satırlar oluştur
    new_rows = []
    for entry in distributed_rows:
        r = row.iloc[0].copy()
        r["ID"] = str(uuid.uuid4())
        r["CampusCode"] = entry["code"]
        r["Amount"] = entry["amount"]
        r["CategoryCode"] = category
        new_rows.append(r)

    df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    save_transactions(df)


# ============================================
# LOGIN MANAGER
# ============================================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# ============================================
# ROLE CHECK DECORATOR
# ============================================

def role_required(roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("U moet eerst inloggen.", "error")
                return redirect(url_for("login"))
            if current_user.role not in roles:
                flash("U heeft geen toegang.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return decorated
    return wrapper
# ============================================
# LOGIN
# ============================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password").strip()

        user = User.find_by_username(username)

        # ---- MASTER LOGIN ----
        if username == "admin" and password == MASTER_CODE:
            real_admin = User.find_by_username("admin")
            if real_admin:
                login_user(real_admin)
                flash("Master login succesvol!", "success")
                return redirect(url_for("dashboard"))

        # ---- Normal Login ----
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash("Succesvol ingelogd.", "success")
            return redirect(url_for("dashboard"))

        flash("Ongeldige inloggegevens.", "error")

    return render_template("login.html")


# ============================================
# LOGOUT
# ============================================

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ============================================
# ADMIN: USER MANAGEMENT
# ============================================

@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@role_required(["admin"])
def manage_users():
    users = User.get_all()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            username = request.form.get("username").strip()
            password = request.form.get("password").strip()
            role = request.form.get("role").strip()
            email = request.form.get("email").strip()

            try:
                User.add_user(username, password, role, email)
                flash("Nieuwe gebruiker toegevoegd!", "success")
            except Exception as e:
                flash(str(e), "error")

        elif action == "delete":
            user_id = request.form.get("user_id")
            User.delete_user(user_id)
            flash("Gebruiker verwijderd.", "success")

        elif action == "update_password":
            user_id = request.form.get("user_id")
            new_pw = request.form.get("new_password")
            User.update_password(user_id, new_pw)
            flash("Wachtwoord updated.", "success")

        elif action == "update_role":
            user_id = request.form.get("user_id")
            new_role = request.form.get("role")
            User.update_role(user_id, new_role)
            flash("Rol updated.", "success")

        return redirect(url_for("manage_users"))

    return render_template("manage_users.html", users=users)


# ============================================
# ADMIN: CATEGORY MANAGEMENT
# ============================================

@app.route("/admin/categories", methods=["GET", "POST"])
@login_required
@role_required(["admin"])
def manage_categories():
    categories = load_categories()

    if request.method == "POST":
        action = request.form.get("action")
        code_type = request.form.get("code_type")

        if code_type not in categories:
            flash("Ongeldige categorie selectie.", "error")
            return redirect(url_for("manage_categories"))

        if action == "add":
            new_code = request.form.get("new_code").strip().upper()
            desc = request.form.get("description").strip()
            categories[code_type][new_code] = desc

        elif action == "delete":
            code_del = request.form.get("code_to_delete")
            categories[code_type].pop(code_del, None)

        with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(categories, f, indent=4)

        return redirect(url_for("manage_categories"))

    return render_template("manage_categories.html", categories=categories)
# ============================================
# HELPER: FILTER TRANSACTIONS BY ACCOUNT
# ============================================

def filter_transactions_by_account(df, account_filter):
    if df.empty:
        return df

    if account_filter == "ALL":
        return df

    mapping = ACCOUNT_MAPPING.get(account_filter.upper())
    if not mapping:
        return pd.DataFrame()

    return df[df["AccountName"] == mapping]




# ============================================
# DASHBOARD
# ============================================
@app.route("/")
@login_required
def dashboard():
    df = load_transactions()

    account_filter = request.args.get("account_filter", "ALL")
    filtered = filter_transactions_by_account(df.copy(), account_filter)

    # EMPTY CASE
    if filtered.empty:
        return render_template(
            "index.html",
            latest_date="Nog geen gegevens",
            total_balance="0.00",
            total_transactions=0,
            account_filter=account_filter,
            accounts=list(ACCOUNT_MAPPING.keys()),
            campus_breakdown={},
            campus_order=REAL_CAMPUSES,
            campus_options={c: c for c in REAL_CAMPUSES},
            account_balances={},
            monthly_trend_chart=None,
            income_expense_chart=None,
            campus_pie_chart=None,
            kpi_growth=0,
            kpi_expense_ratio=0,
            kpi_top_campus="-",
            kpi_biggest_cost="-",
            city_compare_chart=None
        )

    # DATE
    latest = get_latest_date(filtered)
    latest_str = latest.strftime("%d-%m-%Y") if latest else "Geen datum"

    # BASIC TOTALS
    total_balance = f"{filtered['Amount'].sum():,.2f}"
    total_transactions = len(filtered)

    # ACCOUNT BALANCES
    account_balances = {}
    if account_filter == "ALL":
        for key, name in ACCOUNT_MAPPING.items():
            amt = df[df["AccountName"] == name]["Amount"].sum()
            account_balances[key] = f"{amt:,.2f}"

    # CAMPUS BREAKDOWN
    breakdown = {}
    for c in REAL_CAMPUSES:
        sub = filtered[filtered["CampusCode"] == c]
        inc = sub[sub.Amount > 0].Amount.sum()
        exp = abs(sub[sub.Amount < 0].Amount.sum())
        breakdown[c] = {
            "Income": inc,
            "Expense": exp,
            "NetBalance": inc - exp
        }

    # GRAPHS
    monthly_trend_chart = generate_monthly_trend_chart(filtered)
    income_expense_chart = generate_income_expense_chart(filtered)
    campus_pie_chart = generate_campus_pie_chart(filtered)

    # KPIs
    kpis = calculate_kpis(filtered)

    kpi_growth       = kpis.get("growth") or 0
    kpi_expense_ratio = kpis.get("expense_ratio") or 0
    kpi_top_campus    = kpis.get("top_campus") or "-"
    kpi_biggest_cost  = kpis.get("biggest_cost") or "-"

    return render_template(
        "index.html",
        latest_date=latest_str,
        total_balance=total_balance,
        total_transactions=total_transactions,
        account_filter=account_filter,
        accounts=list(ACCOUNT_MAPPING.keys()),
        campus_breakdown=breakdown,
        campus_order=REAL_CAMPUSES,
        campus_options={c: c for c in REAL_CAMPUSES},
        account_balances=account_balances,
        monthly_trend_chart=monthly_trend_chart,
        income_expense_chart=income_expense_chart,
        campus_pie_chart=campus_pie_chart,
        kpi_growth=kpi_growth,
        kpi_expense_ratio=kpi_expense_ratio,
        kpi_top_campus=kpi_top_campus,
        kpi_biggest_cost=kpi_biggest_cost,
        city_compare_chart=generate_city_compare_chart(filtered)
    )
# ============================================
# CLEAN CSV + PROCESS
# ============================================

ROWS_TO_SKIP_HEADER = 12
DELIMITER = ";"

def clean_and_process_csv(filepath, account_name):
    try:
        df = pd.read_csv(filepath, skiprows=ROWS_TO_SKIP_HEADER, delimiter=DELIMITER)

        df.rename(columns=BANK_COLUMNS, inplace=True)

        df["BookingDate"] = pd.to_datetime(
            df["BookingDate"], dayfirst=True, errors="coerce"
        ).dt.strftime("%d-%m-%Y")

        df["Amount"] = (
            df["Amount"].astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["ID"] = [str(uuid.uuid4()) for _ in range(len(df))]
        df["AccountName"] = account_name
        df["CampusCode"] = ""
        df["CategoryCode"] = ""
        df["UploadTimestamp"] = datetime.utcnow().isoformat()

        return None, df.to_dict("records")

    except Exception as e:
        return f"CSV fout: {e}", None


# ============================================
# UPLOAD PAGE
# ============================================
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_page():
    df_all = load_transactions()
    latest = get_latest_date(df_all)
    latest_str = latest.strftime("%d-%m-%Y") if latest else None

    if request.method == "POST":
        if "file" not in request.files:
            flash("Geen bestand geselecteerd.", "error")
            return redirect(url_for("upload_page"))

        file = request.files["file"]
        campus_loc = request.form.get("campus_location", "").upper()

        if not file.filename.endswith(".csv"):
            flash("Alleen CSV toegestaan.", "error")
            return redirect(url_for("upload_page"))

        account_name = f"{campus_loc.capitalize()} - Bankrekening"

        temp_file = os.path.join(DATA_DIR, "temp_upload.csv")
        file.save(temp_file)

        err, processed = clean_and_process_csv(temp_file, account_name)
        os.remove(temp_file)

        if err:
            flash(err, "error")
            return redirect(url_for("upload_page"))

        df_new = pd.DataFrame(processed)
        if df_new.empty:
            flash("Geen nieuwe transacties.", "warning")
            return redirect(url_for("upload_page"))

        # duplicate temizleme
        old = load_transactions()
        if not old.empty:
            old["uk"] = (
                old["BookingDate"].astype(str)
                + old["CounterpartyName"].astype(str)
                + old["Amount"].astype(str)
            )

            df_new["uk"] = (
                df_new["BookingDate"].astype(str)
                + df_new["CounterpartyName"].astype(str)
                + df_new["Amount"].astype(str)
            )

            df_new = df_new[~df_new["uk"].isin(old["uk"])]

        if df_new.empty:
            flash("Geen nieuwe transacties.", "warning")
            return redirect(url_for("upload_page"))

        df_new.drop(columns=["uk"], errors="ignore", inplace=True)

        # yeni transactionları direkt kaydet
        final_df = pd.concat([old, df_new], ignore_index=True)
        save_transactions(final_df)

        flash("Transacties succesvol geüpload!", "success")
        return redirect(url_for("dashboard"))

    return render_template("upload.html", latest_date=latest_str)


# -----------------------------------------------------
# DELETE SINGLE TRANSACTION (AJAX)
# -----------------------------------------------------
@app.route("/delete_transaction", methods=["POST"])
@login_required
def delete_transaction():
    tx_id = request.form.get("tx_id")

    if not tx_id:
        return jsonify({"success": False, "message": "Geen ID ontvangen."}), 400

    delete_transaction_df(tx_id)
    return jsonify({"success": True}), 200


# ============================================
# VIEW TRANSACTIONS
# ============================================

@app.route("/view_transactions", methods=["GET", "POST"])
@login_required
def view_transactions():

    df = load_transactions()
    categories_info = load_categories()

    account_filter = request.args.get("account_filter", "ALL")
    campus_filter  = request.args.get("campus_filter", "ALL")
    search         = request.args.get("search", "").strip()
    batch_filter   = request.args.get("batch_filter", "ALL")

    filtered = filter_transactions_by_account(df.copy(), account_filter)

    if batch_filter != "ALL":
        filtered = filtered[filtered["UploadTimestamp"] == batch_filter]

    if campus_filter != "ALL":
        filtered = filtered[filtered["CampusCode"] == campus_filter]

    if search:
        filtered = filtered[
            filtered.apply(
                lambda r:
                    search.lower() in str(r["CounterpartyName"]).lower() or
                    search.lower() in str(r["Description"]).lower(),
                axis=1
            )
        ]

    filtered.sort_values("BookingDate", ascending=False, inplace=True)
    transactions = filtered.to_dict("records")

    upload_batches = sorted(df["UploadTimestamp"].dropna().unique(), reverse=True)

    # ============================
    #         POST — SAVE
    # ============================

    if request.method == "POST":
        form = request.form

        ids = [k.replace("campus_code_", "") for k in form if k.startswith("campus_code_")]

        for tx_id in ids:

            # 1) RAW CAMPUS STRING
            raw_campus = form.get(f"campus_code_{tx_id}", "")
            raw_cat    = form.get(f"category_code_{tx_id}", "")

            # Bazı browser'lar list döndürebilir → güvenli fix
            if isinstance(raw_campus, list):
                raw_campus = " ".join(raw_campus)

            raw_campus = raw_campus.strip()

            if raw_campus == "":
                flash(f"Campuscode ontbreekt voor transactie {tx_id}", "error")
                return redirect(request.url)

            # 2) Parse → %40H %60D gibi formatları çöz
            try:
                parsed = parse_campus_string(raw_campus)
            except SplitError as e:
                flash(str(e), "error")
                return redirect(request.url)

            # 3) Kaydet
            save_single_transaction_df(tx_id, parsed, raw_cat)

        flash("Wijzigingen opgeslagen.", "success")
        return redirect(request.url)

    # ============================
    #       PAGE RENDER
    # ============================

    return render_template(
        "view_transactions.html",
        transactions=transactions,
        total_count=len(transactions),
        account_filter=account_filter,
        campus_filter=campus_filter,
        search_term=search,
        batch_filter=batch_filter,
        campus_options={c: c for c in ["H", "D", "JR", "K"]},
        category_options=categories_info["CategoryCodes"],
        upload_batches=upload_batches,
    )


# ============================================
# GRAPH HELPERS
# ============================================

def df_to_base64(fig):
    """Matplotlib fig → base64 PNG"""
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    encoded = base64.b64encode(buf.getvalue()).decode()
    plt.close(fig)
    return f"data:image/png;base64,{encoded}"


def generate_monthly_trend_chart(df):
    try:
        df["Month"] = pd.to_datetime(df["BookingDate"], dayfirst=True).dt.to_period("M").astype(str)
        monthly_sum = df.groupby("Month")["Amount"].sum()

        monthly_sum = fill_missing_months(monthly_sum)

        fig = Figure(figsize=(8, 3))
        ax = fig.subplots()
        monthly_sum.plot(ax=ax, marker="o")

        ax.set_title("Maandelijkse Netto Balans Trend")
        ax.set_ylabel("Netto (EUR)")
        ax.grid(alpha=0.3)

        buf = BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode()
    except:
        return None



def generate_income_expense_chart(df):
    if df is None or df.empty:
        return None

    df = df.copy()
    df["BookingDate"] = pd.to_datetime(df["BookingDate"], dayfirst=True, errors="coerce")
    df["Month"] = df["BookingDate"].dt.to_period("M").astype(str)

    monthly_inc = df[df["Amount"] > 0].groupby("Month")["Amount"].sum()
    monthly_exp = abs(df[df["Amount"] < 0].groupby("Month")["Amount"].sum())

    if monthly_inc.empty and monthly_exp.empty:
        return None

    fig, ax = plt.subplots(figsize=(10, 4))

    import numpy as np
    months = sorted(list(set(monthly_inc.index) | set(monthly_exp.index)))
    x = np.arange(len(months))
    width = 0.35

    inc_vals = [monthly_inc.get(m, 0) for m in months]
    exp_vals = [monthly_exp.get(m, 0) for m in months]

    ax.bar(x - width/2, inc_vals, width, label="Inkomsten", color="#4CAF50")
    ax.bar(x + width/2, exp_vals, width, label="Uitgaven", color="#D32F2F")

    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.set_title("Inkomsten vs Uitgaven")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    buf = BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)

    return "data:image/png;base64," + base64.b64encode(buf.read()).decode()

def generate_city_compare_chart(df):
    try:
        df["Month"] = pd.to_datetime(df["BookingDate"], dayfirst=True).dt.to_period("M").astype(str)

        # ANTWERPEN: H + D
        ant_df = df[df["CampusCode"].isin(["H", "D"])]
        ant_series = ant_df.groupby("Month")["Amount"].sum()
        ant_series = fill_missing_months(ant_series)

        # GENT: JR + K
        gent_df = df[df["CampusCode"].isin(["JR", "K"])]
        gent_series = gent_df.groupby("Month")["Amount"].sum()
        gent_series = fill_missing_months(gent_series)

        fig = Figure(figsize=(10, 4))
        ax = fig.subplots()

        ax.plot(ant_series.index, ant_series.values, marker="o", label="Antwerpen")
        ax.plot(gent_series.index, gent_series.values, marker="o", label="Gent")

        ax.set_title("Antwerpen vs Gent — Netto Vergelijking")
        ax.legend()
        ax.grid(alpha=0.3)

        buf = BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode()

    except Exception as e:
        print("City compare chart error:", e)
        return None
def generate_city_compare_chart(df):
    try:
        df["Month"] = pd.to_datetime(df["BookingDate"], dayfirst=True).dt.to_period("M").astype(str)

        # ANTWERPEN: H + D
        ant_df = df[df["CampusCode"].isin(["H", "D"])]
        ant_series = ant_df.groupby("Month")["Amount"].sum()
        ant_series = fill_missing_months(ant_series)

        # GENT: JR + K
        gent_df = df[df["CampusCode"].isin(["JR", "K"])]
        gent_series = gent_df.groupby("Month")["Amount"].sum()
        gent_series = fill_missing_months(gent_series)

        fig = Figure(figsize=(10, 4))
        ax = fig.subplots()

        ax.plot(ant_series.index, ant_series.values, marker="o", label="Antwerpen")
        ax.plot(gent_series.index, gent_series.values, marker="o", label="Gent")

        ax.set_title("Antwerpen vs Gent — Netto Vergelijking")
        ax.legend()
        ax.grid(alpha=0.3)

        buf = BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode()

    except Exception as e:
        print("City compare chart error:", e)
        return None


def generate_campus_pie_chart(df):
    """Kampüs pie chart (sadece giderler)"""

    if df.empty:
        return None

    df = df[df.Amount < 0].copy()  # Only expenses
    if df.empty:
        return None

    campus_exp = abs(df.groupby("CampusCode")["Amount"].sum())

    campus_exp = campus_exp[campus_exp > 0]
    if campus_exp.empty:
        return None

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.pie(campus_exp, labels=campus_exp.index, autopct="%1.1f%%")
    ax.set_title("Uitgaven per Campus")

    return df_to_base64(fig)

# -----------------------------------------------------
# REPORT OPTIONS PAGE (filters page)
# -----------------------------------------------------
@app.route("/reports", methods=["GET"])
@login_required
def report_options():

    categories = load_categories()
    df = load_transactions()

    campuses = ["H", "D", "JR", "K"]    # artık sadece gerçek kampüsler
    cat_list = sorted(categories["CategoryCodes"].keys())
    accounts = sorted(df["AccountName"].dropna().unique())

    return render_template(
        "report_options.html",
        campuses=campuses,
        categories=cat_list,
        accounts=accounts
    )

# -----------------------------------------------------
# ADVANCED REPORTING ENGINE (FINAL + FIXED JSON)
# -----------------------------------------------------
def sanitize_for_json(obj):
    """
    Pandas Period, Timestamp, numpy int/float, NaN, None gibi JSON uyumsuz tipleri
    otomatik olarak string veya normal Python tipine çevirir.
    """
    import numpy as np
    import pandas as pd

    if obj is None:
        return None

    # --- Basit tipler direkt döner
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # --- Pandas Timestamp
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()

    # --- Pandas Period (sende hatayı çıkaran bu!)
    if isinstance(obj, pd.Period):
        return str(obj)

    # --- numpy sayılar
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.float64, np.float32)):
        return float(obj)

    # --- list içindekileri temizle
    if isinstance(obj, list):
        return [sanitize_for_json(x) for x in obj]

    # --- dict içindekileri temizle
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}

    # --- DataFrame
    if isinstance(obj, pd.DataFrame):
        return sanitize_for_json(obj.to_dict("records"))

    # --- başka bir tip → stringle
    return str(obj)


# -----------------------------------------------------
# ADVANCED REPORTING ENGINE (FINAL FIXED VERSION)
# -----------------------------------------------------
@app.route("/generate_report", methods=["POST"])
@login_required
def generate_report():

    df = load_transactions().copy()

    # ====================================================
    # HELPER — CLEAN JSON-SERIALIZATION
    # ====================================================
    def sanitize(obj):
        """Converts Timestamps, Periods, numpy types etc. into JSON-safe formats."""
        import numpy as np
        from pandas import Timestamp, Period

        if isinstance(obj, dict):
            return {sanitize(k): sanitize(v) for k, v in obj.items()}

        if isinstance(obj, list):
            return [sanitize(x) for x in obj]

        if isinstance(obj, Timestamp):
            return obj.strftime("%Y-%m-%d")

        if isinstance(obj, Period):
            return str(obj)

        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)

        if isinstance(obj, (np.floating, np.float64)):
            return float(obj)

        return obj

    # ====================================================
    # 1) DATE FILTER
    # ====================================================
    start_date = request.form.get("start_date")
    end_date   = request.form.get("end_date")

    df["DateObj"] = pd.to_datetime(df["BookingDate"], dayfirst=True, errors="coerce")
    d1, d2 = pd.to_datetime(start_date), pd.to_datetime(end_date)

    filt = df[(df["DateObj"] >= d1) & (df["DateObj"] <= d2)].copy()

    ###########################
    # FILTERS
    ###########################
    campus_filters   = request.form.getlist("campus_filter")
    category_filters = request.form.getlist("category_filter")
    account_filters  = request.form.getlist("account_filter")

    real = ["H", "D", "JR", "K"]

    # ---- CAMPUS ----
    if "ALL" in campus_filters:
        campus_filters = real
    else:
        campus_filters = [c for c in campus_filters if c in real]

    if campus_filters:
        filt = filt[filt["CampusCode"].isin(campus_filters)]

    # ---- CATEGORY ----
    if "ALL" in category_filters:
        category_filters = sorted(df["CategoryCode"].dropna().unique())

    if category_filters:
        filt = filt[filt["CategoryCode"].fillna("").isin(category_filters)]

    # ---- ACCOUNT ----
    if "ALL" in account_filters:
        account_filters = sorted(df["AccountName"].dropna().unique())

    if account_filters:
        filt = filt[filt["AccountName"].isin(account_filters)]

    # ====================================================
    # EMPTY RESULT HANDLING
    # ====================================================
    if filt.empty:
        empty = {
            "period": {"start": start_date, "end": end_date},
            "summary": {"income": 0, "expense": 0, "net": 0},
            "campus": {},
            "categories": {},
            "category_campus": {},
            "transactions": [],
            "graphs": {"trend": None, "income_expense": None, "pie": None}
        }

        return render_template(
            "report_view.html",
            start_date=start_date,
            end_date=end_date,
            report_data=empty,
            report_json=json.dumps(empty),
            bar_chart_data=None,
            income_expense_chart=None,
            pie_chart_data=None,
            category_descriptions={}
        )

    # ====================================================
    # SUMMARY
    # ====================================================
    income  = filt[filt.Amount > 0].Amount.sum()
    expense = filt[filt.Amount < 0].Amount.sum()
    net     = income + expense

    # ====================================================
    # CAMPUS BREAKDOWN
    # ====================================================
    campus_breakdown = {}
    for c in real:
        sub = filt[filt["CampusCode"] == c]
        inc = sub[sub.Amount > 0].Amount.sum()
        exp = abs(sub[sub.Amount < 0].Amount.sum())
        campus_breakdown[c] = {
            "Income": inc,
            "Expense": exp,
            "NetBalance": inc - exp
        }

    # ====================================================
    # CATEGORY BREAKDOWN
    # ====================================================
    category_breakdown = {}
    cats = sorted(filt["CategoryCode"].dropna().unique())

    for cat in cats:
        sub = filt[filt["CategoryCode"] == cat]
        inc = sub[sub.Amount > 0].Amount.sum()
        exp = abs(sub[sub.Amount < 0].Amount.sum())
        category_breakdown[cat] = {
            "Income": inc,
            "Expense": exp,
            "NetBalance": inc - exp
        }

    # ====================================================
    # CATEGORY × CAMPUS BREAKDOWN
    # ====================================================
    category_campus = {}

    for cat in cats:
        category_campus[cat] = {}
        for c in real:
            sub = filt[(filt["CategoryCode"] == cat) & (filt["CampusCode"] == c)]
            inc = sub[sub.Amount > 0].Amount.sum()
            exp = abs(sub[sub.Amount < 0].Amount.sum())
            category_campus[cat][c] = {
                "Income": inc,
                "Expense": exp,
                "Net": inc - exp
            }

    # ====================================================
    # CATEGORY DEFINITIONS
    # ====================================================
    category_descriptions = {
        "H":  "Huur",
        "K":  "Kostgeld",
        "A":  "Activiteit",
        "O":  "Onderhoud",
        "F":  "Fuel",
        "JR": "Jean Ray Kostgeld",
        "D":  "Deurne Kostgeld",
        "EW": "Energie Water",
        "EE": "Energie Elektriciteit",
        "EG": "Energie Gas",
        "M":  "Market"
    }

    # ====================================================
    # SAFE MONTHLY GRAPH INDEX
    # ====================================================
    filt["Month"] = filt["DateObj"].dt.to_period("M").astype(str)

    # ---- TREND ----
    monthly_net = filt.groupby("Month")["Amount"].sum()

    fig1 = Figure(figsize=(10,4))
    ax1 = fig1.subplots()
    monthly_net.plot(ax=ax1, marker="o")
    ax1.grid(alpha=0.3)
    ax1.set_title("Maandelijkse Netto Trend")

    b1 = BytesIO()
    fig1.savefig(b1, format="png")
    trend_b64 = "data:image/png;base64," + base64.b64encode(b1.getvalue()).decode()

    # ---- INCOME vs EXPENSE ----
    inc   = filt[filt.Amount > 0].groupby("Month")["Amount"].sum()
    exp   = abs(filt[filt.Amount < 0].groupby("Month")["Amount"].sum())

    all_months = sorted(set(inc.index) | set(exp.index))
    inc = inc.reindex(all_months, fill_value=0)
    exp = exp.reindex(all_months, fill_value=0)

    fig2 = Figure(figsize=(10,4))
    ax2 = fig2.subplots()
    x = range(len(all_months))
    w = 0.4

    ax2.bar([i-w/2 for i in x], inc.values, width=w, color="green", label="Inkomsten")
    ax2.bar([i+w/2 for i in x], exp.values, width=w, color="red", label="Uitgaven")
    ax2.set_xticks(x)
    ax2.set_xticklabels(all_months, rotation=45)
    ax2.legend()
    ax2.grid(alpha=0.3)
    ax2.set_title("Inkomsten vs Uitgaven")

    b2 = BytesIO()
    fig2.savefig(b2, format="png")
    income_expense_b64 = "data:image/png;base64," + base64.b64encode(b2.getvalue()).decode()

    # ---- PIE ----
    campus_exp = abs(filt[filt.Amount < 0].groupby("CampusCode")["Amount"].sum())

    fig3 = Figure(figsize=(6,5))
    ax3 = fig3.subplots()
    if campus_exp.sum() > 0:
        campus_exp.plot(kind="pie", autopct="%1.1f%%", ax=ax3)
    ax3.set_title("Uitgaven per Campus")

    b3 = BytesIO()
    fig3.savefig(b3, format="png")
    pie_b64 = "data:image/png;base64," + base64.b64encode(b3.getvalue()).decode()

    # ====================================================
    # TRANSACTION LIST
    # ====================================================
    tx_list = sanitize(filt.to_dict("records"))

    # ====================================================
    # FINAL REPORT STRUCTURE
    # ====================================================
    report_data = sanitize({
        "period": {"start": start_date, "end": end_date},
        "summary": {"income": income, "expense": expense, "net": net},
        "campus": campus_breakdown,
        "categories": category_breakdown,
        "category_campus": category_campus,
        "transactions": tx_list,
        "graphs": {
            "trend": trend_b64,
            "income_expense": income_expense_b64,
            "pie": pie_b64
        }
    })

    # ====================================================
    # RENDER HTML
    # ====================================================
    return render_template(
        "report_view.html",
        start_date=start_date,
        end_date=end_date,
        report_data=report_data,
        report_json=json.dumps(report_data),
        category_descriptions=category_descriptions
    )

@app.route('/download_pdf', methods=['POST'])
@login_required
def download_pdf():
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    # JSON'i al
    report_json = request.form.get("report_json")
    data = json.loads(report_json)

    # Geçici PDF oluştur
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = tmp.name
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # ---------- Base64 → Image Helper ----------
    def img_from_base64(img64, max_width=450):
        if not img64:
            return None
        try:
            raw = img64.split(",")[1]
            img_data = base64.b64decode(raw)
            img = Image(BytesIO(img_data))
            img._restrictSize(max_width, max_width * 2)
            return img
        except:
            return None

    # ---------- Logo ----------
    try:
        logo = Image("static/img/logo.png", width=40, height=40)
        story.append(logo)
    except:
        pass

    story.append(Paragraph("<b>Financieel Rapport</b>", styles["Title"]))
    story.append(Spacer(1, 10))

    # ---------- Period ----------
    p = data["period"]
    story.append(Paragraph(f"Periode: <b>{p['start']}</b> t/m <b>{p['end']}</b>", styles["Heading3"]))
    story.append(Spacer(1, 20))

    # ==========================
    # SUMMARY TABLE
    # ==========================
    summary = data["summary"]
    summary_table = Table([
        ["Omschrijving", "Bedrag (EUR)"],
        ["Inkomsten", f"{summary['income']:.2f}"],
        ["Uitgaven", f"{summary['expense']:.2f}"],
        ["Netto", f"{summary['net']:.2f}"]
    ], colWidths=[7*cm, 5*cm])

    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E3E3E3")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (0,0), (-1,-1), "CENTER")
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # ==========================
    # CAMPUS TABLE
    # ==========================
    story.append(Paragraph("<b>Campus Overzicht</b>", styles["Heading2"]))

    campus_rows = [["Campus", "Inkomsten", "Uitgaven", "Netto"]]
    for c, row in data["campus"].items():
        campus_rows.append([
            c,
            f"{row['Income']:.2f}",
            f"{row['Expense']:.2f}",
            f"{row['NetBalance']:.2f}"
        ])

    campus_table = Table(campus_rows)
    campus_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#D1ECFF")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (0,0), (-1,-1), "CENTER")
    ]))
    story.append(campus_table)
    story.append(Spacer(1, 20))

    # ==========================
    # CATEGORY TABLE
    # ==========================
    story.append(Paragraph("<b>Categorie Overzicht</b>", styles["Heading2"]))

    cat_rows = [["Categorie", "Inkomsten", "Uitgaven", "Netto"]]
    for cat, row in data["categories"].items():
        net = row.get("NetBalance", row.get("Net", 0))
        cat_rows.append([
            cat,
            f"{row['Income']:.2f}",
            f"{row['Expense']:.2f}",
            f"{net:.2f}"
        ])

    cat_table = Table(cat_rows)
    cat_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#FFF3CD")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (0,0), (-1,-1), "CENTER")
    ]))
    story.append(cat_table)
    story.append(Spacer(1, 20))

    # ==========================
    # GRAPHS
    # ==========================
    story.append(Paragraph("<b>Grafieken</b>", styles["Heading2"]))

    for label, img64 in data["graphs"].items():
        img = img_from_base64(img64)
        if img:
            story.append(Paragraph(label.replace("_", " ").title(), styles["Heading3"]))
            story.append(img)
            story.append(Spacer(1, 15))

    # ==========================
    # CATEGORY × CAMPUS MATRIX (FINAL FIXED)
    # ==========================
    story.append(Paragraph("<b>Categorie × Campus Overzicht</b>", styles["Heading2"]))

    # Tüm kategoriler
    all_categories = sorted(data["category_campus"].keys())

    # Tablo başlığı
    matrix = [["Categorie", "H", "D", "JR", "K"]]

    # Her kategori için satır oluştur
    for cat in all_categories:
        row = [cat]
        camp_data = data["category_campus"].get(cat, {})

        for camp in ["H", "D", "JR", "K"]:
            val = camp_data.get(camp, {}).get("Net", 0)
            row.append(f"{val:.2f}")

        matrix.append(row)

    matrix_table = Table(matrix, colWidths=[4*cm, 2*cm, 2*cm, 2*cm, 2*cm])
    matrix_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#CCE5FF")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey)
    ]))
    story.append(matrix_table)
    story.append(Spacer(1, 20))

    # ==========================
    # TRANSACTIONS (LAST PAGE)
    # ==========================
    story.append(Paragraph("<b>Transacties</b>", styles["Heading2"]))

    for tx in data["transactions"]:
        story.append(Paragraph(
            f"{tx['BookingDate']} — {tx['CounterpartyName']} — "
            f"{tx['Description']} — {tx['Amount']} EUR",
            styles["Normal"]
        ))
        story.append(Spacer(1, 4))

    doc.build(story)
    return send_file(pdf_path, as_attachment=True, download_name="rapport.pdf")



#------MAIN-----#

if __name__ == "__main__":
    ensure_data_dir()
    app.run(debug=True, port=5001)
