"""Detect language requirements stated in a job description.

Generalizes the original project's German-only detector into one that
scans for ANY configured language's stated proficiency requirement.
A job with no detected requirement genuinely has none - that's the
correct, common outcome outside German-speaking markets, arrived at
from what the text actually says rather than an assumption about the
country a job is in.
"""

import re
from typing import Dict, List, Tuple

# Level ordering, low to high - used to compare "required vs candidate has"
LEVEL_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2", "Native"]

# Each language's proficiency levels as they actually appear in job text.
# Starting scope: German and English only (see HUNTLY_ARCHITECTURE.md §7) -
# adding a language later means adding one more entry here, not a redesign.
LANGUAGE_PATTERNS: Dict[str, List[str]] = {
    "German": ["german", "deutsch"],
    "English": ["english"],
}

# A level mention further than this from its nearest language keyword is
# not considered related to it at all
MAX_ASSOCIATION_DISTANCE = 40


def _find_language_matches(text: str) -> List[Tuple[int, int, str]]:
    """Find every language keyword occurrence: (start, end, language)."""
    matches = []
    for language, keywords in LANGUAGE_PATTERNS.items():
        for keyword in keywords:
            # German compounds words directly onto the language name
            # ("Deutschkenntnisse") - only require a boundary before the
            # keyword, not after, so these compounds still match. Other
            # languages keep the stricter whole-word match.
            pattern = r"\b" + re.escape(keyword) if language == "German" else r"\b" + re.escape(keyword) + r"\b"
            for m in re.finditer(pattern, text, re.IGNORECASE):
                matches.append((m.start(), m.end(), language))
    return matches


def _find_level_matches(text: str) -> List[Tuple[int, int, str]]:
    """Find every proficiency-level mention: (start, end, level)."""
    matches = []
    for m in re.finditer(r"\b([ABC][12])\b", text, re.IGNORECASE):
        matches.append((m.start(), m.end(), m.group(1).upper()))
    for m in re.finditer(r"\b(native|fluent|muttersprache)\b", text, re.IGNORECASE):
        matches.append((m.start(), m.end(), "Native"))
    return matches


def _distance(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Character distance between two spans (0 if they overlap)."""
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0


def detect_language_requirements(description: str) -> List[Tuple[str, str]]:
    """Scan a job description for stated language requirements.

    Each detected proficiency level is assigned to whichever language
    keyword it's physically closest to in the text - not just any
    language within a loose window - so adjacent multi-language mentions
    ("Fluent German (C1) and English required") are handled correctly:
    the C1 is closer to "German" than to "English", so it's assigned
    only to German, and English is correctly left with no detected level.

    Args:
        description: the job's full description text

    Returns:
        List of (language, level) tuples. Empty list means no language
        requirement was detected at all - not "unknown," genuinely none
        found, which is the expected result for most English-primary-market
        postings.
    """
    if not description:
        return []

    language_matches = _find_language_matches(description)
    level_matches = _find_level_matches(description)

    if not language_matches or not level_matches:
        return []

    found: Dict[str, str] = {}
    for level_start, level_end, level in level_matches:
        # find the nearest language keyword to this level mention
        nearest_lang = None
        nearest_dist = None
        for lang_start, lang_end, language in language_matches:
            dist = _distance(level_start, level_end, lang_start, lang_end)
            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest_lang = language

        if nearest_lang and nearest_dist is not None and nearest_dist <= MAX_ASSOCIATION_DISTANCE:
            # first level found for a language wins - a job restating
            # the same requirement twice shouldn't overwrite it
            found.setdefault(nearest_lang, level)

    return list(found.items())


def meets_requirement(required_level: str, candidate_level: str) -> bool:
    """Check whether a candidate's stated proficiency meets a job's
    required level for the same language."""
    try:
        required_idx = LEVEL_ORDER.index(required_level)
        candidate_idx = LEVEL_ORDER.index(candidate_level)
    except ValueError:
        # Unrecognized level string - fail safe by treating as not met,
        # rather than silently passing an unverifiable requirement
        return False
    return candidate_idx >= required_idx
