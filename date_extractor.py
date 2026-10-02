from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
import re
import io
import gc
import cv2
import numpy as np
import fitz
from PIL import Image
import dateparser
from blank_page_detector import detect_blank_page

try:
    import pytesseract
except ImportError:
    pytesseract = None


@dataclass
class DateCandidate:
    matched_text: str
    parsed_date: Optional[date]
    confidence: float
    bbox: tuple
    page_width: int
    page_height: int
    keywords_nearby: List[str]
    source_psm: int
    source_page: int = 0
    score: float = 0.0
    source_text: str = ""
    is_ambiguous_numeric: bool = False


@dataclass
class DateResult:
    date: Optional[date]
    confidence: int
    method: str
    candidates: List[DateCandidate]
    raw_ocr_text: str
    blank_pages: List[int] = field(default_factory=list)
    all_blank: bool = False
    needs_review: bool = False
    top_candidates: List[DateCandidate] = field(default_factory=list)
    page_texts: List[str] = field(default_factory=list)


DATE_PATTERNS = [
    r'\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b',
    r'\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2})\b',
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})\b',
    r'\b(\d{1,2})\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{4})\b',
]

KEYWORDS = ['date', 'dated', 'as of', 'issued', 'effective', 'ref', 're:']

DOC_DATE_LABEL = re.compile(
    r'\b(?:doc\w*|dc\w{1,5})\s+(?:date|dte|dat|dae)',
    re.IGNORECASE
)

ROW_TOLERANCE_PCT = 0.02


def _build_positions(ocr_data):
    words = []
    positions = []
    for i in range(len(ocr_data['text'])):
        text = ocr_data['text'][i].strip()
        if text:
            words.append(text)
            positions.append({
                'left': ocr_data['left'][i],
                'top': ocr_data['top'][i],
                'width': ocr_data['width'][i],
                'height': ocr_data['height'][i],
                'conf': ocr_data['conf'][i],
            })
    return words, positions


def _find_date_in_text(text_chunk):
    for dp in DATE_PATTERNS:
        m = re.search(dp, text_chunk, re.IGNORECASE)
        if m:
            try:
                parsed = dateparser.parse(m.group(0), settings={
                    'PREFER_DAY_OF_MONTH': 'first',
                    'REQUIRE_PARTS': ['year', 'month', 'day'],
                })
                if parsed:
                    d = parsed.date()
                    if d.year >= 1950 and d <= date.today():
                        return d, m.group(0)
            except Exception:
                pass
    return None, None


def find_labeled_date_candidates(page_ocr_data_list):
    labeled = set()

    for entry in page_ocr_data_list:
        ocr_data = entry['ocr_data']
        page_idx = entry['page_idx']
        page_h = entry['page_height']
        page_w = entry['page_width']

        words, positions = _build_positions(ocr_data)
        if not words:
            continue

        page_text = ' '.join(words)
        row_tol = max(page_h * ROW_TOLERANCE_PCT, 10)

        for label_match in DOC_DATE_LABEL.finditer(page_text):
            label_end_char = label_match.end()
            label_start_char = label_match.start()

            label_word_idx = len(page_text[:label_start_char].split())
            label_word_idx_end = len(page_text[:label_end_char].split()) - 1
            label_word_idx = min(label_word_idx, len(positions) - 1)
            label_word_idx_end = min(label_word_idx_end, len(positions) - 1)

            if label_word_idx >= len(positions):
                continue

            label_y = positions[label_word_idx]['top']
            label_right = (positions[label_word_idx_end]['left']
                           + positions[label_word_idx_end]['width'])

            found = False

            for wi in range(len(words)):
                w_top = positions[wi]['top']
                w_left = positions[wi]['left']
                if abs(w_top - label_y) <= row_tol and w_left > label_right:
                    d, _ = _find_date_in_text(words[wi])
                    if d:
                        labeled.add((d, page_idx))
                        found = True
                        break

            if not found:
                for wi in range(len(words)):
                    w_top = positions[wi]['top']
                    w_left = positions[wi]['left']
                    if w_top >= label_y and w_left >= label_right:
                        d, _ = _find_date_in_text(words[wi])
                        if d:
                            labeled.add((d, page_idx))
                            found = True
                            break

            if not found:
                after = page_text[label_end_char:label_end_char + 80]
                d, _ = _find_date_in_text(after)
                if d:
                    labeled.add((d, page_idx))

    return labeled


CONTROL_NO_PATTERN = re.compile(r'\b\d{0,2}9002000\d{3}\b')
CONTROL_DATE_PATTERN = re.compile(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b')
# Flexible ISO datetime: handles "2017-09-20 10:04:53", "2017-09-2010:03:19", "2017-09 20 1004 53"
ISO_DATETIME_PATTERN = re.compile(r'\b\d{4}[-/]\d{2}[-/ ]\d{2}[\sT]*\d{1,2}[:\s]?\d{2}[:\s]?\d{2}\b')
# Date-only pattern: "2017-09-20" without time
ISO_DATE_ONLY_PATTERN = re.compile(r'\b(\d{4})[-/](\d{2})[-/](\d{2})\b')
# Corrupted date patterns: "W17-09-20", "L2017-09-20"
CORRUPTED_DATE_PATTERN = re.compile(r'\b[A-Z]?(\d{2,4})[-/](\d{2})[-/](\d{2})\b')
ROUTING_SLIP_RE = re.compile(r'(?:document\s+routing\s+slip|routing\s+slip|date\s+requested)', re.IGNORECASE)
MM_DD_YYYY_RE = re.compile(r'\b(\d{1,2})[/\-!\.](\d{1,2})[/\-!1\.](\d{4})\b')


def find_routing_slip_date(page_text: str) -> Optional[dict]:
    """
    Find the routing slip date from IC documents.
    Only returns a date if found via:
    1. MM/DD/YYYY near IC control number
    2. Date (MM/DD/YYYY, ISO, date-only, corrupted) near "Routing Slip" header
    Returns None if not found (no fallback — goes to pending).
    """
    MIN_YEAR = 2017

    def _parse_date(y, m, d):
        try:
            yr = int(y)
            if yr < 100:
                yr += 2000
            parsed_date = date(yr, int(m), int(d))
            if MIN_YEAR <= parsed_date.year <= date.today().year:
                return parsed_date
        except Exception:
            pass
        return None

    # Strategy 1: Find dates near IC control number
    for ctrl_match in CONTROL_NO_PATTERN.finditer(page_text):
        ctrl_end = ctrl_match.end()
        window = page_text[ctrl_end:ctrl_end + 80]
        date_match = CONTROL_DATE_PATTERN.search(window)
        if date_match:
            try:
                parsed = dateparser.parse(date_match.group(0), settings={
                    'PREFER_DAY_OF_MONTH': 'first',
                    'REQUIRE_PARTS': ['year', 'month', 'day'],
                })
                if parsed:
                    d = parsed.date()
                    if MIN_YEAR <= d.year <= date.today().year:
                        return {'date': d, 'source': 'control_number', 'confirmed': True}
            except Exception:
                pass

    # Strategy 2: Find dates near "Routing Slip" header
    for routing_match in ROUTING_SLIP_RE.finditer(page_text):
        window = page_text[routing_match.start():routing_match.start() + 300]

        # Try MM/DD/YYYY first (handles OCR glitches like 09/05!2017)
        mmdd_match = MM_DD_YYYY_RE.search(window)
        if mmdd_match:
            try:
                cleaned = f"{mmdd_match.group(1)}/{mmdd_match.group(2)}/{mmdd_match.group(3)}"
                parsed = dateparser.parse(cleaned, settings={
                    'PREFER_DAY_OF_MONTH': 'first',
                    'REQUIRE_PARTS': ['year', 'month', 'day'],
                })
                if parsed:
                    d = parsed.date()
                    if MIN_YEAR <= d.year <= date.today().year:
                        return {'date': d, 'source': 'routing_slip_mmdd', 'confirmed': True}
            except Exception:
                pass

        # Try full ISO datetime
        iso_match = ISO_DATETIME_PATTERN.search(window)
        if iso_match:
            try:
                iso_text = iso_match.group(0)
                date_part_match = re.match(r'(\d{4})[-/](\d{2})[-/ ](\d{2})', iso_text)
                if date_part_match:
                    y, m, d = date_part_match.groups()
                    parsed_date = _parse_date(y, m, d)
                    if parsed_date:
                        return {'date': parsed_date, 'source': 'routing_slip_iso', 'confirmed': True}
            except Exception:
                pass

        # Try date-only (YYYY-MM-DD)
        date_only_match = ISO_DATE_ONLY_PATTERN.search(window)
        if date_only_match:
            y, m, d = date_only_match.groups()
            parsed_date = _parse_date(y, m, d)
            if parsed_date:
                return {'date': parsed_date, 'source': 'routing_slip_date_only', 'confirmed': True}

        # Try corrupted date (W17-09-20, L2017-09-20)
        for corr_match in CORRUPTED_DATE_PATTERN.finditer(window):
            y_raw, m, d = corr_match.groups()
            parsed_date = _parse_date(y_raw, m, d)
            if parsed_date:
                return {'date': parsed_date, 'source': 'routing_slip_corrupted', 'confirmed': True}
            if len(y_raw) == 2:
                parsed_date = _parse_date('20' + y_raw, m, d)
                if parsed_date:
                    return {'date': parsed_date, 'source': 'routing_slip_corrupted', 'confirmed': True}

    # No routing slip date found — return None (goes to pending)
    return None


def find_document_date_via_control_anchor(page_text: str):
    for ctrl_match in CONTROL_NO_PATTERN.finditer(page_text):
        ctrl_end = ctrl_match.end()
        window = page_text[ctrl_end:ctrl_end + 60]
        date_match = CONTROL_DATE_PATTERN.search(window)
        if not date_match:
            continue
        try:
            parsed = dateparser.parse(date_match.group(0), settings={
                'PREFER_DAY_OF_MONTH': 'first',
                'REQUIRE_PARTS': ['year', 'month', 'day'],
            })
            if not parsed:
                continue
            d = parsed.date()
            if d.year < 1950 or d > date.today():
                continue
        except Exception:
            continue

        date_end_in_window = date_match.end()
        confirm_window = window[date_end_in_window:date_end_in_window + 80]
        confirmed = bool(ISO_DATETIME_PATTERN.search(confirm_window))

        return {'date': d, 'matched_text': date_match.group(0), 'confirmed_by_date_requested': confirmed}
    return None


def preprocess_image(image: np.ndarray, upscale: float = 1.5) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    if upscale > 1.0:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (int(w * upscale), int(h * upscale)), interpolation=cv2.INTER_LINEAR)

    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    return thresh


def pdf_to_image(pdf_path: str, page_index: int = 0, dpi: int = 200, doc: Optional["fitz.Document"] = None) -> Optional[np.ndarray]:
    own_doc = doc is None
    if own_doc:
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            print(f"Error opening PDF: {e}")
            return None
    try:
        if page_index >= len(doc):
            page_index = 0
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        img_data = pix.tobytes("png")
        del pix
        img = Image.open(io.BytesIO(img_data))
        img_np = np.array(img)
        del img, img_data
        if len(img_np.shape) == 3:
            if img_np.shape[2] == 4:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
            else:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        return img_np
    except Exception as e:
        print(f"Error converting PDF to image: {e}")
        return None
    finally:
        if own_doc and doc:
            try:
                doc.close()
            except Exception:
                pass


def run_ocr_tesseract(image: np.ndarray, psm: int = 6) -> dict:
    if pytesseract is None:
        raise ImportError("pytesseract is not installed. Install it with: pip install pytesseract")
    config = f'--psm {psm} --oem 3'
    return pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)


def run_ocr(image: np.ndarray, psm: int = 6, engine: str = "tesseract") -> dict:
    return run_ocr_tesseract(image, psm)


def extract_candidates_from_ocr(ocr_data: dict, psm: int, page_width: int, page_height: int, page_idx: int) -> List[DateCandidate]:
    candidates = []
    n_boxes = len(ocr_data['text'])

    words = []
    positions = []
    for i in range(n_boxes):
        text = ocr_data['text'][i].strip()
        if text:
            words.append(text)
            positions.append({
                'left': ocr_data['left'][i],
                'top': ocr_data['top'][i],
                'width': ocr_data['width'][i],
                'height': ocr_data['height'][i],
                'conf': ocr_data['conf'][i]
            })

    full_text = ' '.join(words)

    for pattern_idx, pattern in enumerate(DATE_PATTERNS):
        for match in re.finditer(pattern, full_text, re.IGNORECASE):
            matched = match.group(0)
            try:
                parsed = dateparser.parse(matched, settings={
                    'PREFER_DAY_OF_MONTH': 'first',
                    'REQUIRE_PARTS': ['year', 'month', 'day']
                })
                if not parsed:
                    continue
                parsed_date = parsed.date()
                if parsed_date.year < 1950 or parsed_date > date.today():
                    continue

                is_ambiguous = False
                if pattern_idx in (0, 1) and match.lastindex and match.lastindex >= 2:
                    try:
                        g1 = int(match.group(1))
                        g2 = int(match.group(2))
                        if g1 <= 12 and g2 <= 12:
                            is_ambiguous = True
                    except (ValueError, IndexError):
                        pass

                word_idx = len(full_text[:match.start()].split())
                if 0 <= word_idx < len(positions):
                    pos = positions[word_idx]
                    x, y = pos['left'], pos['top']
                    w, h = pos['width'], pos['height']
                    conf = pos['conf'] if pos['conf'] > 0 else 50
                else:
                    x, y, w, h = 0, 0, 0, 0
                    conf = 50

                keywords = []
                ctx_start = max(0, match.start() - 100)
                ctx_end = min(len(full_text), match.end() + 100)
                context = full_text[ctx_start:ctx_end].lower()
                for kw in KEYWORDS:
                    if kw in context:
                        keywords.append(kw)

                candidates.append(DateCandidate(
                    matched_text=matched,
                    parsed_date=parsed_date,
                    confidence=float(conf),
                    bbox=(x, y, w, h),
                    page_width=page_width,
                    page_height=page_height,
                    keywords_nearby=keywords,
                    source_psm=psm,
                    source_page=page_idx,
                    source_text=full_text[max(0, match.start()-80):match.end()+80],
                    is_ambiguous_numeric=is_ambiguous
                ))
            except Exception:
                continue

    return candidates


def score_candidate(candidate: DateCandidate, uniqueness_bonus: float) -> float:
    score = 0.0

    score += 20.0

    score += len(candidate.keywords_nearby) * 15
    score += min(candidate.confidence, 100) * 0.3
    score += uniqueness_bonus

    if candidate.is_ambiguous_numeric:
        score -= 35
    else:
        score += 20

    return score


def _quick_confidence(all_candidates, page_ocr_data_list, control_results):
    if not all_candidates:
        return 0.0, False
    deduped = {}
    for c in all_candidates:
        key = (c.parsed_date, c.source_page)
        if key not in deduped or c.confidence > deduped[key].confidence:
            deduped[key] = c
    unique_candidates = list(deduped.values())
    unique_date_count = len(set(c.parsed_date for c in unique_candidates if c.parsed_date))
    uniqueness_bonus = 15.0 if unique_date_count <= 1 else 0.0
    scored = [(c, score_candidate(c, uniqueness_bonus)) for c in unique_candidates]
    labeled_set = find_labeled_date_candidates(page_ocr_data_list)
    control_lookup = {}
    for pg, cr in control_results.items():
        control_lookup[(cr['date'], pg)] = cr['confirmed_by_date_requested']
    for i, (c, s) in enumerate(scored):
        key = (c.parsed_date, c.source_page)
        in_labeled = key in labeled_set
        in_control = key in control_lookup
        if in_labeled and in_control:
            scored[i] = (c, s + 100)
        elif in_control:
            scored[i] = (c, s + (90 if control_lookup[key] else 60))
        elif in_labeled:
            scored[i] = (c, s + 80)
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return 0.0, False
    best_score = scored[0][1]
    return int(min(best_score, 100)), True


def extract_document_date(pdf_path: str, page_index: int = 0, max_pages: int = None, engine: str = "tesseract", dpi: int = 150, doc: Optional["fitz.Document"] = None, docsep_pages: Optional[List[int]] = None) -> DateResult:
    all_candidates = []
    raw_texts = []
    page_ocr_data_list = []
    control_results = {}
    method = f"{engine}_heuristic"
    blank_pages = []
    EARLY_EXIT_CONFIDENCE = 85
    skip_pages = set(docsep_pages or [])

    own_doc = doc is None
    if own_doc:
        try:
            doc = fitz.open(pdf_path)
            num_pages = len(doc) if max_pages is None else min(len(doc), max_pages)
        except Exception:
            num_pages = 1
    else:
        num_pages = len(doc) if max_pages is None else min(len(doc), max_pages)

    # --- Module 1: Blank detection (all pages, no early exit) ---
    for page_idx in range(num_pages):
        if page_idx in skip_pages:
            continue
        img = pdf_to_image(pdf_path, page_idx, dpi=dpi, doc=doc)
        if img is None:
            continue
        if img.size == 0 or img.shape[0] < 10 or img.shape[1] < 10:
            continue
        blank_result = detect_blank_page(img)
        if blank_result["is_blank"] is True or blank_result["is_blank"] == "needs_review":
            # Verify with text / OCR before declaring blank (from widgets pipeline)
            is_confirmed_blank = False
            native_txt = ""
            try:
                _chk_doc = fitz.open(pdf_path) if own_doc else doc
                native_txt = _chk_doc[page_idx].get_text().strip()
                if own_doc:
                    _chk_doc.close()
            except Exception:
                pass
            words = [w for w in native_txt.split() if len(w) >= 2]
            if len(words) >= 2:
                is_confirmed_blank = False
            elif pytesseract is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
                tess_txt = pytesseract.image_to_string(gray, config='--psm 6').strip()
                tess_words = [w for w in tess_txt.split() if len(w) >= 2]
                is_confirmed_blank = len(tess_words) < 2
            else:
                is_confirmed_blank = (blank_result["is_blank"] is True)

            if is_confirmed_blank:
                blank_pages.append(page_idx)
        del img

    skip_pages.update(blank_pages)

    # --- Module 2: Date extraction via OCR (skip blank + docsep, early exit OK) ---
    for page_idx in range(num_pages):
        if page_idx in skip_pages:
            continue

        img = pdf_to_image(pdf_path, page_idx, dpi=dpi, doc=doc)
        if img is None:
            continue

        if img.size == 0 or img.shape[0] < 10 or img.shape[1] < 10:
            continue

        try:
            processed = preprocess_image(img, upscale=1.5)
        except Exception:
            del img
            continue

        del img
        ph, pw = processed.shape[:2]

        for psm in [6]:
            try:
                ocr_data = run_ocr(processed, psm, engine=engine)
                del processed
                raw_text = ' '.join([t for t in ocr_data['text'] if t.strip()])
                raw_texts.append(raw_text)
                ctrl = find_document_date_via_control_anchor(raw_text)
                if ctrl:
                    control_results[page_idx] = ctrl
                page_ocr_data_list.append({
                    'ocr_data': ocr_data, 'page_idx': page_idx,
                    'page_height': ph, 'page_width': pw,
                })
                candidates = extract_candidates_from_ocr(ocr_data, psm, pw, ph, page_idx)
                all_candidates.extend(candidates)
                del ocr_data
            except Exception:
                del processed
                continue

        if page_idx >= 1:
            conf, ok = _quick_confidence(all_candidates, page_ocr_data_list, control_results)
            labeled_pages = {page for _, page in find_labeled_date_candidates(page_ocr_data_list)}
            page_anchored = page_idx in control_results or page_idx in labeled_pages
            if ok and conf >= EARLY_EXIT_CONFIDENCE and page_anchored:
                break

    if own_doc and doc:
        try:
            doc.close()
        except Exception:
            pass

    # Check if all pages were blank
    all_blank = len(blank_pages) == num_pages

    # --- Module 3: Routing slip date (PRIMARY source) ---
    # Check ALL pages: both native text and Tesseract OCR text
    routing_dates = []
    try:
        import fitz as _fitz
        _doc_for_routing = _fitz.open(pdf_path) if own_doc else doc
        for page_idx in range(len(_doc_for_routing)):
            native_text = _doc_for_routing[page_idx].get_text()
            rs = find_routing_slip_date(native_text)
            if rs:
                routing_dates.append(rs)
        if own_doc:
            _doc_for_routing.close()
    except Exception:
        pass

    for raw_text in raw_texts:
        if raw_text:
            rs = find_routing_slip_date(raw_text)
            if rs:
                routing_dates.append(rs)

    # Any routing slip date found in the valid year range is high-confidence (95%)
    if routing_dates:
        best_routing = routing_dates[0]
        return DateResult(
            date=best_routing['date'],
            confidence=95,
            method=best_routing.get('source', 'routing_slip_confirmed'),
            candidates=[],
            raw_ocr_text=' '.join(raw_texts) if raw_texts else "",
            blank_pages=blank_pages,
            all_blank=all_blank,
            needs_review=False,
            top_candidates=[],
            page_texts=raw_texts,
        )

    # No routing slip date found — use candidate scoring with LOW confidence
    if not all_candidates:
        return DateResult(
            date=None,
            confidence=0,
            method=method,
            candidates=[],
            raw_ocr_text=' '.join(raw_texts) if raw_texts else "",
            blank_pages=blank_pages,
            all_blank=all_blank,
            needs_review=False,
            top_candidates=[],
            page_texts=raw_texts,
        )

    deduped = {}
    for c in all_candidates:
        key = (c.parsed_date, c.source_page)
        if key not in deduped or c.confidence > deduped[key].confidence:
            deduped[key] = c
    unique_candidates = list(deduped.values())

    unique_date_count = len(set(c.parsed_date for c in unique_candidates if c.parsed_date))
    uniqueness_bonus = 15.0 if unique_date_count <= 1 else 0.0

    scored = [(c, score_candidate(c, uniqueness_bonus)) for c in unique_candidates]

    labeled_set = find_labeled_date_candidates(page_ocr_data_list)

    control_lookup = {}
    for pg, cr in control_results.items():
        control_lookup[(cr['date'], pg)] = cr['confirmed_by_date_requested']

    disagree_pages = set()
    all_tracked_pages = set(d_p[1] for d_p in labeled_set) | set(control_results.keys())
    for pg in all_tracked_pages:
        labeled_dates = {d for d, p in labeled_set if p == pg}
        control_dates_on_page = {d for d, p in control_lookup.keys() if p == pg}
        if labeled_dates and control_dates_on_page and not labeled_dates & control_dates_on_page:
            disagree_pages.add(pg)

    for i, (c, s) in enumerate(scored):
        key = (c.parsed_date, c.source_page)
        if c.source_page in disagree_pages:
            continue
        in_labeled = key in labeled_set
        in_control = key in control_lookup
        if in_labeled and in_control:
            scored[i] = (c, s + 100)
        elif in_control:
            scored[i] = (c, s + (90 if control_lookup[key] else 60))
        elif in_labeled:
            scored[i] = (c, s + 80)

    scored.sort(key=lambda x: x[1], reverse=True)

    best_candidate, best_score = scored[0]
    # Cap fallback confidence at 70 (routing slip is the primary source)
    final_confidence = int(min(best_score, 70))

    needs_review = bool(disagree_pages)
    top_candidates = []
    if len(scored) >= 2:
        seen_dates = set()
        unique_scored = []
        for c, s in scored:
            if c.parsed_date not in seen_dates:
                seen_dates.add(c.parsed_date)
                unique_scored.append((c, s))
        if len(unique_scored) >= 2:
            score_gap = unique_scored[0][1] - unique_scored[1][1]
            if score_gap < 20:
                needs_review = True
            top_candidates = [c for c, s in unique_scored[:3]]

    return DateResult(
        date=best_candidate.parsed_date if best_candidate else None,
        confidence=final_confidence,
        method=method,
        candidates=all_candidates,
        raw_ocr_text=' '.join(raw_texts),
        blank_pages=blank_pages,
        all_blank=all_blank,
        needs_review=needs_review,
        top_candidates=top_candidates,
        page_texts=raw_texts,
    )
