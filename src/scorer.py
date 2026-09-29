"""Score cleaned job postings against a candidate profile.

Fresh implementation for Huntly - conceptually similar to the original
project's scoring approach (skill match + title fit + seniority fit +
language fit + experience fit), but every candidate-specific value
comes from a CandidateProfile object passed in, not a single global
.env profile. The same job pool can be scored against as many
different profiles as needed, independently.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from language_detector import meets_requirement


@dataclass
class CandidateProfile:
    """One person's search criteria and background."""

    profile_id: int
    skills: List[str]
    years_experience: int
    job_titles: List[str] = field(default_factory=list)
    languages: Dict[str, str] = field(default_factory=dict)  # {"English": "Native", "German": "B1"}


@dataclass
class JobPosting:
    """A cleaned job posting from the shared scrape pool."""

    job_id: int
    title: str
    skills: List[str]
    language_requirements: List[Tuple[str, str]]  # [("German", "B1")]
    seniority_level: str = "Not Specified"  # Junior, Mid-level, Senior, Not Specified


def calculate_skill_score(profile: CandidateProfile, job: JobPosting) -> Tuple[int, List[str], List[str]]:
    """Skill match: up to 50 points, scaled by what fraction of the
    skills THIS job actually asks for the candidate has.

    Deliberately relative to the job's own detected skills, not a flat
    5-points-per-match count capped at 50. A flat count punishes a
    posting that only mentions one or two skills in prose (common for
    business-facing ad copy, as opposed to a bullet-point requirements
    list) even when the candidate has every one of them - a job that
    mentions exactly the one skill the candidate has is a 100% match on
    the skills it states, and should score like one, not like a job
    that named ten skills and the candidate only had one.

    A job with zero detected skills scores 0 here (no signal either
    way) rather than being padded up - the caller's title/seniority/
    language components still carry the score in that case.
    """
    user_skills = set(s.lower() for s in profile.skills)
    job_skills = set(s.lower() for s in job.skills)

    matched = job_skills & user_skills
    missing = job_skills - user_skills

    if not job_skills:
        return 0, [], []

    score = round(50 * len(matched) / len(job_skills))
    return score, sorted(matched), sorted(missing)


def calculate_skill_gap_penalty(missing_skills: List[str]) -> int:
    """Small, capped penalty for skills the job wants that the candidate
    doesn't have - same calibration as the original project."""
    if not missing_skills:
        return 0
    return -min(len(missing_skills), 10)


def calculate_language_score(profile: CandidateProfile, job: JobPosting) -> Tuple[int, bool]:
    """Language fit: no detected requirement = full points. A detected
    requirement the candidate meets = full points. One they don't meet =
    penalty. Works for any language, not just German.

    Returns:
        (score, penalty_applied) - penalty_applied is used for
        transparency in stored results (so a user can see *why* a score
        was reduced, not just the number).
    """
    if not job.language_requirements:
        return 10, False  # nothing stated - no basis to penalize

    for language, required_level in job.language_requirements:
        candidate_level = profile.languages.get(language)
        if candidate_level is None:
            # job requires a language the candidate hasn't stated at all
            return -20, True
        if not meets_requirement(required_level, candidate_level):
            return -20, True

    return 10, False


def calculate_experience_score(profile: CandidateProfile) -> int:
    """Simple experience-fit placeholder - a full posted-range comparison
    (like the original project's exp_min/exp_max matching) is a later
    step, once real scraped experience-range data is wired in."""
    return 10 if profile.years_experience >= 0 else 0


def calculate_title_match_score(job_title: str, target_titles: List[str]) -> int:
    """Title fit: does this job's title share meaningful words with any
    of THIS profile's own target job titles?

    Deliberately profile-relative, not a hardcoded keyword list like
    "data analyst / BI analyst" - Huntly is multi-user, and different
    people search for completely different roles. A hardcoded list
    would only ever be correct for one person's job search, the same
    mistake the single-profile original project's design would make if
    reused here directly.
    """
    job_title_lower = (job_title or "").lower()
    job_words = set(job_title_lower.split())

    for target in target_titles:
        target_words = set(target.lower().split())
        if target_words & job_words:
            return 10

    return 0


def calculate_seniority_score(seniority_level: str, years_experience: int) -> int:
    """Seniority fit: relative to THIS profile's own years of
    experience, not a fixed "Junior is always best" assumption.

    A 0-2 year candidate wants Junior roles; a 7+ year candidate wants
    Senior roles. Hardcoding "Junior is ideal" (as the single-profile
    original project reasonably did, since that one person was early
    career) would actively misscore an experienced candidate on a
    multi-user product - it would reward exactly the roles a senior
    candidate should probably skip.
    """
    if not seniority_level or seniority_level.lower() == "not specified":
        return 0

    if years_experience <= 2:
        ideal = "junior"
    elif years_experience <= 6:
        ideal = "mid-level"
    else:
        ideal = "senior"

    level = seniority_level.lower()
    order = ["junior", "mid-level", "senior"]

    if level == ideal:
        return 20
    if level not in order:
        return 0

    distance = abs(order.index(level) - order.index(ideal))
    if distance == 1:
        return 5  # adjacent level - not ideal, but not a bad fit either
    return -10  # opposite ends (e.g. Junior candidate vs Senior role)


def calculate_final_score(
    skill_score: int,
    skill_gap_penalty: int,
    language_score: int,
    experience_score: int,
    title_match_score: int,
    seniority_score: int,
) -> int:
    """Sum weighted components, clamp to 0-100. Max achievable:
    50 (skills) + 10 (title) + 20 (seniority) + 10 (language) +
    10 (experience) = 100."""
    total = (
        skill_score + skill_gap_penalty + language_score + experience_score
        + title_match_score + seniority_score
    )
    return max(0, min(100, int(total)))


def calculate_priority(score: int) -> str:
    if score >= 80:
        return "High"
    elif score >= 50:
        return "Medium"
    return "Low"


def score_job_for_profile(profile: CandidateProfile, job: JobPosting) -> dict:
    """Score one job against one profile."""
    skill_score, matched, missing = calculate_skill_score(profile, job)
    skill_gap_penalty = calculate_skill_gap_penalty(missing)
    language_score, language_penalty_applied = calculate_language_score(profile, job)
    experience_score = calculate_experience_score(profile)
    title_match_score = calculate_title_match_score(job.title, profile.job_titles)
    seniority_score = calculate_seniority_score(job.seniority_level, profile.years_experience)

    final_score = calculate_final_score(
        skill_score, skill_gap_penalty, language_score, experience_score,
        title_match_score, seniority_score,
    )
    priority = calculate_priority(final_score)

    return {
        "profile_id": profile.profile_id,
        "job_id": job.job_id,
        "match_score": final_score,
        "priority_level": priority,
        "matched_skills": matched,
        "missing_skills": missing,
        "language_penalty_applied": language_penalty_applied,
    }
