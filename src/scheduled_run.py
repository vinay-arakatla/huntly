"""Standalone script for scheduled, unattended scraping and scoring.

Runs outside Streamlit entirely (via GitHub Actions on a cron schedule),
so it reads connection details from plain environment variables rather
than st.secrets. Calls the exact same scraper.py / scoring_runner.py
functions the app itself uses when a user clicks the buttons manually -
this script just means someone doesn't have to.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from scraper import scrape_for_active_profiles
from scoring_runner import score_all_active_profiles


def get_conn_params() -> dict:
    """Connection settings from environment variables (set as GitHub
    Actions repository secrets in the actual scheduled workflow)."""
    return dict(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


def main() -> None:
    conn_params = get_conn_params()

    print("Starting scheduled scrape...")
    summary = scrape_for_active_profiles(conn_params)
    print(f"Scrape complete: {summary}")

    print("Scoring all active profiles...")
    count = score_all_active_profiles(conn_params)
    print(f"Scoring complete: {count} (profile, job) pairs scored.")


if __name__ == "__main__":
    main()
