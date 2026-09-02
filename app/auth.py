import os
import hashlib
import secrets
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL ontbreekt.")


@contextmanager
def get_connection():
    with psycopg.connect(
        DATABASE_URL,
        sslmode="require",
        row_factory=dict_row,
    ) as conn:
        yield conn


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

    user_id = secrets.token_hex(16)
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO public.clipparty_users
                    (id, name, email, password_hash, salt)
                VALUES
                    (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    name,
                    email,
                    password_hash,
                    salt,
                ),
            )
            conn.commit()

    except psycopg.errors.UniqueViolation:
        raise ValueError(
            "Er bestaat al een account met dit e-mailadres."
        )

    return {
        "id": user_id,
        "name": name,
        "email": email,
    }


def authenticate(email: str, password: str):
    email = email.strip().lower()

    with get_connection() as conn:
        user = conn.execute(
            """
            SELECT id, name, email, password_hash, salt
            FROM public.clipparty_users
            WHERE email = %s
            LIMIT 1
            """,
            (email,),
        ).fetchone()

    if not user:
        return None

    expected = _hash_password(password, user["salt"])

    if not secrets.compare_digest(
        expected,
        user["password_hash"],
    ):
        return None

    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
    }


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO public.clipparty_sessions
                (token, user_id)
            VALUES
                (%s, %s)
            """,
            (token, user_id),
        )
        conn.commit()

    return token


def get_user_from_token(token: str):
    if not token:
        return None

    with get_connection() as conn:
        user = conn.execute(
            """
            SELECT
                u.id,
                u.name,
                u.email
            FROM public.clipparty_sessions s
            JOIN public.clipparty_users u
                ON u.id = s.user_id
            WHERE s.token = %s
            LIMIT 1
            """,
            (token,),
        ).fetchone()

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

    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM public.clipparty_sessions
            WHERE token = %s
            """,
            (token,),
        )
        conn.commit()
