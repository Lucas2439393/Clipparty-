import hashlib
import json
import secrets
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_DB_PATH = BASE_DIR / "auth_db.json"

_lock = threading.Lock()


def _load():
    if not AUTH_DB_PATH.exists():
        return {"users": {}, "sessions": {}}

    try:
        data = json.loads(AUTH_DB_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"users": {}, "sessions": {}}

        data.setdefault("users", {})
        data.setdefault("sessions", {})
        return data
    except Exception:
        return {"users": {}, "sessions": {}}


_db = _load()


def _save():
    AUTH_DB_PATH.write_text(
        json.dumps(_db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()


def create_user(name: str, email: str, password: str) -> dict:
    name = name.strip()
    email = email.strip().lower()

    if not name:
        raise ValueError("Naam is verplicht.")

    if not email or "@" not in email:
        raise ValueError("Vul een geldig e-mailadres in.")

    if len(password) < 8:
        raise ValueError("Wachtwoord moet minimaal 8 tekens bevatten.")

    with _lock:
        for user in _db["users"].values():
            if user["email"] == email:
                raise ValueError("Er bestaat al een account met dit e-mailadres.")

        user_id = secrets.token_hex(16)
        salt = secrets.token_hex(16)

        _db["users"][user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "password_hash": _hash_password(password, salt),
            "salt": salt,
        }

        _save()

        return {
            "id": user_id,
            "name": name,
            "email": email,
        }


def authenticate(email: str, password: str):
    email = email.strip().lower()

    with _lock:
        for user in _db["users"].values():
            if user["email"] != email:
                continue

            expected = _hash_password(password, user["salt"])

            if secrets.compare_digest(
                expected,
                user["password_hash"],
            ):
                return {
                    "id": user["id"],
                    "name": user["name"],
                    "email": user["email"],
                }

            return None

    return None


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)

    with _lock:
        _db["sessions"][token] = user_id
        _save()

    return token


def get_user_from_token(token: str):
    if not token:
        return None

    with _lock:
        user_id = _db["sessions"].get(token)

        if not user_id:
            return None

        user = _db["users"].get(user_id)

        if not user:
            return None

        return {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
        }


def delete_session(token: str):
    if not token:
        return

    with _lock:
        _db["sessions"].pop(token, None)
        _save()
