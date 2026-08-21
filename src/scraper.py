"""Scrape real job postings for every distinct (title, location) combo
across all active candidate profiles - one scrape shared by every user
who wants that search, not one scrape per user.

Reuses the exact JobSpy configuration already proven to work in the
original single-user pipeline, including every real-world fix found
along the way:
  - linkedin_fetch_description=True (without it, LinkedIn results have
    empty descriptions, breaking skill/language extraction downstream)
  - country_indeed="germany" (without it, Indeed silently searches the
    US site and returns nothing relevant for German cities)
  - "site" / "interval" as the actual JobSpy column names, not the
    guessed-wrong "site_name" / "salary_interval"
"""

import itertools
from datetime import datetime
from typing import List, Tuple

import pandas as pd
import psycopg2
from jobspy import scrape_jobs

from language_detector import detect_language_requirements


def _get_active_search_combinations(cur) -> List[Tuple[str, str]]:
    """Every distinct (job_title, location) pair across all active
    profiles - deduplicated, so two users searching the same thing only
    trigger one real scrape."""
    cur.execute("SELECT job_titles, locations FROM candidate_profiles")
    combos = set()
    for job_titles, locations in cur.fetchall():
        for title, location in itertools.product(job_titles, locations):
            combos.add((title, location))
    return sorted(combos)


def _get_or_create_query_id(cur, job_title: str, location: str) -> int:
    cur.execute(
        "SELECT query_id FROM search_queries WHERE job_title = %s AND location = %s",
        (job_title, location),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO search_queries (job_title, location) VALUES (%s, %s) RETURNING query_id",
        (job_title, location),
    )
    return cur.fetchone()[0]


def _clean(value):
    """Convert pandas NaN/NaT to None so psycopg2 can bind it as SQL
    NULL - real scraped data has missing dates/salary far more often
    than clean test fixtures suggest."""
    if value is None:
        return None
    if isinstance(value, (list, dict, set, tuple)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def scrape_for_active_profiles(conn_params: dict, results_wanted: int = 20) -> dict:
    """Scrape real jobs for every active profile's search criteria, load
    them into raw_job_postings, then clean into cleaned_job_postings.

    Returns a summary dict for display in the app (searches run, jobs
    found, jobs actually new).
    """
    conn = psycopg2.connect(**conn_params)
    summary = {"searches_run": 0, "jobs_found": 0, "jobs_new": 0}
    try:
        cur = conn.cursor()
        combos = _get_active_search_combinations(cur)

        for job_title, location in combos:
            query_id = _get_or_create_query_id(cur, job_title, location)
            conn.commit()

            try:
                jobs_df = scrape_jobs(
                    # Always scrape every supported platform, regardless
                    # of any individual profile's preference - filtering
                    # to what a profile actually wants to see happens at
                    # display time (get_scores_for_profile), not here.
                    # This keeps the shared-scrape model simple: one
                    # search per (title, location) still serves everyone,
                    # whatever platforms they each chose.
                    site_name=["linkedin", "indeed", "glassdoor"],
                    search_term=job_title,
                    location=location,
                    results_wanted=results_wanted,
                    hours_old=72,  # widen beyond 24h since this runs on-demand, not daily yet
                    linkedin_fetch_description=True,
                    country_indeed="germany",
                )
            except Exception as e:
                # one search failing shouldn't stop the others - matches
                # the original project's per-search error handling
                summary.setdefault("errors", []).append(f"{job_title} / {location}: {e}")
                continue

            summary["searches_run"] += 1
            if jobs_df is None or jobs_df.empty:
                continue
            summary["jobs_found"] += len(jobs_df)

            for _, row in jobs_df.iterrows():
                try:
                    # Real scraped data is messier than test fixtures -
                    # some postings genuinely have no company name. A
                    # sensible fallback keeps the row usable instead of
                    # violating the NOT NULL constraint and losing it.
                    title = _clean(row.get("title")) or "Untitled Position"
                    company = _clean(row.get("company")) or "Unknown Company"

                    cur.execute(
                        """
                        INSERT INTO raw_job_postings
                            (query_id, source_platform, title, company, location, description, job_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (job_url) DO NOTHING
                        RETURNING raw_job_id
                        """,
                        (
                            query_id,
                            _clean(row.get("site")),
                            title,
                            company,
                            _clean(row.get("location")),
                            _clean(row.get("description")),
                            _clean(row.get("job_url")),
                        ),
                    )
                    result = cur.fetchone()
                    if result:
                        raw_job_id = result[0]
                        summary["jobs_new"] += 1

                        cur.execute(
                            """
                            INSERT INTO cleaned_job_postings
                                (raw_job_id, title_clean, company_clean, location_clean, description_clean, job_url, source_platform, is_active)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                            ON CONFLICT (job_url) DO NOTHING
                            """,
                            (
                                raw_job_id,
                                title,
                                company,
                                _clean(row.get("location")),
                                _clean(row.get("description")),
                                _clean(row.get("job_url")),
                                _clean(row.get("site")),
                            ),
                        )

                    # Commit after each row, not just once per search -
                    # if a LATER row fails, rolling back only undoes that
                    # one row's own (already-failed) transaction, never
                    # losing rows that already succeeded in this batch.
                    conn.commit()

                except Exception as e:
                    conn.rollback()
                    summary.setdefault("row_errors", []).append(str(e))
                    continue

            cur.execute(
                "UPDATE search_queries SET last_scraped_at = NOW() WHERE query_id = %s",
                (query_id,),
            )
            conn.commit()

        return summary
    finally:
        conn.close()
