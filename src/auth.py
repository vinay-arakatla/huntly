"""User accounts: signup and login.

Deliberately simple for Phase 2 - hashed passwords, no sessions/JWT yet
(the Streamlit app keeps the logged-in user_id in its own session state).
No password reset, no email verification - those are real gaps, fine for
"a second real person could use this," not fine for a public launch.
"""

import re
from typing import Optional, Tuple

import bcrypt
import psycopg2


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _get_connection(conn_params: dict):
    return psycopg2.connect(**conn_params)


def signup(conn_params: dict, email: str, password: str) -> Tuple[Optional[int], str]:
    """Create a new user account.

    Returns:
        (user_id, message) - user_id is None if signup failed, with
        message explaining why.
    """
    email = email.strip().lower()

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
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING user_id",
            (email, password_hash),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        return user_id, "Account created successfully."
    finally:
        conn.close()


def login(conn_params: dict, email: str, password: str) -> Tuple[Optional[int], str]:
    """Verify credentials and return the user_id if they're correct.

    Returns:
        (user_id, message) - user_id is None if login failed.
    """
    email = email.strip().lower()

    conn = _get_connection(conn_params)
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, password_hash FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row is None:
            return None, "No account found with this email."

        user_id, password_hash = row
        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            return None, "Incorrect password."

        return user_id, "Logged in successfully."
    finally:
        conn.close()
