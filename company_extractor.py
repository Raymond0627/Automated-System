import json
import re
from pathlib import Path
from typing import Optional, List

try:
    from rapidfuzz import fuzz
    from rapidfuzz.process import extractOne
except ImportError:
    fuzz = None
    extractOne = None

TITLE_LINE_RE = re.compile(
    r'(president|chairman|chief\s+executive\s+officer|ceo|managing\s+director)',
    re.IGNORECASE
)
ADDRESS_LINE_RE = re.compile(
    r'(\d{1,4}\s|floor|bldg|building|tower|center|centre|drive)',
    re.IGNORECASE
)
SUBJECT_RE = re.compile(r'\bSUBJECT\s*:', re.IGNORECASE)
ADDRESSEE_RE = re.compile(
    r'addressee\s*:\s*(.+)',
    re.IGNORECASE
)
ADDRESSEE_ADDRESS_RE = re.compile(
    r'\s+\d{1,4}\s*[A-Z]?\b.*$|'
    r'\s+(?:floor|flr?|ave(nue)?|st(reet)?\.?|bldg|building|tower|center|centre|road|drive|house|unit|suite|room|rm)\b.*$',
    re.IGNORECASE
)

KNOWN_COMPANIES_FILE = Path(__file__).parent / "known_companies.json"

COMPANY_SUFFIX_RE = re.compile(
    r'\b(\w*(?:INSURANCE|INS|CO|LTD|COMPANY|CORP|CORPORATION|ASSURANCE|POLICY))\b',
    re.IGNORECASE
)

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


def extract_company_name_from_header(page_text: str) -> Optional[str]:
    title_match = TITLE_LINE_RE.search(page_text)
    if not title_match:
        return None

    search_start = title_match.end()

    subject_match = SUBJECT_RE.search(page_text, search_start)
    address_match = ADDRESS_LINE_RE.search(page_text, search_start)

    end_positions = [search_start + 200]
    if subject_match:
        end_positions.append(subject_match.start())
    if address_match:
        end_positions.append(address_match.start())

    search_end = min(end_positions)
    if search_end <= search_start:
        return None

    candidate = page_text[search_start:search_end]
    candidate = re.sub(r'[.,:;\-|]+', ' ', candidate).strip()
    candidate = re.sub(r'\s+', ' ', candidate).strip()
    candidate = TITLE_PREFIX_RE.sub('', candidate).strip()

    if len(candidate) < 4:
        return None

    return candidate


def extract_company_name_from_addressee(page_text: str) -> Optional[str]:
    match = ADDRESSEE_RE.search(page_text)
    if not match:
        return None

    candidate = match.group(1).strip()
    candidate = re.sub(r'[.,:;\-|]+', ' ', candidate).strip()
    candidate = re.sub(r'\s+', ' ', candidate).strip()
    candidate = TITLE_PREFIX_RE.sub('', candidate).strip()

    address_match = ADDRESSEE_ADDRESS_RE.search(candidate)
    if address_match:
        candidate = candidate[:address_match.start()].strip()

    if len(candidate) < 4:
        return None

    return candidate


def extract_company_name_from_keywords(page_text: str, known_companies: List[str]) -> Optional[dict]:
    if not known_companies or fuzz is None:
        return None

    for match in COMPANY_SUFFIX_RE.finditer(page_text):
        suffix_word = match.group(1)
        match_pos = match.start()

        words_before = page_text[:match_pos].split()
        words_after = page_text[match_pos + len(suffix_word):].split()

        before = words_before[-10:] if len(words_before) > 10 else words_before
        after = words_after[:5] if len(words_after) > 5 else words_after

        window = ' '.join(before + [suffix_word] + after)
        window = re.sub(r'[.,:;\-|]+', ' ', window).strip()
        window = re.sub(r'\s+', ' ', window).strip()

        if len(window) < 4:
            continue

        normalized = normalize_company_name(window)
        corrected = correct_company_name(normalized, known_companies)

        if corrected != normalized:
            return {
                'company_name': corrected,
                'source': 'keyword',
                'fuzzy_match_score': None,
                'needs_review': False,
            }

        score_match = extractOne(normalized, known_companies, scorer=fuzz.token_set_ratio)
        score = int(score_match[1]) if score_match else None
        if score is not None and score >= 80:
            return {
                'company_name': score_match[0],
                'source': 'keyword',
                'fuzzy_match_score': score,
                'needs_review': False,
            }

    return None


def normalize_company_name(raw: str) -> str:
    result = raw.upper()

    for pattern, replacement in ABBREVIATIONS:
        result = pattern.sub(replacement, result)

    result = re.sub(r'[^A-Z0-9 ]', ' ', result)
    result = re.sub(r'\s+', ' ', result).strip()

    return result


def correct_company_name(normalized: str, known_companies: List[str], threshold: int = 80) -> str:
    if not known_companies or fuzz is None:
        return normalized

    for company in known_companies:
        if company.startswith(normalized) and len(normalized) >= len(company) * 0.4:
            return company

    words = normalized.split()
    if len(words) < 2:
        return normalized

    match = extractOne(normalized, known_companies, scorer=fuzz.token_set_ratio)
    if match and match[1] >= threshold:
        return match[0]

    return normalized


def load_known_companies() -> List[str]:
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
    with open(KNOWN_COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)


def learn_company_name(company_name: str) -> None:
    if not company_name:
        return
    companies = load_known_companies()
    if company_name not in companies:
        companies.append(company_name)
        save_known_companies(companies)


def get_company_name_for_filename(page_ocr_data_list: list, fallback_folder_name: str) -> dict:
    known_companies = load_known_companies()

    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue

        result = extract_company_name_from_header(page_text)
        if result:
            normalized = normalize_company_name(result)
            if known_companies and fuzz is not None:
                corrected = correct_company_name(normalized, known_companies)
                score_match = extractOne(normalized, known_companies, scorer=fuzz.token_set_ratio)
                score = int(score_match[1]) if score_match else None
                if corrected != normalized:
                    return {
                        'company_name': corrected,
                        'source': 'header',
                        'fuzzy_match_score': score,
                        'needs_review': False,
                    }
                return {
                    'company_name': normalized,
                    'source': 'header',
                    'fuzzy_match_score': score,
                    'needs_review': score is not None and score < 80,
                }
            return {
                'company_name': normalized,
                'source': 'header',
                'fuzzy_match_score': None,
                'needs_review': False,
            }

    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue

        result = extract_company_name_from_addressee(page_text)
        if result:
            normalized = normalize_company_name(result)
            if known_companies and fuzz is not None:
                corrected = correct_company_name(normalized, known_companies)
                score_match = extractOne(normalized, known_companies, scorer=fuzz.token_set_ratio)
                score = int(score_match[1]) if score_match else None
                if corrected != normalized:
                    return {
                        'company_name': corrected,
                        'source': 'addressee',
                        'fuzzy_match_score': score,
                        'needs_review': False,
                    }
                return {
                    'company_name': normalized,
                    'source': 'addressee',
                    'fuzzy_match_score': score,
                    'needs_review': score is not None and score < 80,
                }
            return {
                'company_name': normalized,
                'source': 'addressee',
                'fuzzy_match_score': None,
                'needs_review': False,
            }

    for entry in page_ocr_data_list:
        page_text = entry.get('page_text', '')
        if not page_text:
            continue

        keyword_result = extract_company_name_from_keywords(page_text, known_companies)
        if keyword_result:
            return keyword_result

    normalized_folder = normalize_company_name(fallback_folder_name)
    return {
        'company_name': normalized_folder,
        'source': 'folder_fallback',
        'fuzzy_match_score': None,
        'needs_review': False,
    }
