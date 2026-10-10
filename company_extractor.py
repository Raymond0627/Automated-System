import json
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from rapidfuzz import fuzz
    from rapidfuzz.process import extractOne
except ImportError:
    fuzz = None
    extractOne = None

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent))
from paths import DATA_DIR, RESOURCE_DIR, ensure_data_dir

# Writable per-user roster location (so the installed app can persist changes).
KNOWN_COMPANIES_FILE = DATA_DIR / "known_companies.json"

# --- Tier 0/0b/1: Caption extraction ---
VERSUS_RE = re.compile(
    r'-\s*versus\s*-|versus|vs\.?\s|vs\s*\.\s',
    re.IGNORECASE
)
RESPONDENT_RE = re.compile(
    r'\b(?:respondents?|responder)\b',
    re.IGNORECASE
)
COMPLAINANT_RE = re.compile(
    r'\bcomplainants?\b',
    re.IGNORECASE
)

# --- Tier 1: Routing slip subject field ---
SUBJECT_FIELD_RE = re.compile(
    r'subject\s*:\s*(.+?)(?:\n|$)',
    re.IGNORECASE
)

# --- Tier 2/2b: Corporate/insurance keywords ---
STRONG_KEYWORDS_RE = re.compile(
    r'\b(?:INSURANCE|INS|CORPORATION|CORP|INCORPORATED|INC|COMPANY|CO|'
    r'ASSURANCE|REINSURANCE|UNDERWRITERS|MUTUAL|'
    r'PLANS|PRE-?NEED|MEMORIAL|HEALTH\s+CARE|HMO)\b',
    re.IGNORECASE
)
WEAK_KEYWORDS_RE = re.compile(
    r'\b(?:ENTERPRISES|GROUP|HOLDINGS|ASSOCIATION|FOUNDATION|'
    r'HOSPITAL|MEDICAL)\b',
    re.IGNORECASE
)
ALL_KEYWORDS_RE = re.compile(
    r'\b(?:INSURANCE|INS|CORPORATION|CORP|INCORPORATED|INC|COMPANY|CO|'
    r'ASSURANCE|REINSURANCE|UNDERWRITERS|MUTUAL|'
    r'PLANS|PRE-?NEED|MEMORIAL|HEALTH\s+CARE|HMO|'
    r'ENTERPRISES|GROUP|HOLDINGS|ASSOCIATION|FOUNDATION|'
    r'HOSPITAL|MEDICAL)\b',
    re.IGNORECASE
)

# --- Tier 3: Header CEO/President (LAST RESORT) ---
TITLE_LINE_RE = re.compile(
    r'(?:president|chairman|chief\s+executive\s+officer|ceo|managing\s+director)',
    re.IGNORECASE
)

# --- Officer name stripping ---
OFFICER_RE = re.compile(
    r',?\s+(?:represented\s+by\s+)?(?:its|his|her|their)\s+'
    r'(?:president|chairman|chief\s+executive\s+officer|ceo|managing\s+director|'
    r'general\s+manager|manager|officer|secretary|treasurer)',
    re.IGNORECASE
)

# --- Legacy patterns (kept for backward compat) ---
TITLE_PREFIX_RE = re.compile(
    r'^(?:AND\s+(?:CHIEF\s+EXECUTIVE\s+OFFICER|CEO|PRESIDENT|CHAIRMAN|MANAGING\s+DIRECTOR)\s+|'
    r'CEO\s+|CHIEF\s+EXECUTIVE\s+OFFICER\s+|PRESIDENT\s+|CHAIRMAN\s+|MANAGING\s+DIRECTOR\s+)',
    re.IGNORECASE
)

ABBREVIATIONS = [
    (re.compile(r'\bINCORPORATED\b', re.IGNORECASE), 'INC'),
    (re.compile(r'\bCORPORATION\b', re.IGNORECASE), 'CORP'),
    (re.compile(r'\bINSURANCE\b', re.IGNORECASE), 'INS'),
    (re.compile(r'\bPHILIPPINES\b', re.IGNORECASE), 'PHILS'),
    (re.compile(r'\bCOMPANY\b', re.IGNORECASE), 'CO'),
]


# ─────────────────────────────────────────────────────────────
# Utility: normalize company name
# ─────────────────────────────────────────────────────────────

def normalize_company_name(raw: str) -> str:
    result = raw.upper()
    for pattern, replacement in ABBREVIATIONS:
        result = pattern.sub(replacement, result)
    result = re.sub(r'[^A-Z0-9 ]', ' ', result)
    result = re.sub(r'\s+', ' ', result).strip()
    return result


# ─────────────────────────────────────────────────────────────
# Utility: fuzzy match a candidate against roster
# ─────────────────────────────────────────────────────────────

def _fuzzy_best(candidate: str, roster: List[str]) -> tuple:
    """Return (matched_entry, score) or (None, None)."""
    if not roster or fuzz is None:
        return None, None
    match = extractOne(candidate, roster, scorer=fuzz.token_set_ratio)
    if match:
        return match[0], int(match[1])
    return None, None


def _fuzzy_above(candidate: str, roster: List[str], min_score: int) -> bool:
    """Return True if best match score >= min_score."""
    _, score = _fuzzy_best(candidate, roster)
    return score is not None and score >= min_score


def _fuzzy_margin(candidate: str, roster: List[str], min_score: int, min_margin: int) -> Optional[str]:
    """
    Return matched entry ONLY if score >= min_score AND score is >= min_margin
    above the second-best match. Returns None otherwise.
    """
    if not roster or fuzz is None:
        return None

    best_match = extractOne(candidate, roster, scorer=fuzz.token_set_ratio)
    if not best_match:
        return None

    best_entry, best_score = best_match[0], int(best_match[1])
    if best_score < min_score:
        return None

    # Find second-best by excluding the best entry
    remaining = [c for c in roster if c != best_entry]
    if remaining:
        second_match = extractOne(candidate, remaining, scorer=fuzz.token_set_ratio)
        second_score = int(second_match[1]) if second_match else 0
        if best_score - second_score < min_margin:
            return None
    return best_entry


# ─────────────────────────────────────────────────────────────
# Tier 0: Caption regex (PRIMARY)
# ─────────────────────────────────────────────────────────────

def extract_from_caption(page_text: str) -> Optional[Dict[str, Any]]:
    """
    Find '-versus-'/'vs.' -> capture RESPONDENT company name.

    Handles IC formats:
    (A) "Complainant, vs. COMPANY, Respondent"
    (B) "REPUBLIC -versus- RESPONDENT, COMPANY"
    Skips routing slip noise (text before actual caption).
    """
    comp_match = COMPLAINANT_RE.search(page_text)
    resp_match = RESPONDENT_RE.search(page_text)

    if comp_match and resp_match:
        # Format A: extract between Complainant and Respondent
        between = page_text[comp_match.end():resp_match.start()]
        versus_match = VERSUS_RE.search(between)
        if versus_match:
            after_versus = between[versus_match.end():].strip()
            # Join lines (OCR splits company names across lines), then split on commas/periods
            after_versus_joined = re.sub(r'\s*\n\s*', ' ', after_versus)
            for chunk in re.split(r'[,.]', after_versus_joined):
                chunk = chunk.strip()
                chunk = re.sub(r'^[.,:;\-|/]+', '', chunk).strip()
                chunk = re.sub(r'[.,:;\-|/]+$', '', chunk).strip()
                if not chunk:
                    continue
                # Skip case numbers, dates, and short noise
                if re.match(r'^[\d\s\-/().]+$', chunk):
                    continue
                if re.match(r'^I\.?C\.?\s*\(CAD\)', chunk, re.IGNORECASE):
                    continue
                if re.match(r'^CAD\s+CASE', chunk, re.IGNORECASE):
                    continue
                if len(chunk.split()) >= 2:
                    normalized = normalize_company_name(chunk)
                    has_strong = bool(STRONG_KEYWORDS_RE.search(normalized))
                    if has_strong or len(chunk.split()) >= 3:
                        return {
                            'company_name': normalized,
                            'tier': 'caption',
                            'confidence': 'high',
                        }

    # Format B: find versus anywhere (fallback)
    versus_match = VERSUS_RE.search(page_text)
    if not versus_match:
        return None

    after_versus = page_text[versus_match.end():].strip()

    respondent_match = RESPONDENT_RE.search(after_versus)

    if respondent_match:
        after_respondent = after_versus[respondent_match.end():].strip()
        raw_company = re.split(r'[.]', after_respondent)[0].strip()
        raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()
        raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()
    else:
        raw_company = re.split(r'[.]', after_versus)[0].strip()
        raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()
        raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()

    words = raw_company.split()
    if len(words) < 2:
        return None

    normalized = normalize_company_name(raw_company)
    has_strong = bool(STRONG_KEYWORDS_RE.search(normalized))

    if not has_strong and len(words) < 3:
        return None

    return {
        'company_name': normalized,
        'tier': 'caption',
        'confidence': 'high',
    }


# ─────────────────────────────────────────────────────────────
# Tier 0b: Widened caption capture
# ─────────────────────────────────────────────────────────────

def extract_from_caption_wide(page_text: str) -> Optional[Dict[str, Any]]:
    """
    Fallback caption strategies:
    (a) Capture text between first and second "Respondent" occurrence
    (b) Capture 4-6 words immediately before "Respondent" regardless of '-versus-'
    """
    respondent_matches = list(RESPONDENT_RE.finditer(page_text))

    # Strategy (a): first to second Respondent
    if len(respondent_matches) >= 2:
        first_resp_end = respondent_matches[0].end()
        second_resp_start = respondent_matches[1].start()
        raw_company = page_text[first_resp_end:second_resp_start].strip()
        raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()
        raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()

        words = raw_company.split()
        if len(words) >= 2:
            normalized = normalize_company_name(raw_company)
            if STRONG_KEYWORDS_RE.search(normalized):
                return {
                    'company_name': normalized,
                    'tier': 'caption_wide',
                    'confidence': 'high',
                }

    # Strategy (b): 4-6 words before Respondent
    if respondent_matches:
        resp_start = respondent_matches[0].start()
        before_text = page_text[:resp_start].strip()
        words_before = before_text.split()

        for window_size in [6, 5, 4]:
            if len(words_before) >= window_size:
                candidate_words = words_before[-window_size:]
                raw_company = ' '.join(candidate_words)
                raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()
                raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()

                normalized = normalize_company_name(raw_company)
                if STRONG_KEYWORDS_RE.search(normalized):
                    return {
                        'company_name': normalized,
                        'tier': 'caption_wide',
                        'confidence': 'high',
                    }

    return None


# ─────────────────────────────────────────────────────────────
# Tier 0c: Complainant/Respondent extraction (IC format)
# ─────────────────────────────────────────────────────────────

def extract_from_complainant_respondent(page_text: str) -> Optional[Dict[str, Any]]:
    """
    Extract RESPONDENT company name from IC document format:
    'Complainant, vs. COMPANY NAME, Respondent'
    Captures text AFTER 'vs.' and BEFORE 'Respondent' (the respondent entity).
    """
    comp_match = COMPLAINANT_RE.search(page_text)
    resp_match = RESPONDENT_RE.search(page_text)
    if not comp_match or not resp_match:
        return None

    between = page_text[comp_match.end():resp_match.start()]

    versus_match = VERSUS_RE.search(between)
    if not versus_match:
        return None

    after_versus = between[versus_match.end():].strip()
    after_versus_joined = re.sub(r'\s*\n\s*', ' ', after_versus)
    for chunk in re.split(r'[,.]', after_versus_joined):
        chunk = chunk.strip()
        chunk = re.sub(r'^[.,:;\-|/]+', '', chunk).strip()
        chunk = re.sub(r'[.,:;\-|/]+$', '', chunk).strip()
        if not chunk:
            continue
        if re.match(r'^[\d\s\-/().]+$', chunk):
            continue
        if re.match(r'^I\.?C\.?\s*\(CAD\)', chunk, re.IGNORECASE):
            continue
        if re.match(r'^CAD\s+CASE', chunk, re.IGNORECASE):
            continue
        if len(chunk.split()) >= 2:
            normalized = normalize_company_name(chunk)
            has_strong = bool(STRONG_KEYWORDS_RE.search(normalized))
            if has_strong or len(chunk.split()) >= 3:
                return {
                    'company_name': normalized,
                    'tier': 'complainant_respondent',
                    'confidence': 'high',
                }


# ─────────────────────────────────────────────────────────────
# Tier 1: Routing slip subject field
# ─────────────────────────────────────────────────────────────

def extract_from_routing_slip(page_text: str) -> Optional[Dict[str, Any]]:
    """
    Find 'Subject:' field, apply same caption regex to extracted text.
    """
    subject_match = SUBJECT_FIELD_RE.search(page_text)
    if not subject_match:
        return None

    subject_text = subject_match.group(1).strip()

    # Try caption regex within subject text
    versus_match = VERSUS_RE.search(subject_text)
    if versus_match:
        after_versus = subject_text[versus_match.end():].strip()
        respondent_match = RESPONDENT_RE.search(after_versus)

        if respondent_match:
            # Format B: capture AFTER "Respondent" up to next period
            after_respondent = after_versus[respondent_match.end():].strip()
            raw_company = re.split(r'[.]', after_respondent)[0].strip()
        else:
            # Format A: capture after vs. up to next period
            raw_company = re.split(r'[.]', after_versus)[0].strip()

        raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()
        raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()

        words = raw_company.split()
        if len(words) >= 2:
            normalized = normalize_company_name(raw_company)
            has_strong = bool(STRONG_KEYWORDS_RE.search(normalized))
            if has_strong or len(words) >= 3:
                return {
                    'company_name': normalized,
                    'tier': 'routing_slip',
                    'confidence': 'high',
                }

    # Fallback: text after "Subject:" up to next period or comma
    raw_company = re.split(r'[.,]', subject_text)[0].strip()
    words = raw_company.split()
    if len(words) >= 2:
        normalized = normalize_company_name(raw_company)
        if STRONG_KEYWORDS_RE.search(normalized):
            return {
                'company_name': normalized,
                'tier': 'routing_slip',
                'confidence': 'high',
            }

    return None


# ─────────────────────────────────────────────────────────────
# Tier 2: Caption keywords (caption span only)
# ─────────────────────────────────────────────────────────────

def extract_from_caption_keywords(page_text: str) -> Optional[Dict[str, Any]]:
    """
    Scan only within caption region (text from start to first "Respondent")
    for corporate keywords.
    """
    respondent_match = RESPONDENT_RE.search(page_text)
    if not respondent_match:
        return None

    caption_span = page_text[:respondent_match.start()]

    # Words to exclude from the noun phrase window
    EXCLUDE_WORDS = re.compile(
        r'^(?:versus|vs|v|the|of|and|for|by|its|his|her|their|a|an|in|on|at|to|from)$',
        re.IGNORECASE
    )

    for kw_match in ALL_KEYWORDS_RE.finditer(caption_span):
        match_word = kw_match.group(0)
        match_pos = kw_match.start()

        words_before = caption_span[:match_pos].split()
        words_after = caption_span[match_pos + len(match_word):].split()

        before = words_before[-4:] if len(words_before) > 4 else words_before
        after = words_after[:3] if len(words_after) > 3 else words_after

        # Filter out excluded words
        before = [w for w in before if not EXCLUDE_WORDS.match(w)]
        after = [w for w in after if not EXCLUDE_WORDS.match(w)]

        window = ' '.join(before + [match_word] + after)
        window = re.sub(r'[.,:;\-|/]+', ' ', window).strip()
        window = re.sub(r'\s+', ' ', window).strip()

        if len(window) < 4:
            continue

        normalized = normalize_company_name(window)

        if STRONG_KEYWORDS_RE.search(normalized):
            return {
                'company_name': normalized,
                'tier': 'caption_keyword',
                'confidence': 'medium',
            }

    return None


# ─────────────────────────────────────────────────────────────
# Tier 2b: Full-document keyword scan (wide net, strict)
# ─────────────────────────────────────────────────────────────

def extract_from_full_keywords(page_text: str, known_companies: List[str]) -> Optional[Dict[str, Any]]:
    """
    Scan entire document for keywords. Wide net, strict matching.
    Only accepts if score >= 90 AND score >= 10 points above second-best.
    """
    if not known_companies or fuzz is None:
        return None

    candidates = []

    for kw_match in ALL_KEYWORDS_RE.finditer(page_text):
        match_word = kw_match.group(0)
        match_pos = kw_match.start()

        words_before = page_text[:match_pos].split()
        words_after = page_text[match_pos + len(match_word):].split()

        before = words_before[-6:] if len(words_before) > 6 else words_before
        after = words_after[:4] if len(words_after) > 4 else words_after

        window = ' '.join(before + [match_word] + after)
        window = re.sub(r'[.,:;\-|/]+', ' ', window).strip()
        window = re.sub(r'\s+', ' ', window).strip()

        if len(window) < 4:
            continue

        normalized = normalize_company_name(window)
        candidates.append(normalized)

    for candidate in candidates:
        matched = _fuzzy_margin(candidate, known_companies, min_score=90, min_margin=10)
        if matched:
            return {
                'company_name': matched,
                'tier': 'full_keyword',
                'confidence': 'medium',
            }

    return None


# ─────────────────────────────────────────────────────────────
# Tier 3: Header CEO/President (LAST RESORT, must be corroborated)
# ─────────────────────────────────────────────────────────────

def extract_from_header_corroborated(page_text: str) -> Optional[Dict[str, Any]]:
    """
    LAST RESORT: Find CEO/President, but ONLY accept if also near "Respondent".
    Requires at least one strong keyword in the result.
    """
    title_match = TITLE_LINE_RE.search(page_text)
    if not title_match:
        return None

    # Check if "Respondent" appears within 15 words of the CEO mention
    title_pos = title_match.start()

    after_title_text = page_text[title_pos:title_pos + 500]
    resp_in_range = RESPONDENT_RE.search(after_title_text)
    if not resp_in_range:
        return None

    words_between = after_title_text[:resp_in_range.start()].split()
    if len(words_between) > 15:
        return None

    # Skip the title word itself (e.g., "president") and common filler
    FILLER = re.compile(
        r'^(?:president|chairman|ceo|chief|executive|officer|managing|director|'
        r'of|the|for|by|its|his|her|their|a|an|in|on|at|to|from|'
        r'as|and|or|represented|managing|general|manager|'
        r'but|no|not|nor|yet|also|too|very|just|only|now|here|there|then|than)$',
        re.IGNORECASE
    )
    company_words = [w for w in words_between if not FILLER.match(w)]

    if len(company_words) < 2:
        return None

    raw_company = ' '.join(company_words)
    raw_company = re.sub(r'[.,:;\-|/]+$', '', raw_company).strip()
    raw_company = re.sub(r'^[.,:;\-|/]+', '', raw_company).strip()

    normalized = normalize_company_name(raw_company)

    if not STRONG_KEYWORDS_RE.search(normalized):
        return None

    return {
        'company_name': normalized,
        'tier': 'header_corroborated',
        'confidence': 'low',
    }


# ─────────────────────────────────────────────────────────────
# Fuzzy matching with tier-adjusted thresholds
# ─────────────────────────────────────────────────────────────

def fuzzy_match_with_tier(
    candidates: List[str],
    known_companies: List[str],
    tier: str,
    cross_validated: bool = False,
) -> Dict[str, Any]:
    """
    Match candidates against roster with tier-adjusted thresholds.

    Returns dict with:
        company_name, matched_roster_entry, fuzzy_match_score,
        tier_used, cross_validated, confidence_label, needs_review
    """
    if not candidates:
        return {
            'company_name': '',
            'matched_roster_entry': None,
            'fuzzy_match_score': None,
            'company_confidence': 0,
            'tier_used': tier,
            'cross_validated': cross_validated,
            'confidence_label': 'none',
            'needs_review': True,
        }

    if not known_companies or fuzz is None:
        return {
            'company_name': candidates[0],
            'matched_roster_entry': None,
            'fuzzy_match_score': None,
            'company_confidence': 0,
            'tier_used': tier,
            'cross_validated': cross_validated,
            'confidence_label': 'none',
            'needs_review': True,
        }

    # Determine threshold based on tier and cross-validation
    HIGH_CONFIDENCE_TIERS = ('caption', 'caption_wide', 'routing_slip', 'complainant_respondent')
    MEDIUM_CONFIDENCE_TIERS = ('caption_keyword', 'full_keyword')

    if cross_validated:
        threshold = 70
    elif tier in HIGH_CONFIDENCE_TIERS:
        threshold = 80
    elif tier in MEDIUM_CONFIDENCE_TIERS:
        threshold = 90
    elif tier == 'header_corroborated':
        threshold = 85
    else:
        # 'unresolved' or any unknown tier: NEVER auto-confirm
        threshold = 999

    best_entry = None
    best_score = None
    best_candidate = candidates[0]

    for candidate in candidates:
        entry, score = _fuzzy_best(candidate, known_companies)
        if entry and (best_score is None or score > best_score):
            best_entry = entry
            best_score = score
            best_candidate = candidate

    # For full_keyword tier, also require margin over second-best
    if tier == 'full_keyword' and best_entry:
        margin_check = _fuzzy_margin(best_candidate, known_companies, min_score=90, min_margin=10)
        if not margin_check:
            best_entry = None
            best_score = None

    if best_entry and best_score is not None and best_score >= threshold:
        # Compute company_confidence: fuzzy score boosted by tier quality
        tier_boost = 10 if tier in HIGH_CONFIDENCE_TIERS else 0
        company_confidence = min(100, best_score + tier_boost)
        return {
            'company_name': best_entry,
            'matched_roster_entry': best_entry,
            'fuzzy_match_score': best_score,
            'company_confidence': company_confidence,
            'tier_used': tier,
            'cross_validated': cross_validated,
            'confidence_label': 'high' if cross_validated or tier in HIGH_CONFIDENCE_TIERS else 'medium',
            'needs_review': False,
        }

    # Below threshold: check if any candidate is close enough to flag
    if best_entry and best_score is not None:
        return {
            'company_name': best_entry,
            'matched_roster_entry': best_entry,
            'fuzzy_match_score': best_score,
            'company_confidence': best_score,
            'tier_used': tier,
            'cross_validated': cross_validated,
            'confidence_label': 'low',
            'needs_review': True,
        }

    return {
        'company_name': best_candidate,
        'matched_roster_entry': None,
        'fuzzy_match_score': None,
        'company_confidence': 0,
        'tier_used': tier,
        'cross_validated': cross_validated,
        'confidence_label': 'none',
        'needs_review': True,
    }


# ─────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────

def get_company_name_for_filename(page_ocr_data_list: list, context: dict = None) -> dict:
    """Resolve the company name for a document.

    Delegates to the multi-source resolver (``company_resolver``), which
    collects candidates from several detectors (caption, routing slip,
    letterhead, signature, roster gazetteer, keyword window, folder hint,
    learned hints), validates them against the roster, and decides by
    corroboration. Falls back to the legacy tier ladder if the resolver is
    unavailable.
    """
    try:
        from company_resolver import resolve_company
        return resolve_company(page_ocr_data_list, context=context)
    except Exception:
        return _legacy_get_company_name_for_filename(page_ocr_data_list)


def _legacy_get_company_name_for_filename(page_ocr_data_list: list) -> dict:
    """
    Extract company name using a 6-tier priority system.

    Tiers:
    0. Caption regex (-versus- -> Respondent)
    0b. Widened caption (2nd Respondent / 4-6 words before Respondent)
    1. Routing slip subject field
    2. Caption keywords (caption span only)
    2b. Full-document keywords (wide net, strict)
    3. Header CEO/President (must be corroborated by "Respondent")

    There is no folder-name fallback: the source folder name is never treated as
    a company value. When every tier fails the result is an empty company_name
    with company_confidence 0, which fails the auto-confirm gate and routes the
    document to manual review.

    Cross-validation: If Tier 0 and Tier 1 both match and agree (fuzzy >= 70),
    confidence is elevated.
    """
    known_companies = load_known_companies()

    tier0_result = None
    tier1_result = None

    # Tier 0: Caption regex (PRIMARY)
    # Search ALL pages — prefer results from pages with Complainant/Respondent markers
    # AND "Republic of the Philippines" header (actual case caption, not routing slip)
    COMPLAINANT_RE_LOCAL = re.compile(r'\bcomplainants?\b', re.IGNORECASE)
    RESPONDENT_RE_LOCAL = re.compile(r'\b(?:respondents?|responder)\b', re.IGNORECASE)
    CASE_HEADER_RE = re.compile(r'republic\s+of\s+the\s+philippines', re.IGNORECASE)

    best_tier0 = None
    best_score = 0  # 0=none, 1=strong, 2=markers, 4=case_header
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        has_markers = bool(COMPLAINANT_RE_LOCAL.search(page_text) and RESPONDENT_RE_LOCAL.search(page_text))
        has_header = bool(CASE_HEADER_RE.search(page_text))
        result = extract_from_caption(page_text)
        if result:
            has_strong = bool(STRONG_KEYWORDS_RE.search(result['company_name']))
            # Score: case_header + markers + strong = highest
            score = (4 if has_header else 0) + (2 if has_markers else 0) + (1 if has_strong else 0)
            if score > best_score:
                best_tier0 = result
                best_score = score
    tier0_result = best_tier0

    # Tier 1: Routing slip subject field
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier1_result = extract_from_routing_slip(page_text)
        if tier1_result:
            break

    # Cross-validation check
    cross_validated = False
    if tier0_result and tier1_result:
        score = fuzz.token_set_ratio(
            tier0_result['company_name'],
            tier1_result['company_name']
        ) if fuzz else 0
        if score >= 70:
            cross_validated = True

    # Use Tier 0 if available (with or without cross-validation)
    if tier0_result:
        return fuzzy_match_with_tier(
            [tier0_result['company_name']],
            known_companies,
            'caption',
            cross_validated=cross_validated,
        )

    # Use Tier 1 if Tier 0 failed
    if tier1_result:
        return fuzzy_match_with_tier(
            [tier1_result['company_name']],
            known_companies,
            'routing_slip',
            cross_validated=cross_validated,
        )

    # Tier 0b: Widened caption
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier0b_result = extract_from_caption_wide(page_text)
        if tier0b_result:
            return fuzzy_match_with_tier(
                [tier0b_result['company_name']],
                known_companies,
                'caption_wide',
            )

    # Tier 0c: Complainant/Respondent extraction
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier0c_result = extract_from_complainant_respondent(page_text)
        if tier0c_result:
            return fuzzy_match_with_tier(
                [tier0c_result['company_name']],
                known_companies,
                'complainant_respondent',
            )

    # Tier 2: Caption keywords (caption span only)
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier2_result = extract_from_caption_keywords(page_text)
        if tier2_result:
            return fuzzy_match_with_tier(
                [tier2_result['company_name']],
                known_companies,
                'caption_keyword',
            )

    # Tier 2b: Full-document keywords (wide net, strict)
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier2b_result = extract_from_full_keywords(page_text, known_companies)
        if tier2b_result:
            return fuzzy_match_with_tier(
                [tier2b_result['company_name']],
                known_companies,
                'full_keyword',
            )

    # Tier 3: Header CEO/President (LAST RESORT, must be corroborated)
    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue
        tier3_result = extract_from_header_corroborated(page_text)
        if tier3_result:
            return fuzzy_match_with_tier(
                [tier3_result['company_name']],
                known_companies,
                'header_corroborated',
            )

    # No tier produced a candidate. Return an empty name so the caller treats it
    # as unresolved and routes the document to manual review.
    return fuzzy_match_with_tier([], known_companies, 'unresolved')


# ─────────────────────────────────────────────────────────────
# Roster management (unchanged)
# ─────────────────────────────────────────────────────────────

def _seed_known_companies_if_needed() -> None:
    if KNOWN_COMPANIES_FILE.exists():
        return
    resource_file = RESOURCE_DIR / "known_companies.json"
    if resource_file.exists():
        try:
            ensure_data_dir()
            with open(resource_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                with open(KNOWN_COMPANIES_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def load_known_companies() -> List[str]:
    _seed_known_companies_if_needed()
    try:
        if KNOWN_COMPANIES_FILE.exists():
            with open(KNOWN_COMPANIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def save_known_companies(companies: List[str]) -> None:
    deduped = sorted(set(companies))
    ensure_data_dir()
    with open(KNOWN_COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)


def learn_company_name(company_name: str) -> None:
    if not company_name:
        return
    companies = load_known_companies()
    if company_name not in companies:
        companies.append(company_name)
        save_known_companies(companies)
