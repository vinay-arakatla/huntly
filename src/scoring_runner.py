"""Score every active candidate profile against the shared job pool.

This is the piece that will eventually run as an invisible background
job (Airflow, in a later phase) - for now it's callable directly from
the app (a "refresh my matches" action), so the dashboard has
something real to show without needing the full pipeline built yet.
"""

import re
from typing import Dict, List

import psycopg2

from language_detector import detect_language_requirements
from scorer import CandidateProfile, JobPosting, score_job_for_profile


def detect_seniority_level(title: str, description: str) -> str:
    """Detect a job's seniority level from its title/description text.

    Same keyword-based approach as the original project's
    parse_seniority_level - checked in most-specific-first order so
    "Senior" isn't accidentally missed by a looser earlier match.
    """
    combined = f"{title or ''} {description or ''}".lower()

    if "senior" in combined or "lead" in combined:
        return "Senior"
    if "mid" in combined or "intermediate" in combined:
        return "Mid-level"
    if "junior" in combined or "entry" in combined or "intern" in combined or "graduate" in combined:
        return "Junior"
    return "Not Specified"


def _load_profiles(cur) -> Dict[int, CandidateProfile]:
    cur.execute("SELECT profile_id, skills, years_experience, job_titles FROM candidate_profiles")
    profiles = {}
    for profile_id, skills, years_exp, job_titles in cur.fetchall():
        cur.execute(
            "SELECT language, proficiency FROM candidate_languages WHERE profile_id = %s",
            (profile_id,),
        )
        languages = dict(cur.fetchall())
        profiles[profile_id] = CandidateProfile(
            profile_id=profile_id, skills=skills, years_experience=years_exp,
            job_titles=job_titles, languages=languages,
        )
    return profiles


def _load_jobs(cur) -> Dict[int, JobPosting]:
    cur.execute(
        "SELECT job_id, title_clean, seniority_level FROM cleaned_job_postings WHERE is_active = TRUE"
    )
    jobs = {}
    for job_id, title, seniority_level in cur.fetchall():
        cur.execute("SELECT skill_name FROM job_skills WHERE job_id = %s", (job_id,))
        skills = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT language, required_level FROM job_language_requirements WHERE job_id = %s",
            (job_id,),
        )
        lang_reqs = cur.fetchall()
        jobs[job_id] = JobPosting(
            job_id=job_id, title=title, skills=skills, language_requirements=lang_reqs,
            seniority_level=seniority_level or "Not Specified",
        )
    return jobs


def _get_all_known_skills(cur) -> List[str]:
    """Every distinct skill declared across all active profiles - these
    are the only skills that could ever matter for scoring anyone right
    now, so this is what job postings get scanned for. Dynamic by
    design: a hardcoded list can never cover every user's actual skill
    set on a multi-user product."""
    cur.execute("SELECT DISTINCT unnest(skills) FROM candidate_profiles")
    return [row[0] for row in cur.fetchall()]


def ensure_job_metadata(cur, job_id: int, title: str, description: str, known_skills: List[str]) -> None:
    """Extract and store skills, language requirements, and seniority
    level for a job.

    Always re-scans (safe - inserts are ON CONFLICT DO NOTHING, and the
    seniority UPDATE is idempotent) rather than skipping already-
    processed jobs, so a newly-added skill (e.g. from a new user's
    profile) gets picked up on already-scraped jobs too, not just
    future ones.
    """
    for skill in known_skills:
        # Word-boundary match, not naive substring - "SQL" as a bare
        # substring check would incorrectly match inside "PostgreSQL"
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, (description or "").lower()):
            cur.execute(
                "INSERT INTO job_skills (job_id, skill_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (job_id, skill),
            )

    for language, level in detect_language_requirements(description):
        cur.execute(
            """INSERT INTO job_language_requirements (job_id, language, required_level)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (job_id, language, level),
        )

    seniority = detect_seniority_level(title, description)
    cur.execute(
        "UPDATE cleaned_job_postings SET seniority_level = %s WHERE job_id = %s",
        (seniority, job_id),
    )


def score_all_active_profiles(conn_params: dict) -> int:
    """Score every active profile against every active job in the shared
    pool, storing results in user_job_scores. Returns the number of
    (profile, job) scores written or updated."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()

        # every skill declared across all active profiles - the only
        # ones that could ever matter for scoring anyone right now
        known_skills = _get_all_known_skills(cur)

        # make sure every cleaned job has skills/language/seniority extracted
        cur.execute(
            "SELECT job_id, title_clean, description_clean FROM cleaned_job_postings WHERE is_active = TRUE"
        )
        for job_id, title, description in cur.fetchall():
            ensure_job_metadata(cur, job_id, title, description, known_skills)
        conn.commit()

        profiles = _load_profiles(cur)
        jobs = _load_jobs(cur)

        count = 0
        for profile in profiles.values():
            for job in jobs.values():
                result = score_job_for_profile(profile, job)
                cur.execute(
                    """
                    INSERT INTO user_job_scores
                        (profile_id, job_id, match_score, priority_level, matched_skills, missing_skills, language_penalty_applied)
                    VALUES (%(profile_id)s, %(job_id)s, %(match_score)s, %(priority_level)s, %(matched_skills)s, %(missing_skills)s, %(language_penalty_applied)s)
                    ON CONFLICT (profile_id, job_id) DO UPDATE SET
                        match_score = EXCLUDED.match_score,
                        priority_level = EXCLUDED.priority_level,
                        matched_skills = EXCLUDED.matched_skills,
                        missing_skills = EXCLUDED.missing_skills,
                        language_penalty_applied = EXCLUDED.language_penalty_applied
                    """,
                    result,
                )
                count += 1

        conn.commit()
        return count
    finally:
        conn.close()


def get_scores_for_profile(conn_params: dict, profile_id: int) -> List[dict]:
    """Fetch a profile's scored matches, joined with job details, for
    display on the dashboard."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.title_clean, c.company_clean, c.location_clean, c.job_url,
                   s.match_score, s.priority_level, s.matched_skills, s.missing_skills,
                   s.language_penalty_applied
            FROM user_job_scores s
            JOIN cleaned_job_postings c ON s.job_id = c.job_id
            WHERE s.profile_id = %s AND c.is_active = TRUE
            ORDER BY s.match_score DESC
            """,
            (profile_id,),
        )
        columns = [
            "title", "company", "location", "job_url", "match_score",
            "priority_level", "matched_skills", "missing_skills", "language_penalty_applied",
        ]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()
