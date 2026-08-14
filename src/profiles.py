"""Create and read candidate profiles, tied to a real user account."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import psycopg2


@dataclass
class Profile:
    profile_id: int
    user_id: int
    profile_name: str
    job_titles: List[str]
    locations: List[str]
    skills: List[str]
    years_experience: int
    languages: Dict[str, str] = field(default_factory=dict)


def create_profile(
    conn_params: dict,
    user_id: int,
    profile_name: str,
    job_titles: List[str],
    locations: List[str],
    skills: List[str],
    years_experience: int,
    languages: Dict[str, str],
) -> int:
    """Create a new profile for a user. Returns the new profile_id."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO candidate_profiles
                (user_id, profile_name, job_titles, locations, skills, years_experience)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING profile_id
            """,
            (user_id, profile_name, job_titles, locations, skills, years_experience),
        )
        profile_id = cur.fetchone()[0]

        for language, level in languages.items():
            cur.execute(
                "INSERT INTO candidate_languages (profile_id, language, proficiency) VALUES (%s, %s, %s)",
                (profile_id, language, level),
            )

        conn.commit()
        return profile_id
    finally:
        conn.close()


def get_profiles_for_user(conn_params: dict, user_id: int) -> List[Profile]:
    """Get every profile belonging to a user (usually just one, but the
    schema allows more - e.g. someone job-hunting in two different
    fields at once)."""
    conn = psycopg2.connect(**conn_params)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT profile_id, user_id, profile_name, job_titles, locations, skills, years_experience
            FROM candidate_profiles WHERE user_id = %s ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

        profiles = []
        for profile_id, uid, name, titles, locs, skills, years_exp in rows:
            cur.execute(
                "SELECT language, proficiency FROM candidate_languages WHERE profile_id = %s",
                (profile_id,),
            )
            languages = dict(cur.fetchall())
            profiles.append(Profile(
                profile_id=profile_id, user_id=uid, profile_name=name,
                job_titles=titles, locations=locs, skills=skills,
                years_experience=years_exp, languages=languages,
            ))
        return profiles
    finally:
        conn.close()
