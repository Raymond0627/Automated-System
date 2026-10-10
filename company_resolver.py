"""Multi-source company-name resolver.

Collects company candidates from independent detectors over a document's OCR
page text, validates each candidate against the known-company roster, and
decides by corroboration. Designed to handle layouts the caption/routing-slip
tiers alone miss (letterheads, business letters, invoices, policies, ...).

The roster is the source of truth: a candidate only survives if it fuzzy-matches
a roster entry, so detectors can afford to be permissive.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from rapidfuzz import fuzz
    from rapidfuzz.process import extractOne
except ImportError:  # pragma: no cover - rapidfuzz is a hard dependency in practice
    fuzz = None
    extractOne = None

import company_extractor as ce
from paths import DATA_DIR, ensure_data_dir


HINTS_FILE = DATA_DIR / "company_hints.json"

# Default confidence an entry needs before the rest of the app treats it as
# auto-confirmed. The pipeline reads the configurable value; this is the fallback.
DEFAULT_CONFIDENCE_THRESHOLD = 90

# Minimum fuzzy score for a candidate to be considered the same company as a
# roster entry at all (below this it is treated as "no roster match").
MIN_MATCH_SCORE = 65

# Closing markers that precede a signature block.
CLOSING_RE = re.compile(
    r'\b(?:very\s+truly\s+yours|yours\s+faithfully|yours\s+truly|yours\s+sincerely|'
    r'sincerely|respectfully|truly\s+yours|faithfully\s+yours)\b',
    re.IGNORECASE,
)

# Addressee / regulator text that must never be treated as the company.
ADDRESSEE_RE = re.compile(
    r'(?:office\s+of\s+the|the\s+insurance\s+commission|insurance\s+commission|'
    r'dear\s+sirs?|attention\s*:|to\s*:)',
    re.IGNORECASE,
)

REGULATOR_BLACKLIST = (
    "INSURANCE COMMISSION",
    "OFFICE OF THE INSURANCE COMMISSIONER",
    "INSURANCE COMMISSIONER",
)

# Per-source profile: (min fuzzy score to accept, confidence boost on accept).
SOURCE_PROFILE = {
    "learned": (60, 10),
    "caption": (80, 10),
    "routing_slip": (80, 10),
    "letterhead": (72, 6),
    "signature": (72, 6),
    "gazetteer": (68, 5),
    "keyword": (88, 0),
    "folder": (95, 0),
}

# Sources strong enough that two of them agreeing is decisive.
HIGH_SOURCES = {"caption", "routing_slip", "letterhead", "signature", "gazetteer", "learned"}


@dataclass
class CompanyCandidate:
    name: str
    source: str
    weight: float


# ─────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return ce.normalize_company_name(text or "")


def _norm_match(text: str) -> str:
    """Normalize for roster comparison WITHOUT abbreviation expansion.

    ``ce.normalize_company_name`` expands INSURANCE->INS, COMPANY->CO, etc.,
    but roster entries are stored unexpanded, so expanding one side collapses
    exact matches. Matching uses uppercase + punctuation strip only.
    """
    t = (text or "").upper()
    t = re.sub(r'[^A-Z0-9 ]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _tokens(text: str) -> List[str]:
    return _norm_match(text).split()


def _token_subsequence(needle: List[str], hay: List[str]) -> bool:
    it = iter(hay)
    return all(any(t == nt for t in it) for nt in needle)


def _is_blacklisted(normalized: str) -> bool:
    n = _norm_match(normalized)
    return any(bl in n for bl in (_norm_match(b) for b in REGULATOR_BLACKLIST))


def _best_roster_match(name: str, roster: List[str]):
    """Return (matched_entry, score) or (None, None)."""
    if not name or not roster or extractOne is None:
        return None, None
    match = extractOne(_norm_match(name), roster, scorer=fuzz.token_set_ratio)
    if not match:
        return None, None
    return match[0], int(match[1])


def _pages_text(page_texts: List[dict]) -> List[str]:
    out = []
    for entry in page_texts or []:
        txt = entry.get("page_text") if isinstance(entry, dict) else None
        if txt:
            out.append(txt)
    return out


def _looks_like_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 3:
        return False
    upper_ratio = sum(1 for c in line if c.isupper()) / max(1, len(line))
    # Mostly-uppercase lines, or short title-case name lines.
    return upper_ratio >= 0.7 or line.istitle()


def _candidate_from_run(run_lines: List[str], source: str, weight: float) -> Optional[CompanyCandidate]:
    name = " ".join(run_lines).strip()
    norm = _norm_match(name)
    if not norm or _is_blacklisted(name):
        return None
    if not ce.STRONG_KEYWORDS_RE.search(norm):
        return None
    return CompanyCandidate(name, source, weight)


# ─────────────────────────────────────────────────────────────
# Detectors
# ─────────────────────────────────────────────────────────────

def detect_case_caption(page_texts: List[dict]) -> List[CompanyCandidate]:
    out: List[CompanyCandidate] = []
    for text in _pages_text(page_texts):
        for fn in (ce.extract_from_caption, ce.extract_from_caption_wide,
                   ce.extract_from_complainant_respondent):
            try:
                res = fn(text)
            except Exception:
                res = None
            if res and res.get("company_name"):
                out.append(CompanyCandidate(res["company_name"], "caption", 0.9))
    return out


def detect_routing_slip(page_texts: List[dict]) -> List[CompanyCandidate]:
    out: List[CompanyCandidate] = []
    for text in _pages_text(page_texts):
        try:
            res = ce.extract_from_routing_slip(text)
        except Exception:
            res = None
        if res and res.get("company_name"):
            out.append(CompanyCandidate(res["company_name"], "routing_slip", 0.8))
    return out


def _head_lines(text: str) -> List[str]:
    """Non-empty lines from the top of a page, stopping at the addressee block."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    head: List[str] = []
    for ln in lines:
        if ADDRESSEE_RE.search(ln):
            break
        head.append(ln)
        if len(head) >= 15:
            break
    return head


def detect_letterhead(page_texts: List[dict]) -> List[CompanyCandidate]:
    """Company block near the top of a page (letterhead/masthead), stopping at
    the addressee so a body mention of another company is not mistaken for one."""
    out: List[CompanyCandidate] = []
    for text in _pages_text(page_texts):
        head = _head_lines(text)
        i = 0
        while i < len(head):
            if _looks_like_heading(head[i]):
                j = i
                while j < len(head) and j - i < 4 and _looks_like_heading(head[j]):
                    j += 1
                cand = _candidate_from_run(head[i:j], "letterhead", 0.85)
                if cand:
                    out.append(cand)
                i = j
            else:
                i += 1
    return out


def detect_signature_block(page_texts: List[dict]) -> List[CompanyCandidate]:
    """Company in the signature block following a letter closing.

    Only text AFTER the closing marker is considered: text above the closing is
    body prose and routinely names other companies.
    """
    out: List[CompanyCandidate] = []
    for text in _pages_text(page_texts):
        for m in CLOSING_RE.finditer(text):
            after = text[m.end(): m.end() + 500]
            after_lines = [ln.strip() for ln in after.splitlines() if ln.strip()][:8]
            for line in after_lines:
                norm = _norm_match(line)
                if norm and not _is_blacklisted(line) and ce.STRONG_KEYWORDS_RE.search(norm):
                    out.append(CompanyCandidate(line, "signature", 0.8))
    return out


def detect_roster_gazetteer(page_texts: List[dict], roster: List[str]) -> List[CompanyCandidate]:
    """Roster entries directly present in the text; sender region outranks body."""
    if not roster:
        return []
    pages = _pages_text(page_texts)
    full_hay = _tokens(" ".join(pages))
    # Sender region: top of every page + around every closing marker.
    sender_chunks: List[str] = []
    for text in pages:
        # Top-of-page letterhead only (stopping at the addressee block), plus the
        # signature area after each closing marker. Body prose is excluded.
        sender_chunks.append(" ".join(_head_lines(text)))
        for m in CLOSING_RE.finditer(text):
            sender_chunks.append(text[m.start(): m.start() + 400])
    sender_hay = _tokens(" ".join(sender_chunks))

    out: List[CompanyCandidate] = []
    for entry in roster:
        et = _tokens(entry)
        if not et or _is_blacklisted(entry):
            continue
        if _token_subsequence(et, sender_hay):
            out.append(CompanyCandidate(entry, "gazetteer", 0.9))
        elif _token_subsequence(et, full_hay):
            out.append(CompanyCandidate(entry, "gazetteer", 0.7))
    return out


def detect_keyword_window(page_texts: List[dict], roster: List[str]) -> List[CompanyCandidate]:
    out: List[CompanyCandidate] = []
    if not roster:
        return out
    for text in _pages_text(page_texts):
        try:
            res = ce.extract_from_full_keywords(text, roster)
        except Exception:
            res = None
        if res and res.get("company_name"):
            out.append(CompanyCandidate(res["company_name"], "keyword", 0.6))
    return out


def _folders_from_context(context: Optional[dict]) -> List[str]:
    if not context:
        return []
    names: List[str] = []
    rel = (context.get("rel_dir") or "").replace("\\", "/").strip("/")
    if rel:
        names.extend([p for p in rel.split("/") if p])
    path = context.get("original_path") or ""
    if path:
        p = Path(path)
        if p.parent.name:
            names.append(p.parent.name)
        if p.parent.parent.name:
            names.append(p.parent.parent.name)
    # de-dup, preserve order
    seen = set()
    out = []
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def detect_folder_hint(context: Optional[dict], roster: List[str]) -> List[CompanyCandidate]:
    """Weak corroborator: grouping folder name.

    Emits the folder name when it looks like a company (contains a corporate
    keyword), with low weight so it never auto-confirms alone. If it matches a
    roster entry, the roster spelling is used so it corroborates other sources.
    """
    out: List[CompanyCandidate] = []
    for folder in _folders_from_context(context):
        nm = _norm_match(folder)
        if not nm or not ce.STRONG_KEYWORDS_RE.search(nm) or _is_blacklisted(folder):
            continue
        entry, score = _best_roster_match(folder, roster) if roster else (None, None)
        name = entry if (entry and score is not None and score >= 95) else nm
        out.append(CompanyCandidate(name, "folder", 0.4))
    return out


def detect_learned_hint(context: Optional[dict], page_texts: List[dict], roster: List[str]) -> List[CompanyCandidate]:
    hints = load_hints()
    out: List[CompanyCandidate] = []
    folders = hints.get("folders", {})
    for folder in _folders_from_context(context):
        comp = folders.get(folder)
        if comp:
            out.append(CompanyCandidate(comp, "learned", 0.95))
    letterheads = hints.get("letterheads", {})
    if letterheads:
        for text in _pages_text(page_texts):
            lines = [ln.strip() for ln in text.splitlines()]
            head = [ln for ln in lines if ln][:15]
            for line in head:
                comp = letterheads.get(_norm(line))
                if comp:
                    out.append(CompanyCandidate(comp, "learned", 0.95))
    return out


# ─────────────────────────────────────────────────────────────
# Combiner
# ─────────────────────────────────────────────────────────────

def _empty_result(threshold: int = DEFAULT_CONFIDENCE_THRESHOLD) -> Dict[str, Any]:
    return {
        "company_name": "",
        "matched_roster_entry": None,
        "fuzzy_match_score": None,
        "company_confidence": 0,
        "tier_used": "unresolved",
        "cross_validated": False,
        "confidence_label": "none",
        "needs_review": True,
        "resolved_letterhead": "",
    }


def combine(candidates: List[CompanyCandidate], roster: List[str],
            threshold: int = DEFAULT_CONFIDENCE_THRESHOLD) -> Dict[str, Any]:
    """Resolve candidates to roster entries and decide by corroboration."""
    if not candidates or not roster:
        return _empty_result(threshold)

    grouped: Dict[str, Dict[str, Any]] = {}
    for cand in candidates:
        entry, score = _best_roster_match(cand.name, roster)
        if not entry or score is None or score < MIN_MATCH_SCORE:
            continue
        g = grouped.setdefault(entry, {"sources": set(), "max_conf": 0, "max_fuzzy": 0, "raw": {}})
        min_score, boost = SOURCE_PROFILE.get(cand.source, (95, 0))
        if score >= min_score:
            conf = min(100, score + boost)
            g["max_conf"] = max(g["max_conf"], conf)
        g["max_fuzzy"] = max(g["max_fuzzy"], score)
        g["sources"].add(cand.source)
        g["raw"].setdefault(cand.source, cand.name)

    if not grouped:
        return _suggest_raw(candidates, threshold)

    best_entry = None
    best = None
    for entry, g in grouped.items():
        key = (len(g["sources"]), g["max_conf"], g["max_fuzzy"])
        if best is None or key > (len(best["sources"]), best["max_conf"], best["max_fuzzy"]):
            best = g
            best_entry = entry

    confidence = best["max_conf"]
    strong_sources = {s for s in best["sources"] if s in HIGH_SOURCES}
    cross_validated = len(best["sources"]) >= 2
    if len(g["sources"]) >= 2 or len(best["sources"]) >= 2:
        # Two independent sources agreeing on the same entry is decisive.
        confidence = max(confidence, threshold)
    if len(best["sources"]) >= 2:
        confidence = max(confidence, DEFAULT_CONFIDENCE_THRESHOLD)

    confidence = int(min(100, round(confidence)))
    # A lone weak source must never auto-confirm.
    if len(grouped[best_entry]["sources"]) == 1 and next(iter(grouped[best_entry]["sources"])) not in HIGH_SOURCES:
        confidence = min(confidence, 89)

    return {
        "company_name": best_entry,
        "matched_roster_entry": best_entry,
        "fuzzy_match_score": grouped[best_entry]["max_fuzzy"] or None,
        "company_confidence": confidence,
        "tier_used": _source_label_simple(grouped[best_entry]["sources"]),
        "cross_validated": len(grouped[best_entry]["sources"]) >= 2,
        "confidence_label": "high" if confidence >= threshold else ("medium" if confidence >= 60 else "low"),
        "needs_review": confidence < threshold,
        "resolved_letterhead": grouped[best_entry]["raw"].get("letterhead", ""),
    }


def _source_label_simple(sources):
    order = ["learned", "caption", "routing_slip", "letterhead", "signature", "gazetteer", "keyword", "folder"]
    return "+".join(sorted(sources, key=lambda s: order.index(s) if s in order else 99))


def _suggest_raw(candidates: List[CompanyCandidate], threshold: int) -> Dict[str, Any]:
    """No roster match: suggest the best raw name for the reviewer to confirm.

    Prefers the grouping folder (when it looks like a company), else the
    highest-weighted letterhead/signature/caption candidate. Always needs_review.
    """
    folder = next((c for c in candidates if c.source == "folder" and c.name), None)
    if folder:
        name, src = folder.name, "folder"
    else:
        best = max(candidates, key=lambda c: c.weight, default=None)
        if not best:
            return _empty_result(threshold)
        name, src = best.name, best.source
    name = name.strip()
    if not name:
        return _empty_result(threshold)
    return {
        "company_name": name,
        "matched_roster_entry": None,
        "fuzzy_match_score": None,
        "company_confidence": 0,
        "tier_used": src,
        "cross_validated": False,
        "confidence_label": "none",
        "needs_review": True,
        "resolved_letterhead": name if src == "letterhead" else "",
    }


# ─────────────────────────────────────────────────────────────
# Learning store
# ─────────────────────────────────────────────────────────────

def load_hints() -> Dict[str, Dict[str, str]]:
    try:
        if HINTS_FILE.exists():
            with open(HINTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("folders", {})
                data.setdefault("letterheads", {})
                return data
    except Exception:
        pass
    return {"folders": {}, "letterheads": {}}


def save_hints(hints: Dict[str, Dict[str, str]]) -> None:
    try:
        ensure_data_dir()
        with open(HINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(hints, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def record_folder_hint(folder: str, company: str) -> None:
    if not folder or not company:
        return
    hints = load_hints()
    hints["folders"][folder] = ce.normalize_company_name(company)
    save_hints(hints)


def record_letterhead_hint(letterhead_line: str, company: str) -> None:
    if not letterhead_line or not company:
        return
    hints = load_hints()
    hints["letterheads"][_norm(letterhead_line)] = ce.normalize_company_name(company)
    save_hints(hints)


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def resolve_company(page_texts: List[dict], context: Optional[dict] = None,
                    roster: Optional[List[str]] = None,
                    threshold: int = DEFAULT_CONFIDENCE_THRESHOLD) -> Dict[str, Any]:
    if roster is None:
        roster = ce.load_known_companies()

    candidates: List[CompanyCandidate] = []
    candidates += detect_case_caption(page_texts)
    candidates += detect_routing_slip(page_texts)
    candidates += detect_letterhead(page_texts)
    candidates += detect_signature_block(page_texts)
    candidates += detect_roster_gazetteer(page_texts, roster)
    candidates += detect_keyword_window(page_texts, roster)
    candidates += detect_folder_hint(context, roster)
    candidates += detect_learned_hint(context, page_texts, roster)

    result = combine(candidates, roster, threshold=threshold)
    if result["matched_roster_entry"]:
        # record provenance source string
        pass
    return result
