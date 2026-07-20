from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
import re
import io
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


_paddle_ocr_instance = None


def _get_paddle_ocr():
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        import os
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
        from paddleocr import PaddleOCR
        _paddle_ocr_instance = PaddleOCR(use_textline_orientation=True, lang='en')
    return _paddle_ocr_instance


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


@dataclass
class DateResult:
    date: Optional[date]
    confidence: int
    method: str
    candidates: List[DateCandidate]
    raw_ocr_text: str
    blank_pages: List[int] = field(default_factory=list)
    all_blank: bool = False


DATE_PATTERNS = [
    r'\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b',
    r'\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2})\b',
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})\b',
    r'\b(\d{1,2})\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{4})\b',
]

KEYWORDS = ['date', 'dated', 'as of', 'issued', 'effective', 'ref', 're:']


def preprocess_image(image: np.ndarray, upscale: float = 1.5) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    if upscale > 1.0:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (int(w * upscale), int(h * upscale)), interpolation=cv2.INTER_LINEAR)

    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    return thresh


def pdf_to_image(pdf_path: str, page_index: int = 0, dpi: int = 200) -> Optional[np.ndarray]:
    try:
        doc = fitz.open(pdf_path)
        if page_index >= len(doc):
            page_index = 0
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        img_np = np.array(img)
        if len(img_np.shape) == 3:
            if img_np.shape[2] == 4:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
            else:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        doc.close()
        return img_np
    except Exception as e:
        print(f"Error converting PDF to image: {e}")
        return None


def run_ocr_tesseract(image: np.ndarray, psm: int = 6) -> dict:
    if pytesseract is None:
        raise ImportError("pytesseract is not installed. Install it with: pip install pytesseract")
    config = f'--psm {psm} --oem 3'
    return pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)


def run_ocr_paddle(image: np.ndarray) -> dict:
    ocr = _get_paddle_ocr()
    result = list(ocr.predict(image))

    texts, lefts, tops, widths, heights, confs = [], [], [], [], [], []

    if result:
        r = result[0]
        rec_texts = r['rec_texts']
        rec_scores = r['rec_scores']
        rec_boxes = r['rec_boxes']

        for text, score, box in zip(rec_texts, rec_scores, rec_boxes):
            if not text.strip():
                continue

            if hasattr(box, '__iter__') and len(box) >= 4:
                if len(box) == 4 and hasattr(box[0], '__iter__'):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                else:
                    xs = [box[0], box[2]]
                    ys = [box[1], box[3]]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - min(xs)), int(max(ys) - min(y))
            else:
                x, y, w, h = 0, 0, 0, 0

            texts.append(text)
            lefts.append(x)
            tops.append(y)
            widths.append(w)
            heights.append(h)
            confs.append(int(float(score) * 100))

    return {
        'text': texts, 'left': lefts, 'top': tops,
        'width': widths, 'height': heights, 'conf': confs
    }


def run_ocr(image: np.ndarray, psm: int = 6, engine: str = "tesseract") -> dict:
    if engine == "paddle":
        return run_ocr_paddle(image)
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

    for pattern in DATE_PATTERNS:
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
                    source_text=full_text[max(0, match.start()-80):match.end()+80]
                ))
            except Exception:
                continue

    return candidates


def score_candidate(candidate: DateCandidate, uniqueness_bonus: float) -> float:
    score = 0.0
    x, y, w, h = candidate.bbox
    page_h, page_w = candidate.page_height, candidate.page_width

    score += 20.0

    if page_h > 0 and page_w > 0:
        rel_y = y / page_h
        if rel_y < 0.33:
            score += 30 * (1 - rel_y / 0.33)
        rel_x = x / page_w
        if rel_x > 0.66:
            score += 20

    score += len(candidate.keywords_nearby) * 15
    score += min(candidate.confidence, 100) * 0.3
    score += uniqueness_bonus

    if candidate.source_page == 0:
        score += 10

    return score


def extract_document_date(pdf_path: str, page_index: int = 0, max_pages: int = None, engine: str = "tesseract", dpi: int = 150) -> DateResult:
    all_candidates = []
    raw_texts = []
    method = f"{engine}_heuristic"
    blank_pages = []

    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc) if max_pages is None else min(len(doc), max_pages)
        doc.close()
    except Exception:
        num_pages = 1

    for page_idx in range(num_pages):
        img = pdf_to_image(pdf_path, page_idx, dpi=dpi)
        if img is None:
            continue

        if img.size == 0 or img.shape[0] < 10 or img.shape[1] < 10:
            continue

        blank_result = detect_blank_page(img)
        if blank_result["is_blank"] is True or blank_result["is_blank"] == "needs_review":
            blank_pages.append(page_idx)
            if blank_result["is_blank"] is True:
                continue

        if all_candidates:
            continue

        if engine == "paddle":
            try:
                ocr_data = run_ocr(img, engine=engine)
                raw_text = ' '.join([t for t in ocr_data['text'] if t.strip()])
                raw_texts.append(raw_text)
                ph, pw = img.shape[:2]
                candidates = extract_candidates_from_ocr(ocr_data, 6, pw, ph, page_idx)
                all_candidates.extend(candidates)
            except Exception:
                continue
        else:
            try:
                processed = preprocess_image(img, upscale=1.5)
            except Exception:
                continue

            ph, pw = processed.shape[:2]

            for psm in [6]:
                try:
                    ocr_data = run_ocr(processed, psm, engine=engine)
                    raw_text = ' '.join([t for t in ocr_data['text'] if t.strip()])
                    raw_texts.append(raw_text)
                    candidates = extract_candidates_from_ocr(ocr_data, psm, pw, ph, page_idx)
                    all_candidates.extend(candidates)
                except Exception:
                    continue

    # Check if all pages were blank
    all_blank = len(blank_pages) == num_pages

    if not all_candidates:
        return DateResult(
            date=None,
            confidence=0,
            method=method,
            candidates=[],
            raw_ocr_text=' '.join(raw_texts) if raw_texts else "",
            blank_pages=blank_pages,
            all_blank=all_blank
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
    scored.sort(key=lambda x: x[1], reverse=True)

    best_candidate, best_score = scored[0]
    final_confidence = int(min(best_score, 100))

    return DateResult(
        date=best_candidate.parsed_date if best_candidate else None,
        confidence=final_confidence,
        method=method,
        candidates=all_candidates,
        raw_ocr_text=' '.join(raw_texts),
        blank_pages=blank_pages,
        all_blank=all_blank
    )
