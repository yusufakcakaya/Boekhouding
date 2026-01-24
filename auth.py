import json
import os
import uuid
import time
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
RESET_TOKEN_FILE = os.path.join(DATA_DIR, "reset_tokens.json")


# ============================================================
#                         USER MODEL
# ============================================================
class User(UserMixin):

    ROLES = ["admin", "finance", "viewer"]

    def __init__(self, id, username, password_hash, role, email=None):
        self.id = str(id)
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.email = email

    # 🔴 KRİTİK: Flask-Login session için şart
    def get_id(self):
        return str(self.id)

    # ============================================================
    # RESET TOKEN HELPERS
    # ============================================================
    @staticmethod
    def _load_tokens():
        if not os.path.exists(RESET_TOKEN_FILE):
            return {}
        try:
            with open(RESET_TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    @staticmethod
    def _save_tokens(data):
        with open(RESET_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    @staticmethod
    def generate_reset_token(user_id):
        tokens = User._load_tokens()
        token = secrets.token_urlsafe(32)
        tokens[token] = {
            "user_id": user_id,
            "expires": time.time() + 3600
        }
        User._save_tokens(tokens)
        return token

    @staticmethod
    def verify_reset_token(token):
        tokens = User._load_tokens()
        info = tokens.get(token)

        if not info:
            return None

        if time.time() > info["expires"]:
            del tokens[token]
            User._save_tokens(tokens)
            return None

        return info["user_id"]

    # ============================================================
    # USER STORAGE (JSON)
    # ============================================================
    @staticmethod
    def _load_users_data():
        if not os.path.exists(USERS_FILE):
            return []
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    @staticmethod
    def _save_users_data(data):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    # ============================================================
    # LOADERS
    # ============================================================
    @staticmethod
    def get(user_id):
        users = User._load_users_data()
        for u in users:
            if str(u.get("id")) == str(user_id):
                return User(**u)
        return None

    @staticmethod
    def find_by_username(username):
        users = User._load_users_data()
        for u in users:
            if u.get("username", "").lower() == username.lower():
                return User(**u)
        return None

    @staticmethod
    def find_by_email(email):
        users = User._load_users_data()
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return User(**u)
        return None

    @staticmethod
    def get_all():
        users = User._load_users_data()
        result = []
        for u in users:
            try:
                result.append(User(**u))
            except:
                continue
        return result

    # ============================================================
    # USER MANAGEMENT
    # ============================================================
    @staticmethod
    def add_user(username, password, role, email):
        if not username or not password or not role:
            raise Exception("Alle velden zijn verplicht.")

        users = User._load_users_data()

        for u in users:
            if u["username"].lower() == username.lower():
                raise Exception("Gebruikersnaam bestaat al.")

        if email:
            for u in users:
                if u.get("email", "").lower() == email.lower():
                    raise Exception("Email bestaat al.")

        hashed_pw = generate_password_hash(password, method="pbkdf2:sha256")

        new_user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": hashed_pw,
            "role": role,
            "email": email
        }

        users.append(new_user)
        User._save_users_data(users)
        return User(**new_user)

    @staticmethod
    def update_password(user_id, new_password):
        users = User._load_users_data()
        for u in users:
            if str(u["id"]) == str(user_id):
                u["password_hash"] = generate_password_hash(new_password, method="pbkdf2:sha256")
        User._save_users_data(users)

    @staticmethod
    def update_role(user_id, new_role):
        users = User._load_users_data()
        for u in users:
            if str(u["id"]) == str(user_id):
                u["role"] = new_role
        User._save_users_data(users)

    @staticmethod
    def delete_user(user_id):
        users = User._load_users_data()
        users = [u for u in users if str(u["id"]) != str(user_id)]
        User._save_users_data(users)

    # ============================================================
    # PASSWORD CHECK
    # ============================================================
    @staticmethod
    def check_password(hashed, password):
        return check_password_hash(hashed, password)


# ============================================================
# INITIAL ADMIN CREATION
# ============================================================
def initialize_users():
    users = User._load_users_data()

    if not users:
        admin = {
            "id": "1",
            "username": "admin",
            "password_hash": generate_password_hash("admin123", method="pbkdf2:sha256"),
            "role": "admin",
            "email": None
        }
        User._save_users_data([admin])
