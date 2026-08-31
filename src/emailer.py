"""Send password reset codes by email.

Configuration comes from Streamlit secrets (an [smtp] section, alongside
the existing [db] section) - never hardcoded, same pattern as the
database credentials.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional


def send_reset_code_email(smtp_config: dict, to_email: str, code: str) -> bool:
    """Email a password reset code. Returns True on success, False on
    any failure - never raises, so a delivery problem doesn't crash the
    app; the caller should just tell the user to try again or contact
    support.
    """
    if not smtp_config:
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = smtp_config.get("from", smtp_config.get("user"))
        msg["To"] = to_email
        msg["Subject"] = "Your Huntly password reset code"

        body = (
            f"Your password reset code is: {code}\n\n"
            f"This code expires in 15 minutes. If you didn't request this, "
            f"you can safely ignore this email."
        )
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_config["host"], smtp_config.get("port", 587), timeout=30) as server:
            server.starttls()
            server.login(smtp_config["user"], smtp_config["password"])
            server.send_message(msg)

        return True
    except Exception:
        return False
