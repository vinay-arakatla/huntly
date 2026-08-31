"""User accounts: signup, login, and password reset."""

import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import bcrypt
import psycopg2


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESET_CODE_VALIDITY_MINUTES = 15


def _get_connection(conn_params: dict):
    return psycopg2.connect(**conn_params)


def signup(conn_params: dict, name: str, email: str, password: str) -> Tuple[Optional[int], str]:
    """Create a new user account.

    Returns:
        (user_id, message) - user_id is None if signup failed, with
        message explaining why.
    """
    name = name.strip()
    email = email.strip().lower()

    if not name:
        return None, "Please enter your name."
    if not EMAIL_PATTERN.match(email):
        return None, "Please enter a valid email address."
    if len(password) < 8:
        return None, "Password must be at least 8 characters."

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = _get_connection(conn_params)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return None, "An account with this email already exists."

        cur.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) RETURNING user_id",
            (name, email, password_hash),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        return user_id, "Account created successfully."
    finally:
        conn.close()


def login(conn_params: dict, email: str, password: str) -> Tuple[Optional[int], Optional[str], str]:
    """Verify credentials and return the user_id and name if correct.

    Returns:
        (user_id, name, message) - user_id and name are None if login failed.
    """
    email = email.strip().lower()

    conn = _get_connection(conn_params)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, name, password_hash FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row is None:
            return None, None, "No account found with this email."

        user_id, name, password_hash = row
        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            return None, None, "Incorrect password."

        return user_id, name, "Logged in successfully."
    finally:
        conn.close()


def request_password_reset(conn_params: dict, email: str) -> Tuple[bool, str, Optional[str]]:
    """Generate a 6-digit reset code for an account, valid for 15 minutes.

    Returns:
        (success, message, code) - code is returned so the caller can
        email it; it is never itself the return value shown to the user
        in the UI (that would defeat the point of emailing it).
    """
    email = email.strip().lower()

    conn = _get_connection(conn_params)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row is None:
            # Deliberately vague - do not reveal whether an email is
            # registered, to avoid letting this endpoint be used to
            # enumerate real accounts.
            return False, "If an account exists with this email, a reset code has been sent.", None

        code = f"{random.randint(0, 999999):06d}"
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=RESET_CODE_VALIDITY_MINUTES)

        cur.execute(
            "UPDATE users SET reset_code = %s, reset_code_expires_at = %s WHERE email = %s",
            (code, expires_at, email),
        )
        conn.commit()
        return True, "If an account exists with this email, a reset code has been sent.", code
    finally:
        conn.close()


def confirm_password_reset(conn_params: dict, email: str, code: str, new_password: str) -> Tuple[bool, str]:
    """Verify a reset code and set a new password if it's valid and unexpired."""
    email = email.strip().lower()
    code = code.strip()

    if len(new_password) < 8:
        return False, "Password must be at least 8 characters."

    conn = _get_connection(conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT reset_code, reset_code_expires_at FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return False, "Invalid or expired reset code."

        stored_code, expires_at = row
        if stored_code != code:
            return False, "Invalid or expired reset code."
        if expires_at is None or datetime.now(timezone.utc).replace(tzinfo=None) > expires_at:
            return False, "Invalid or expired reset code."

        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute(
            "UPDATE users SET password_hash = %s, reset_code = NULL, reset_code_expires_at = NULL WHERE email = %s",
            (new_hash, email),
        )
        conn.commit()
        return True, "Password reset successfully. You may now log in."
    finally:
        conn.close()
