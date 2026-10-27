import re
from typing import Any, Dict, List, Optional, Tuple
from company_extractor import (
    STRONG_KEYWORDS_RE,
    ALL_KEYWORDS_RE,
    normalize_company_name,
    load_known_companies
)

try:
    from rapidfuzz import fuzz
    from rapidfuzz.process import extractOne
except ImportError:
    fuzz = None
    extractOne = None


def _merge_words_into_lines(words: List[Dict[str, Any]], vertical_tolerance: float = 4.0) -> List[Dict[str, Any]]:
    """Group words that lie on the same horizontal line into line phrases."""
    if not words:
        return []

    # Sort primarily by y0 (top), secondarily by x0 (left)
    sorted_words = sorted(words, key=lambda w: (round(w["bbox"][1] / vertical_tolerance), w["bbox"][0]))
    
    lines: List[Dict[str, Any]] = []
    current_line: List[Dict[str, Any]] = []
    
    for w in sorted_words:
        if not current_line:
            current_line.append(w)
            continue
        
        last = current_line[-1]
        # Check if same vertical line and reasonable horizontal gap
        same_y = abs(w["bbox"][1] - last["bbox"][1]) <= vertical_tolerance
        horiz_gap = w["bbox"][0] - last["bbox"][2]
        avg_h = max(last["bbox"][3] - last["bbox"][1], 10.0)

        if same_y and horiz_gap <= (avg_h * 2.5):
            current_line.append(w)
        else:
            lines.append(_make_line_dict(current_line))
            current_line = [w]
            
    if current_line:
        lines.append(_make_line_dict(current_line))
        
    return lines


def _make_line_dict(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = " ".join(w["text"] for w in words).strip()
    x0 = min(w["bbox"][0] for w in words)
    y0 = min(w["bbox"][1] for w in words)
    x1 = max(w["bbox"][2] for w in words)
    y1 = max(w["bbox"][3] for w in words)
    conf = sum(w.get("conf", 0) for w in words) / max(len(words), 1)
    return {
        "text": text,
        "bbox": [x0, y0, x1, y1],
        "height": y1 - y0,
        "conf": conf,
        "words": words
    }


def find_line_at_point(point: Tuple[float, float], words: List[Dict[str, Any]], tolerance: float = 6.0) -> Optional[str]:
    """Find the full line of text containing or closest to the given point (x, y)."""
    if not words:
        return None
    px, py = point
    lines = _merge_words_into_lines(words, vertical_tolerance=tolerance)

    # 1. Exact hit test
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        if (x0 - tolerance) <= px <= (x1 + tolerance) and (y0 - tolerance) <= py <= (y1 + tolerance):
            return line["text"]

    # 2. Nearest vertical hit
    candidates = []
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        if (y0 - tolerance) <= py <= (y1 + tolerance):
            dist = 0 if (x0 <= px <= x1) else min(abs(px - x0), abs(px - x1))
            candidates.append((dist, line["text"]))

    if candidates:
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]

    return None


def extract_company_candidates(
    word_boxes: List[Dict[str, Any]],
    page_rect: Tuple[float, float, float, float] = (0, 0, 612, 792),
    roster: Optional[List[str]] = None,
    max_candidates: int = 4
) -> List[Dict[str, Any]]:
    """Extract and score company name candidates from word bounding boxes."""
    if not word_boxes:
        return []

    if roster is None:
        try:
            roster = load_known_companies()
        except Exception:
            roster = []

    page_h = max(page_rect[3] - page_rect[1], 100.0)
    lines = _merge_words_into_lines(word_boxes)

    # Multi-line combinations (1 to 2 consecutive lines)
    phrases = []
    for i in range(len(lines)):
        phrases.append(lines[i])
        if i + 1 < len(lines):
            l1 = lines[i]
            l2 = lines[i+1]
            # If line 2 is close below line 1
            if 0 <= (l2["bbox"][1] - l1["bbox"][3]) <= (l1["height"] * 1.5):
                combined_text = f"{l1['text']} {l2['text']}"
                phrases.append({
                    "text": combined_text,
                    "bbox": [
                        min(l1["bbox"][0], l2["bbox"][0]),
                        l1["bbox"][1],
                        max(l1["bbox"][2], l2["bbox"][2]),
                        l2["bbox"][3]
                    ],
                    "height": l2["bbox"][3] - l1["bbox"][1],
                    "conf": (l1["conf"] + l2["conf"]) / 2.0
                })

    scored_candidates = []
    seen_texts = set()

    for p in phrases:
        text = p["text"].strip()
        cleaned = re.sub(r'^[.,:;\-|/]+', '', text).strip()
        cleaned = re.sub(r'[.,:;\-|/]+$', '', cleaned).strip()
        if len(cleaned.split()) < 2:
            continue
        
        norm = normalize_company_name(cleaned)
        if not norm or norm in seen_texts:
            continue

        score = 0.0

        # Feature 1: Corporate keywords
        has_strong = bool(STRONG_KEYWORDS_RE.search(norm))
        has_weak = bool(ALL_KEYWORDS_RE.search(norm))
        if has_strong:
            score += 35.0
        elif has_weak:
            score += 15.0

        # Feature 2: Vertical position (top 35% gets high bonus)
        y0 = p["bbox"][1]
        y_ratio = y0 / page_h
        if y_ratio < 0.25:
            score += 25.0
        elif y_ratio < 0.40:
            score += 15.0
        elif y_ratio > 0.80:
            score += 10.0  # Signature area bonus

        # Feature 3: Font height (box height)
        box_h = p["height"]
        if box_h >= 14.0:
            score += 15.0
        elif box_h >= 11.0:
            score += 8.0

        # Feature 4: All-caps bonus
        letters = [c for c in cleaned if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.75:
            score += 10.0

        # Feature 5: Roster fuzzy match
        roster_match = None
        if roster and extractOne is not None:
            match = extractOne(norm, roster, scorer=fuzz.token_set_ratio)
            if match and match[1] >= 75:
                score += (float(match[1]) * 0.3)
                roster_match = match[0]

        # Filter out obvious non-companies if no keyword and no roster match
        if not has_strong and not roster_match:
            continue

        seen_texts.add(norm)
        scored_candidates.append({
            "text": cleaned,
            "normalized": norm,
            "roster_match": roster_match,
            "bbox": p["bbox"],
            "score": round(score, 1)
        })

    # Sort descending by score
    scored_candidates.sort(key=lambda c: c["score"], reverse=True)

    results = []
    for idx, c in enumerate(scored_candidates[:max_candidates]):
        badge_num = idx + 1
        is_high_conf = (c["score"] >= 65.0)
        c["badge"] = badge_num
        c["is_high_conf"] = is_high_conf
        results.append(c)

    return results
