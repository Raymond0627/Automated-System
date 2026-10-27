import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import fitz

from paths import DATA_DIR, configure_tesseract

OCR_CACHE_DIR = DATA_DIR / "ocr_cache"

def ensure_cache_dir() -> Path:
    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return OCR_CACHE_DIR

def compute_file_hash(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def _get_cache_path(file_hash: str, page_num: int) -> Path:
    return ensure_cache_dir() / f"{file_hash}_{page_num}.json"

def clear_disk_cache() -> None:
    if OCR_CACHE_DIR.exists():
        for item in OCR_CACHE_DIR.glob("*.json"):
            try:
                item.unlink()
            except Exception:
                pass

def get_page_word_boxes(
    doc_path: str,
    page_num: int = 0,
    use_cache: bool = True,
    zone_crop_ratio: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Extract word bounding boxes from a PDF page.
    
    1. Checks disk cache if use_cache is True.
    2. Digital check: PyMuPDF page.get_text('words'). If valid words are found, returns them instantly.
    3. Scanned check: Fallback to pytesseract.image_to_data for image pages.
    4. Caches results to disk.
    """
    file_hash = compute_file_hash(doc_path)
    cache_path = _get_cache_path(file_hash, page_num)

    if use_cache and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass

    boxes: List[Dict[str, Any]] = []

    doc = fitz.open(doc_path)
    try:
        if page_num < 0 or page_num >= len(doc):
            return []
        page = doc[page_num]

        # 1. Digital text check (instant)
        words = page.get_text("words")
        # word tuple: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
        if words and len(words) > 0:
            for w in words:
                text = str(w[4]).strip()
                if not text:
                    continue
                bbox = [float(w[0]), float(w[1]), float(w[2]), float(w[3])]
                if zone_crop_ratio is not None and bbox[1] > page.rect.height * zone_crop_ratio:
                    continue
                boxes.append({
                    "text": text,
                    "bbox": bbox,
                    "conf": 100.0,
                    "is_digital": True,
                    "line_no": int(w[6]) if len(w) > 6 else 0
                })

        # 2. Scanned image check (pytesseract fallback)
        if not boxes:
            try:
                import pytesseract
                configure_tesseract()
                # Render page at 150 DPI for OCR
                dpi = 150
                pix = page.get_pixmap(dpi=dpi)
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                
                # Zone crop if requested
                scale = 72.0 / dpi
                if zone_crop_ratio is not None:
                    crop_h = int(img.height * zone_crop_ratio)
                    img = img.crop((0, 0, img.width, crop_h))

                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
                n_boxes = len(data.get("text", []))
                for i in range(n_boxes):
                    t = str(data["text"][i]).strip()
                    conf = float(data["conf"][i]) if data.get("conf") else -1.0
                    if not t or conf < 20:
                        continue
                    # Scale coordinates from pixmap to PDF points (72 DPI)
                    x0 = float(data["left"][i]) * scale
                    y0 = float(data["top"][i]) * scale
                    x1 = x0 + float(data["width"][i]) * scale
                    y1 = y0 + float(data["height"][i]) * scale
                    line_no = int(data.get("line_num", [0])[i]) if "line_num" in data else 0
                    boxes.append({
                        "text": t,
                        "bbox": [x0, y0, x1, y1],
                        "conf": conf,
                        "is_digital": False,
                        "line_no": line_no
                    })
            except Exception:
                pass
    finally:
        doc.close()

    if use_cache and boxes:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(boxes, f)
        except Exception:
            pass

    return boxes
