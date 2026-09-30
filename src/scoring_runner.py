"""Score every active candidate profile against the shared job pool.

This is the piece that will eventually run as an invisible background
job (Airflow, in a later phase) - for now it's callable directly from
the app (a "refresh my matches" action), so the dashboard has
something real to show without needing the full pipeline built yet.
"""

import re
from typing import Dict, List, Optional

import psycopg2

from language_detector import detect_language_requirements
from scorer import CandidateProfile, JobPosting, score_job_for_profile


# Baseline skill vocabulary scanned for on every job, independent of what
# any candidate profile has declared.
#
# Bug this fixes: job-skill extraction only ever scanned for skills that
# SOME profile already had (_get_all_known_skills), so a job's detected
# "required skills" set (the denominator of the skill-match score) could
# be almost entirely made of skills nobody in the system has declared -
# e.g. a Localization Engineer posting asking for CrowdIn/XLIFF/ICU/i18n
# would detect zero of its real requirements if no profile happened to
# list those, making the job look like a 100% skill match off one or two
# incidental mentions (Python, Docker) instead of the dozen things it
# actually needs. This list is still necessarily incomplete - it can't
# anticipate every possible skill a posting might name - but it removes
# the structural dependency on "did some other user happen to type this
# exact word into their profile."
BASELINE_SKILLS: List[str] = [
    # Languages
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Rust",
    "PHP", "Ruby", "Swift", "Kotlin", "Scala", "R", "Bash",
    # Web / frontend
    "HTML", "CSS", "React", "Angular", "Vue", "Node.js",
    # Data / analytics / BI
    "SQL", "Excel", "Power BI", "Tableau", "ETL", "Airflow", "PostgreSQL",
    "MySQL", "MongoDB", "Spark", "Machine Learning", "NLP",
    # Cloud / infra / devops
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git", "Jenkins",
    "GitHub Actions", "GitLab CI", "CI/CD", "REST API", "GraphQL",
    "Webhooks", "Terraform",
    # Localization / internationalization (the category that was
    # entirely missing before and motivated this fix)
    "Localization", "Internationalization", "i18n", "l10n", "CrowdIn",
    "XLIFF", "ICU", "Unicode", "Gettext", "RTL",
    # Product / design / collaboration
    "Figma", "Photoshop", "Illustrator", "JSON", "YAML", "XML", "Agile",
    "Scrum", "Jira",
    # Sales / marketing / CRM
    "Salesforce", "HubSpot", "SEO", "Google Analytics",
]


def _skill_universe(known_skills: List[str]) -> List[str]:
    """Skills to scan a job for: every profile-declared skill, plus the
    baseline vocabulary above - deduplicated case-insensitively, keeping
    whichever spelling appears first (profile spelling wins, since
    that's the one that needs to match a candidate's own stored skill)."""
    seen = set()
    combined = []
    for skill in list(known_skills) + BASELINE_SKILLS:
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        combined.append(skill)
    return combined


# Minimum years-of-experience patterns, most specific first. Each
# capture group is a lower-bound number of years; where a posting gives
# a range ("5-7 years") we take the lower bound, since that's the actual
# minimum bar a candidate needs to clear.
_YEARS_PATTERNS = [
    re.compile(r"(\d+)\s*\+\s*years?"),                                   # "6+ years"
    re.compile(r"(\d+)\s*(?:-|to)\s*\d+\s*years?"),                        # "5-7 years" / "5 to 7 years"
    re.compile(r"(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d+)\s*years?"),  # "at least 3 years"
    re.compile(r"(\d+)\s*years?\s*(?:of\s*)?(?:dedicated\s+)?experience"),  # "6 years of experience"
]


def extract_required_years_experience(description: str) -> Optional[int]:
    """Extract the minimum years-of-experience requirement stated in a
    job description, if any. Returns None when no such requirement is
    stated in recognizable form - not 0, since "not stated" and
    "explicitly wants 0 years" are different things and the caller
    (calculate_experience_score) treats them differently.

    Where multiple year figures are found, the lowest is used - a
    posting rarely gives more than one *minimum*, and taking the lowest
    avoids accidentally picking up an unrelated larger number (like a
    team size or a founding year) that one of the patterns loosely caught.
    """
    if not description:
        return None

    text = description.lower()
    candidates = []
    for pattern in _YEARS_PATTERNS:
        for m in pattern.finditer(text):
            try:
                candidates.append(int(m.group(1)))
            except (ValueError, IndexError):
                continue

    if not candidates:
        return None
    return min(candidates)


def _skill_pattern(skill: str) -> str:
    """Regex to find a skill's mentions in job text, tolerant of common
    word-variant endings.

    Bug this fixes: a job posting saying "Dashboards" or "Reports" in
    prose was never credited to a candidate's stored "Dashboarding" /
    "Reporting" skills (or vice versa), because the old exact
    word-boundary match required the literal stored string to appear
    verbatim. Real ad copy uses whichever grammatical form fits the
    sentence, not necessarily the noun form a candidate typed into
    their profile.

    Multi-word skills ("Power BI") and skills with non-letter characters
    keep exact whole-phrase matching - stemming is only applied to
    single alphabetic words, and only when the resulting stem is long
    enough to not start matching unrelated words (e.g. "R" or "Go"
    must stay exact, or the pattern would match almost anything).
    """
    skill_lower = skill.lower()
    if " " in skill_lower or not skill_lower.isalpha():
        return r"\b" + re.escape(skill_lower) + r"\b"

    stem = skill_lower
    for suffix in ("ing", "ies", "ed", "es"):
        if skill_lower.endswith(suffix) and len(skill_lower) - len(suffix) >= 3:
            stem = skill_lower[: -len(suffix)]
            break
    else:
        if skill_lower.endswith("s") and not skill_lower.endswith("ss") and len(skill_lower) >= 3:
            stem = skill_lower[:-1]

    if len(stem) < 3:
        # too short to safely allow suffix variation (e.g. "R", "Go")
        return r"\b" + re.escape(skill_lower) + r"\b"

    return r"\b" + re.escape(stem) + r"(?:s|es|ed|ing)?\b"


def detect_seniority_level(title: str, description: str) -> str:
    """Detect a job's seniority level from its title/description text.

    Same keyword-based approach as the original project's
    parse_seniority_level - checked in most-specific-first order so
    "Senior" isn't accidentally missed by a looser earlier match - but
    matched on whole words, not bare substrings.

    Bug this fixes: plain substring checks matched "intern" inside
    "international"/"internal"/"internet" (and inside the German
    "intern"/"interne"/"internen", meaning "internal" - very common in
    German postings), "mid" inside "middleware"/"midsize", and "lead"
    inside "leading"/"leadership"/"leader" in phrases like "a leading
    provider of..." - each silently mislabeling postings.

    Word-boundary matching fixes all of those. It does NOT fully solve
    "intern": German also uses "intern" as a standalone adjective
    meaning "internal" (e.g. "Diese Position ist intern"), which is
    spelled identically to the English noun and can't be told apart by
    word-boundary matching alone. To avoid that false positive, a bare
    "intern" is only treated as a Junior signal when it appears in the
    job TITLE (real internship postings put it there - "Software
    Engineering Intern"), not anywhere in the body text; the body text
    instead relies on unambiguous signals ("internship", "praktikant",
    "werkstudent", "trainee").
    """
    title_lower = (title or "").lower()
    combined = f"{title_lower} {description or ''}".lower()

    def has_word(pattern: str) -> bool:
        return re.search(pattern, combined) is not None

    if has_word(r"\bsenior\b") or has_word(r"\blead\b"):
        return "Senior"
    if has_word(r"\bmid\b") or has_word(r"\bmid-level\b") or has_word(r"\bintermediate\b"):
        return "Mid-level"
    if (
        has_word(r"\bjunior\b")
        or has_word(r"\bentry\b")
        or has_word(r"\bgraduate\b")
        or has_word(r"\btrainee\b")
        or has_word(r"\binternship\b")
        or has_word(r"\bpraktikant\w*\b")
        or has_word(r"\bwerkstudent\w*\b")
        or re.search(r"\bintern\b", title_lower)
    ):
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
        "SELECT job_id, title_clean, seniority_level, required_years_experience "
        "FROM cleaned_job_postings WHERE is_active = TRUE"
    )
    jobs = {}
    for job_id, title, seniority_level, required_years_experience in cur.fetchall():
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
            required_years_experience=required_years_experience,
        )
    return jobs


def _get_all_known_skills(cur) -> List[str]:
    """Every distinct skill declared across all active profiles, combined
    with the baseline vocabulary (see _skill_universe/BASELINE_SKILLS)
    that's scanned for on every job regardless of what any profile has
    declared. Dynamic by design on the profile side: a hardcoded list
    can never cover every user's actual skill set on a multi-user
    product, but relying SOLELY on profile-declared skills meant a
    job's detected requirements could never include anything no one
    had typed into a profile yet (see BASELINE_SKILLS for the bug this
    fixes)."""
    cur.execute("SELECT DISTINCT unnest(skills) FROM candidate_profiles")
    profile_skills = [row[0] for row in cur.fetchall()]
    return _skill_universe(profile_skills)


def ensure_job_metadata(cur, job_id: int, title: str, description: str, known_skills: List[str]) -> None:
    """Extract and store skills, language requirements, seniority level,
    and required years of experience for a job.

    Always re-scans (safe - inserts are ON CONFLICT DO NOTHING, and the
    seniority/years UPDATE is idempotent) rather than skipping already-
    processed jobs, so a newly-added skill (e.g. from a new user's
    profile) gets picked up on already-scraped jobs too, not just
    future ones.
    """
    for skill in known_skills:
        # Word-boundary + word-variant match (see _skill_pattern) - not
        # naive substring ("SQL" as a bare substring would incorrectly
        # match inside "PostgreSQL") and not a rigid exact-string match
        # either ("Dashboards" in the posting should still credit a
        # candidate's stored "Dashboarding").
        pattern = _skill_pattern(skill)
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
    required_years = extract_required_years_experience(description)
    cur.execute(
        "UPDATE cleaned_job_postings SET seniority_level = %s, required_years_experience = %s WHERE job_id = %s",
        (seniority, required_years, job_id),
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
    display on the dashboard. Returns every match regardless of
    platform - platform is a display-time filter the user controls on
    the results page itself, not a restriction baked into what gets
    scored or stored."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.job_id, c.title_clean, c.company_clean, c.location_clean, c.job_url,
                   s.match_score, s.priority_level, s.matched_skills, s.missing_skills,
                   s.language_penalty_applied, c.source_platform, c.job_fetch_date, s.applied
            FROM user_job_scores s
            JOIN cleaned_job_postings c ON s.job_id = c.job_id
            WHERE s.profile_id = %s AND c.is_active = TRUE
            ORDER BY s.match_score DESC
            """,
            (profile_id,),
        )
        columns = [
            "job_id", "title", "company", "location", "job_url", "match_score",
            "priority_level", "matched_skills", "missing_skills",
            "language_penalty_applied", "source_platform", "job_fetch_date", "applied",
        ]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def set_applied_status(conn_params: dict, profile_id: int, job_id: int, applied: bool) -> None:
    """Mark (or unmark) a specific job as applied-to for a given profile."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE user_job_scores
            SET applied = %s, applied_at = CASE WHEN %s THEN NOW() ELSE NULL END
            WHERE profile_id = %s AND job_id = %s
            """,
            (applied, applied, profile_id, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_stale_jobs(conn_params: dict, days: int = 7) -> int:
    """Permanently delete job postings older than N days (by
    job_fetch_date), so the dataset doesn't grow unbounded and "what's
    new" stays meaningful over time.

    Deletion order matters here: cleaned_job_postings must be deleted
    first - job_skills, job_language_requirements, and user_job_scores
    all reference it with ON DELETE CASCADE, so those clean up
    automatically. raw_job_postings, however, has no cascade from
    cleaned_job_postings (the foreign key points the other way), so its
    rows are deleted afterward, once nothing still references them.
    Deleting raw_job_postings first would fail with a foreign key
    violation while a cleaned row still points to it.

    Returns the number of postings deleted.
    """
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT job_id, raw_job_id FROM cleaned_job_postings WHERE job_fetch_date < CURRENT_DATE - %s::int",
            (days,),
        )
        stale = cur.fetchall()
        if not stale:
            return 0

        stale_job_ids = [row[0] for row in stale]
        stale_raw_ids = [row[1] for row in stale if row[1] is not None]

        cur.execute("DELETE FROM cleaned_job_postings WHERE job_id = ANY(%s)", (stale_job_ids,))
        if stale_raw_ids:
            cur.execute("DELETE FROM raw_job_postings WHERE raw_job_id = ANY(%s)", (stale_raw_ids,))

        conn.commit()
        return len(stale_job_ids)
    finally:
        conn.close()
