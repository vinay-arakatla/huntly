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
    required_years_experience: Optional[int] = None  # None = no years requirement detected in the text


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


def calculate_experience_score(profile: CandidateProfile, required_years: Optional[int] = None) -> int:
    """Experience fit: compares the candidate's years against the job's
    own stated years-of-experience requirement (extracted from the
    posting text by the caller - see scoring_runner.extract_required_years_experience).

    A job that states no years requirement at all keeps the old neutral
    placeholder score (no signal either way). A job that states one is
    now actually checked against - previously this was a flat +10
    regardless of whether the candidate had 3 years and the job wanted
    6+, which meant a real mismatch (e.g. this job's "6+ years" vs a
    3-year candidate) was invisible to the score entirely.
    """
    if required_years is None:
        return 10  # no requirement stated in the text - no basis to penalize

    gap = required_years - profile.years_experience
    if gap <= 0:
        return 10  # candidate already meets or exceeds the stated requirement
    if gap <= 2:
        return 3  # a couple of years short - still a plausible, common fit
    return -10  # well short of the stated requirement


# Generic job-title words that appear across many unrelated roles and
# therefore carry no signal about whether two titles are actually the
# same *kind* of role - e.g. "Localization Engineer" and "Data Engineer"
# share only "Engineer", which says nothing about role overlap. Without
# stripping these, any two titles that happen to share a common
# job-family word (Engineer, Analyst, Manager, Specialist, Consultant,
# Lead...) count as a full title match regardless of domain.
GENERIC_TITLE_WORDS = {
    "engineer", "developer", "analyst", "manager", "specialist",
    "consultant", "lead", "senior", "junior", "associate", "director",
    "officer", "coordinator", "administrator", "architect", "designer",
    "scientist", "staff", "principal", "head", "intern", "representative",
    "ii", "iii", "i",
}


def calculate_title_match_score(job_title: str, target_titles: List[str]) -> int:
    """Title fit: does this job's title share meaningful words with any
    of THIS profile's own target job titles?

    Deliberately profile-relative, not a hardcoded keyword list like
    "data analyst / BI analyst" - Huntly is multi-user, and different
    people search for completely different roles. A hardcoded list
    would only ever be correct for one person's job search, the same
    mistake the single-profile original project's design would make if
    reused here directly.

    Generic job-family words (see GENERIC_TITLE_WORDS) are stripped
    from both sides before comparing, so a match requires overlap on
    the words that actually identify the role ("data", "localization",
    "frontend", ...), not just a shared "Engineer"/"Analyst"/"Manager".
    If stripping leaves one side with nothing to compare (the title was
    only ever a generic word, e.g. just "Engineer"), fall back to the
    raw overlap so a fully generic title can still match a fully
    generic target rather than never matching anything.
    """
    job_title_lower = (job_title or "").lower()
    job_words = set(job_title_lower.split())
    meaningful_job_words = job_words - GENERIC_TITLE_WORDS

    for target in target_titles:
        target_words = set(target.lower().split())
        meaningful_target_words = target_words - GENERIC_TITLE_WORDS

        if meaningful_job_words and meaningful_target_words:
            if meaningful_job_words & meaningful_target_words:
                return 10
            continue  # both sides have real signal and it doesn't overlap - not a match

        # one (or both) sides reduced to nothing but generic words -
        # no domain-specific signal to compare, so fall back to raw overlap
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
    experience_score = calculate_experience_score(profile, job.required_years_experience)
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
