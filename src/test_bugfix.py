"""Standalone verification for the skill-scoring bug fixes - no DB needed.

Reproduces the exact failure mode the user reported (a German-language,
business-prose job posting that undervalues a strong-fit candidate) and
checks the fix raises the score, plus a few control cases to confirm the
fix doesn't overcorrect in the other direction.
"""
import re

from scorer import (
    CandidateProfile,
    JobPosting,
    calculate_skill_score,
    calculate_title_match_score,
    score_job_for_profile,
)
from scoring_runner import (
    _skill_pattern,
    _skill_universe,
    detect_seniority_level,
    extract_required_years_experience,
)
from language_detector import detect_language_requirements

PROFILE_SKILLS = [
    "SQL", "Python", "Power BI", "Tableau", "Excel", "Dashboarding",
    "Reporting", "KPI", "Data Analysis", "Ad-hoc Analysis", "ETL", "Airflow",
]

profile = CandidateProfile(
    profile_id=1,
    skills=PROFILE_SKILLS,
    years_experience=3,
    job_titles=["Data Analyst", "BI Analyst"],
    languages={"English": "C1", "German": "B2"},
)


def extract_job_skills(description: str, known_skills):
    found = []
    for skill in known_skills:
        pattern = _skill_pattern(skill)
        if re.search(pattern, description.lower()):
            found.append(skill)
    return found


def make_job(job_id, title, description):
    skills = extract_job_skills(description, PROFILE_SKILLS)
    lang_reqs = detect_language_requirements(description)
    seniority = detect_seniority_level(title, description)
    return JobPosting(job_id=job_id, title=title, skills=skills,
                       language_requirements=lang_reqs, seniority_level=seniority)


# --- Case 1: reconstructed General-Anzeiger-style posting ---
# German-language, business-prose ad: mentions Power BI, Dashboards,
# Reports, KPIs, ad-hoc analysis - but as flowing German prose (plural /
# verb forms), not the candidate's exact stored skill strings, and with
# no explicit "Deutsch B2 erforderlich" clause.
ga_description = """
Als Data Analyst (m/w/d) bist du verantwortlich für die Erstellung und
Pflege von Dashboards und Reports in Power BI. Du analysierst KPIs und
unterstuetzt Fachbereiche mit Ad-hoc Analysen. Sehr gute Excel-Kenntnisse
sind von Vorteil. Wir freuen uns auf deine Bewerbung.
"""
ga_job = make_job(1, "Data Analyst", ga_description)
ga_result = score_job_for_profile(profile, ga_job)

print("=== Case 1: General-Anzeiger-style posting ===")
print("Detected job skills:", ga_job.skills)
print("Matched:", ga_result["matched_skills"], "Missing:", ga_result["missing_skills"])
print("Language requirements detected:", ga_job.language_requirements)
print("Seniority:", ga_job.seniority_level)
print("Final score:", ga_result["match_score"], ga_result["priority_level"])
print()

# Manual comparison against the OLD flat-count formula, to show the delta
old_matched = set(s.lower() for s in ga_job.skills) & set(s.lower() for s in PROFILE_SKILLS)
old_skill_score = min(len(old_matched) * 5, 50)
print(f"Old flat-count skill score would have been: {old_skill_score} "
      f"(vs new: {calculate_skill_score(profile, ga_job)[0]})")
print()

# --- Case 2: the real KPMG job from the sample dump (job_id 464) ---
# Same pattern in real scraped data: "Dashboards und Reports in Power BI".
kpmg_description = """
Dein Schwerpunkt liegt auf der Analyse und Aufbereitung von Daten mit SQL
sowie der Entwicklung von Dashboards und Reports in Power BI. Du bringst
erste Erfahrungen im Bereich Data Analytics mit. Du hast sehr gute
Deutsch- und Englischkenntnisse in Wort und Schrift.
"""
kpmg_job = make_job(2, "Mitarbeiter Data & Analytics - Business Intelligence", kpmg_description)
kpmg_result = score_job_for_profile(profile, kpmg_job)
print("=== Case 2: real KPMG posting from sample data ===")
print("Detected job skills:", kpmg_job.skills)
print("Matched:", kpmg_result["matched_skills"], "Missing:", kpmg_result["missing_skills"])
print("Final score:", kpmg_result["match_score"], kpmg_result["priority_level"])
print()

# --- Case 3: control - job that mentions many skills, candidate has few ---
# Should NOT jump to a high score just because job_skills is small; here
# job_skills is large (8) and candidate matches only 2, so % should stay low.
control_description = """
Required: SQL, Python, Tableau, Excel, ETL, Airflow, KPI reporting, and
ad-hoc analysis. Dashboarding experience is a bonus.
"""
# Simulate candidate only knowing 2 of these for this control case
poor_fit_profile = CandidateProfile(
    profile_id=2, skills=["SQL", "Excel"], years_experience=3,
    job_titles=["Data Analyst"], languages={"English": "C1"},
)
control_job = make_job(3, "Data Analyst", control_description)
control_result = score_job_for_profile(poor_fit_profile, control_job)
print("=== Case 3: control - broad requirements, poor-fit candidate ===")
print("Detected job skills:", control_job.skills)
print("Matched:", control_result["matched_skills"], "Missing:", control_result["missing_skills"])
print("Final score:", control_result["match_score"], control_result["priority_level"])
print()

# --- Case 4: word-variant safety check - should NOT over-match unrelated words ---
print("=== Case 4: word-variant pattern safety checks ===")
safety_checks = [
    ("R", "We use the R programming language for stats.", True),
    ("R", "This report covers Q3 revenue.", False),  # "report" must not match bare "R"
    ("Java", "Backend built in Java.", True),
    ("Java", "Frontend uses JavaScript frameworks.", False),  # must not bleed into JavaScript
    ("Dashboarding", "We need someone skilled in dashboards and dashboarding.", True),
    ("Reporting", "Monthly reports are generated automatically.", True),
    ("KPI", "Tracking KPIs across the business.", True),
]
all_ok = True
for skill, text, should_match in safety_checks:
    pattern = _skill_pattern(skill)
    matched = bool(re.search(pattern, text.lower()))
    ok = matched == should_match
    all_ok &= ok
    print(f"  skill={skill!r:15} text={text!r:55} expected={should_match} got={matched} {'OK' if ok else 'FAIL'}")

print()
print("ALL SAFETY CHECKS PASSED" if all_ok else "SOME SAFETY CHECKS FAILED")

# --- Case 5: the Localization Engineer false-100 report ---
# Reproduces the actual bug report: a "Localization Engineer" posting
# (which shares nothing with this candidate's Data/BI background besides
# the generic word "Engineer", and needs a stack this candidate barely
# touches) was scoring 100/100 High before these fixes, driven by (a) the
# title-match false positive on "Engineer" and (b) a skill denominator
# limited to the two skills this profile happens to share with the
# posting (Python, Docker) rather than the job's real requirements.
data_profile = CandidateProfile(
    profile_id=3,
    skills=["Python", "SQL", "Apache Airflow", "PostgreSQL", "Power BI", "Tableau", "Docker", "ETL"],
    years_experience=3,
    job_titles=["Data Analyst", "Data Engineer", "BI Analyst"],
    languages={"English": "C1", "German": "B2"},
)
loc_eng_description = """
We are seeking a highly technical, hands-on Localization Engineer (LE) to
build, automate, and optimize an internationalization (i18n) and
localization (l10n) technical infrastructure within a Crowdin deployment,
including custom parsers, REST APIs, webhooks, XLIFF, ICU message syntax,
and Unicode handling. Required Qualifications: 6+ years of dedicated
experience in Localization Engineering. Familiarity with containerization
(Docker) and Python scripting for automation.
"""
loc_eng_known_skills = _skill_universe(data_profile.skills)
loc_eng_skills = extract_job_skills(loc_eng_description, loc_eng_known_skills)
loc_eng_required_years = extract_required_years_experience(loc_eng_description)
loc_eng_job = JobPosting(
    job_id=5, title="Localization Engineer", skills=loc_eng_skills,
    language_requirements=detect_language_requirements(loc_eng_description),
    seniority_level=detect_seniority_level("Localization Engineer", loc_eng_description),
    required_years_experience=loc_eng_required_years,
)
loc_eng_result = score_job_for_profile(data_profile, loc_eng_job)
print("=== Case 5: Localization Engineer vs Data/BI profile (the false-100 report) ===")
print("Detected job skills (now includes baseline vocabulary, not just profile skills):", loc_eng_job.skills)
print("Matched:", loc_eng_result["matched_skills"], "Missing:", loc_eng_result["missing_skills"])
print("Title match score (Engineer/Engineer should no longer count):",
      calculate_title_match_score(loc_eng_job.title, data_profile.job_titles))
print("Required years detected:", loc_eng_required_years, "(candidate has", data_profile.years_experience, ")")
print("Final score:", loc_eng_result["match_score"], loc_eng_result["priority_level"])
assert loc_eng_result["match_score"] < 100, "Localization Engineer should NOT score 100 against this profile"
assert calculate_title_match_score(loc_eng_job.title, data_profile.job_titles) == 0, \
    "Title match should not fire on 'Engineer' alone"
print("OK: no longer a bogus 100/100")
print()

# --- Case 6: title-match false positives from generic word overlap ---
print("=== Case 6: title-match generic-word false positives ===")
title_checks = [
    ("Localization Engineer", ["Data Engineer"], 0),   # only "Engineer" overlaps - should NOT match
    ("Senior Data Engineer", ["Data Engineer"], 10),    # "Data" overlaps - should match
    ("Marketing Manager", ["Product Manager"], 0),      # only "Manager" overlaps - should NOT match
    ("Engineer", ["Software Engineer"], 10),            # title is only a generic word - fallback overlap
]
title_ok = True
for job_title, targets, expected in title_checks:
    got = calculate_title_match_score(job_title, targets)
    ok = got == expected
    title_ok &= ok
    print(f"  job_title={job_title!r:25} targets={targets!r:20} expected={expected} got={got} {'OK' if ok else 'FAIL'}")
print("ALL TITLE CHECKS PASSED" if title_ok else "SOME TITLE CHECKS FAILED")
print()

# --- Case 7: seniority word-boundary false positives ---
print("=== Case 7: seniority detection word-boundary safety checks ===")
seniority_checks = [
    ("Software Engineer", "We are a leading provider of international solutions.", "Not Specified"),
    ("Backend Developer", "Work with our middleware and admit new ideas to the team.", "Not Specified"),
    ("Data Analyst", "Diese Position ist Teil unseres internen Teams.", "Not Specified"),  # German "internen" (inflected) must not trigger Junior
    ("Team Lead", "You will lead a team of engineers.", "Senior"),
    ("Mid-level Engineer", "Looking for a mid-level developer.", "Mid-level"),
    ("Software Engineering Intern", "Join us for a summer internship program.", "Junior"),
]
seniority_ok = True
for title, description, expected in seniority_checks:
    got = detect_seniority_level(title, description)
    ok = got == expected
    seniority_ok &= ok
    print(f"  title={title!r:28} expected={expected!r:15} got={got!r:15} {'OK' if ok else 'FAIL'}")
print("ALL SENIORITY CHECKS PASSED" if seniority_ok else "SOME SENIORITY CHECKS FAILED")
print()

# --- Case 8: required-years extraction and experience scoring ---
print("=== Case 8: required-years extraction ===")
years_checks = [
    ("6+ years of dedicated experience in Localization Engineering.", 6),
    ("5-7 years of experience required.", 5),
    ("At least 3 years of experience.", 3),
    ("We'd love someone with 4 years of experience in data.", 4),
    ("No years requirement mentioned here at all.", None),
]
years_ok = True
for text, expected in years_checks:
    got = extract_required_years_experience(text)
    ok = got == expected
    years_ok &= ok
    print(f"  text={text!r:65} expected={expected} got={got} {'OK' if ok else 'FAIL'}")
print("ALL YEARS CHECKS PASSED" if years_ok else "SOME YEARS CHECKS FAILED")
