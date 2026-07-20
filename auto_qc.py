import cv2
import numpy as np
import shutil
from typing import Dict, List, Tuple, Optional
from pathlib import Path

# ---- Default thresholds (overridable via config) ----
BLANK_INK_RATIO_THRESHOLD = 1.5       # % of ink pixels below which page is blank
ROTATION_CONFIDENCE_THRESHOLD = 65    # OSD confidence % threshold
MIRROR_CONFIDENCE_DELTA_THRESHOLD = 15  # % gap between normal vs flipped OCR scores


def check_blank(page_img: np.ndarray, ink_threshold: float = BLANK_INK_RATIO_THRESHOLD) -> Dict:
    """
    Detect blank pages using OpenCV:
      grayscale -> median blur -> adaptive threshold (Gaussian) -> ink pixel ratio.
    Returns {'is_blank': bool, 'ink_ratio': float, 'confidence': float}
    """
    gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.medianBlur(gray, 5)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 4
    )
    ink_pixels = np.count_nonzero(binary)
    total_pixels = binary.shape[0] * binary.shape[1]
    ink_ratio = (ink_pixels / total_pixels) * 100.0
    is_blank = ink_ratio < ink_threshold
    confidence = ink_ratio  # the actual measured value
    return {
        "is_blank": is_blank,
        "ink_ratio": round(ink_ratio, 2),
        "confidence": round(ink_ratio, 2),
        "threshold": ink_threshold,
    }


def check_rotation(page_img: np.ndarray, confidence_threshold: float = ROTATION_CONFIDENCE_THRESHOLD) -> Dict:
    """
    Detect page rotation using Tesseract OSD.
    Preprocess: denoise -> CLAHE contrast enhancement -> upscale 1.5x.
    Returns {'angle': int, 'confidence': float, 'is_rotated': bool}
    Only flags as rotated when OSD detects a non-zero angle with sufficient confidence.
    """
    import pytesseract

    gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 3)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)
    h, w = enhanced.shape
    upscaled = cv2.resize(enhanced, (int(w * 1.2), int(h * 1.2)), interpolation=cv2.INTER_LINEAR)

    try:
        osd = pytesseract.image_to_osd(upscaled, output_type=pytesseract.Output.DICT)
        angle = int(osd.get("rotate", 0))
        conf = float(osd.get("orientation_conf", 0))
    except Exception:
        angle = 0
        conf = 0.0

    is_rotated = angle != 0 and conf >= confidence_threshold
    return {
        "angle": angle,
        "confidence": round(conf, 1),
        "is_rotated": is_rotated,
        "threshold": confidence_threshold,
    }


def check_mirrored(
    page_img: np.ndarray,
    delta_threshold: float = MIRROR_CONFIDENCE_DELTA_THRESHOLD,
) -> Dict:
    """
    Detect mirrored pages by comparing OCR confidence on original vs horizontally-flipped copy.
    If flipped version scores meaningfully higher -> flag as mirrored.
    Uses PaddleOCR if available, falls back to Tesseract.
    Returns {'is_mirrored': bool, 'confidence_delta': float, 'needs_review': bool}
    """
    flipped = cv2.flip(page_img, 1)

    def _ocr_confidence(img: np.ndarray) -> float:
        try:
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False, use_gpu=False)
            result = ocr.ocr(img, cls=False)
            if result and result[0]:
                scores = [line[1][1] for line in result[0] if line[1]]
                return (sum(scores) / len(scores)) * 100 if scores else 0.0
        except Exception:
            pass
        try:
            import pytesseract
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confs = [c for c in data["conf"] if c > 0]
            return sum(confs) / len(confs) if confs else 0.0
        except Exception:
            return 0.0

    normal_conf = _ocr_confidence(page_img)
    flipped_conf = _ocr_confidence(flipped)
    delta = flipped_conf - normal_conf

    is_mirrored = delta >= delta_threshold
    needs_review = 0 < delta < delta_threshold
    return {
        "is_mirrored": is_mirrored,
        "normal_confidence": round(normal_conf, 1),
        "flipped_confidence": round(flipped_conf, 1),
        "confidence_delta": round(delta, 1),
        "needs_review": needs_review,
        "threshold": delta_threshold,
    }


def run_qc(
    page_img: np.ndarray,
    blank_threshold: float = BLANK_INK_RATIO_THRESHOLD,
    rotation_threshold: float = ROTATION_CONFIDENCE_THRESHOLD,
    mirror_threshold: float = MIRROR_CONFIDENCE_DELTA_THRESHOLD,
) -> Dict:
    """Run all QC checks on a single page image. Returns combined results dict."""
    blank = check_blank(page_img, blank_threshold)
    rotation = check_rotation(page_img, rotation_threshold)
    mirrored = check_mirrored(page_img, mirror_threshold)

    failures = []
    if blank["is_blank"]:
        failures.append(f"blank: {blank['ink_ratio']}%")
    if rotation["is_rotated"]:
        failures.append(f"rotated: {rotation['angle']}deg ({rotation['confidence']}%)")
    if mirrored["is_mirrored"]:
        failures.append(f"mirrored: delta {mirrored['confidence_delta']}%")
    if mirrored["needs_review"]:
        failures.append(f"mirror: ambiguous ({mirrored['confidence_delta']}%)")

    is_failure = blank["is_blank"] or rotation["is_rotated"] or mirrored["is_mirrored"]
    needs_review = mirrored["needs_review"]
    qc_status = "failed" if is_failure else ("needs_review" if needs_review else "passed")

    return {
        "blank_detected": blank["is_blank"],
        "blank_ink_ratio": blank["ink_ratio"],
        "rotation_detected": str(rotation["angle"]) + "deg" if rotation["is_rotated"] else "none",
        "rotation_confidence": rotation["confidence"],
        "mirrored_detected": mirrored["is_mirrored"],
        "mirror_delta": mirrored["confidence_delta"],
        "mirror_needs_review": mirrored["needs_review"],
        "qc_status": qc_status,
        "qc_failure_reasons": "; ".join(failures) if failures else "",
    }


def detect_docsep_page(page_img: np.ndarray) -> Dict:
    """
    Detect DOCSEP separator page via QR code detection.
    DOCSEP pages always have a QR code on the left side.
    Returns {'is_docsep': bool, 'confidence': float, 'matched_text': str}
    """
    try:
        gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
        detector = cv2.QRCodeDetector()
        data, points, _ = detector.detectAndDecode(gray)
        if points is not None:
            return {"is_docsep": True, "confidence": 95.0, "matched_text": "QR_CODE"}
    except Exception:
        pass
    return {"is_docsep": False, "confidence": 0.0, "matched_text": ""}


def detect_docsep_flag(pdf_path: str, dpi: int = 150) -> Dict:
    """
    Detect DOCSEP separator pages in a PDF without modifying the file.
    Returns {docsep_pages: List[int], count: int, confidence: float, reason: str}
    """
    import fitz
    doc = fitz.open(pdf_path)
    total = len(doc)
    docsep_pages = []
    confidence = 0.0

    if total > 0:
        pix = doc[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        result = detect_docsep_page(img)
        if result["is_docsep"]:
            docsep_pages.append(0)
            confidence = result["confidence"]

    doc.close()
    return {
        "docsep_pages": docsep_pages,
        "count": len(docsep_pages),
        "confidence": confidence,
        "reason": "QR code detected" if docsep_pages else "",
    }


def remove_docsep_pages(pdf_path: str, output_dir: str, dpi: int = 150) -> Dict:
    """
    Open a PDF, detect and remove DOCSEP separator page from first page position.
    Saves cleaned PDF to output_dir and returns details.
    Returns {removed: bool, count: int, pages_removed: List[int], cleaned_path: str}
    """
    import fitz
    doc = fitz.open(pdf_path)
    total = len(doc)
    pages_to_remove = []

    # Check first page
    if total > 0:
        pix = doc[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        result = detect_docsep_page(img)
        if result["is_docsep"]:
            pages_to_remove.append(0)

    if not pages_to_remove:
        doc.close()
        return {"removed": False, "count": 0, "pages_removed": [], "cleaned_path": pdf_path}

    # Remove pages in reverse order
    for pn in reversed(pages_to_remove):
        doc.delete_page(pn)

    # Save cleaned version
    stem = Path(pdf_path).stem
    cleaned_name = f"{stem}_cleaned.pdf"
    out_path = Path(output_dir) / cleaned_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path), incremental=False, garbage=4, deflate=True)
    doc.close()
    return {
        "removed": True,
        "count": len(pages_to_remove),
        "pages_removed": pages_to_remove,
        "cleaned_path": str(out_path),
    }


def remove_blank_pages(pdf_path: str, blank_indices: List[int], output_path: str) -> int:
    """
    Remove blank pages from a PDF file and save to output_path.
    If output_path == pdf_path, saves to a temp file first then replaces.
    Used during finalize to strip blank pages from the output copy.
    Returns number of pages removed.
    """
    if not blank_indices:
        return 0
    import fitz
    doc = fitz.open(pdf_path)
    removed = 0
    for pn in reversed(sorted(blank_indices)):
        if pn < len(doc):
            doc.delete_page(pn)
            removed += 1
    if removed > 0:
        save_path = output_path
        if output_path == pdf_path:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            save_path = tmp.name
            tmp.close()
            doc.save(save_path, incremental=False, garbage=4, deflate=True)
            doc.close()
            shutil.move(save_path, output_path)
        else:
            doc.save(save_path, incremental=False, garbage=4, deflate=True)
            doc.close()
    else:
        doc.close()
    return removed


def run_qc_on_pdf(
    pdf_path: str,
    blank_threshold: float = BLANK_INK_RATIO_THRESHOLD,
    rotation_threshold: float = ROTATION_CONFIDENCE_THRESHOLD,
    mirror_threshold: float = MIRROR_CONFIDENCE_DELTA_THRESHOLD,
    max_pages: int = 3,
    dpi: int = 150,
) -> Dict:
    """
    Run QC on a PDF file. Processes up to `max_pages` pages (cheapest to scan all,
    but limit for speed). Returns aggregated QC results.
    """
    import fitz
    doc = fitz.open(pdf_path)
    total = len(doc)
    pages_to_check = min(total, max_pages)

    agg = {
        "blank_detected": False,
        "blank_ink_ratio": 0.0,
        "rotation_detected": "none",
        "rotation_confidence": 100.0,
        "mirrored_detected": False,
        "mirror_delta": 0.0,
        "mirror_needs_review": False,
        "qc_status": "passed",
        "qc_failure_reasons": "",
        "pages_checked": pages_to_check,
        "total_pages": total,
    }

    for pn in range(pages_to_check):
        page = doc[pn]
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        result = run_qc(img, blank_threshold, rotation_threshold, mirror_threshold)
        if result["blank_detected"]:
            agg["blank_detected"] = True
        agg["blank_ink_ratio"] = max(agg["blank_ink_ratio"], result["blank_ink_ratio"])
        if result["rotation_detected"] != "none":
            agg["rotation_detected"] = result["rotation_detected"]
            agg["rotation_confidence"] = min(agg["rotation_confidence"], result["rotation_confidence"])
        if result["mirrored_detected"]:
            agg["mirrored_detected"] = True
            agg["mirror_delta"] = max(agg["mirror_delta"], result["mirror_delta"])
        if result["mirror_needs_review"]:
            agg["mirror_needs_review"] = True

        if result["qc_status"] != "passed":
            agg["qc_status"] = result["qc_status"]
        if result["qc_failure_reasons"]:
            agg["qc_failure_reasons"] = (
                agg["qc_failure_reasons"] + "; " + result["qc_failure_reasons"]
                if agg["qc_failure_reasons"]
                else result["qc_failure_reasons"]
            )

    doc.close()
    return agg
