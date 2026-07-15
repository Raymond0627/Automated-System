"""
Blank Page Detector Module
Detects whether a scanned document page is blank (back side with no content).
Uses 15% border crop + center-region adaptive threshold analysis.
Bias toward "needs_review" over auto-blank — false blank loses real documents.
"""

import cv2
import numpy as np
from typing import Dict, Any, Optional


def detect_blank_page(
    image: np.ndarray,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Blank page detection using center-region adaptive threshold.

    1. Crop 15% border (removes scanner bed background)
    2. Analyze center region with adaptive threshold (handles paper texture)
    3. Blank if center has low ink AND few components

    Returns:
        dict: is_blank (True/False/"needs_review"), confidence, reason
    """
    default_metrics = {"white_ratio": 0, "content_area": 0, "edge_ratio": 0, "std_dev": 0, "num_components": 0, "ink_ratio": 0, "text_bands": 0}

    try:
        if image is None or image.size == 0:
            return {"is_blank": "needs_review", "confidence": 0, "reason": "Image is empty or invalid", **({"metrics": default_metrics} if debug else {})}

        h, w = image.shape[:2]
        if h < 50 or w < 50:
            return {"is_blank": "needs_review", "confidence": 0, "reason": "Image too small", **({"metrics": default_metrics} if debug else {})}

        # Step 1: Crop 15% border (removes scanner bed)
        margin = int(min(h, w) * 0.15)
        if margin < 10:
            margin = int(min(h, w) * 0.08)
        cropped = image[margin:h - margin, margin:w - margin]
        ch, cw = cropped.shape[:2]

        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY) if len(cropped.shape) == 3 else cropped.copy()

        # Step 2: Full-image metrics (for logging only)
        blurred_full = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh_full = cv2.threshold(blurred_full, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        total_pixels = thresh_full.size
        white_ratio = cv2.countNonZero(thresh_full) / total_pixels
        std_dev = float(np.std(gray))

        # Step 3: Center region analysis (adaptive threshold)
        center_margin = int(min(ch, cw) * 0.1)
        if center_margin < 5:
            center_margin = 0
        if center_margin > 0:
            center = gray[center_margin:ch - center_margin, center_margin:cw - center_margin]
        else:
            center = gray
        center_total = center.size

        if center_total == 0:
            return {"is_blank": "needs_review", "confidence": 0, "reason": "Center region empty", **({"metrics": default_metrics} if debug else {})}

        # Adaptive threshold on center (handles paper texture, avoids Otsu splitting)
        center_blur = cv2.medianBlur(center, 5)
        center_inv = cv2.adaptiveThreshold(
            center_blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 4
        )
        center_ink = np.count_nonzero(center_inv)
        center_ink_ratio = (center_ink / center_total) * 100.0
        center_white_ratio = 1.0 - (center_ink / center_total)

        # Count significant components from adaptive result
        center_num_labels, _, center_stats, _ = cv2.connectedComponentsWithStats(center_inv, connectivity=8)
        center_min_area = center_total * 0.001
        center_components = sum(1 for i in range(1, center_num_labels) if center_stats[i, cv2.CC_STAT_AREA] >= center_min_area)

        metrics = {
            "white_ratio": round(white_ratio, 4),
            "content_area": 0,
            "edge_ratio": 0,
            "std_dev": round(std_dev, 2),
            "num_components": center_components,
            "ink_ratio": round(center_ink_ratio, 2),
            "text_bands": 0,
        }

        # ---- DECISION LOGIC ----

        # Clearly has content: center has BOTH significant components AND ink
        if (center_components >= 3 and center_ink_ratio > 2.0):
            confidence = min(95, 70 + center_components * 3)
            result = {
                "is_blank": False,
                "confidence": confidence,
                "reason": f"Content: {center_components} regions, ink {center_ink_ratio:.1f}%, {white_ratio*100:.1f}% white",
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Very blank: center has no ink and no significant components
        if (center_white_ratio >= 0.95 and center_components == 0 and center_ink_ratio < 1.0):
            confidence = min(95, int(center_white_ratio * 100))
            result = {
                "is_blank": True,
                "confidence": confidence,
                "reason": f"Blank: center ink {center_ink_ratio:.1f}%, {center_components} regions, {white_ratio*100:.1f}% white",
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Likely blank: low ink, few components
        if (center_white_ratio > 0.90 and center_components <= 1 and center_ink_ratio < 2.0):
            confidence = min(85, int(60 + center_white_ratio * 20))
            result = {
                "is_blank": True,
                "confidence": confidence,
                "reason": f"Likely blank: center ink {center_ink_ratio:.1f}%, {center_components} regions, {white_ratio*100:.1f}% white",
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Borderline — needs review
        confidence = int(50 + (center_white_ratio - 0.80) * 100)
        confidence = max(10, min(80, confidence))
        result = {
            "is_blank": "needs_review",
            "confidence": confidence,
            "reason": f"Ambiguous: center ink {center_ink_ratio:.1f}%, {center_components} regions, {white_ratio*100:.1f}% white, std {std_dev:.1f}",
        }
        if debug:
            result["metrics"] = metrics
        return result

    except Exception as e:
        return {"is_blank": "needs_review", "confidence": 0, "reason": f"Analysis failed: {str(e)}", **({"metrics": default_metrics} if debug else {})}


def detect_blank_page_batch(images: list, debug: bool = False) -> Dict[int, Dict[str, Any]]:
    """Analyzes multiple page images from a single document."""
    return {i: detect_blank_page(img, debug=debug) for i, img in enumerate(images)}


def is_entirely_blank(images: list) -> bool:
    """Checks if ALL pages in a document are blank."""
    if not images:
        return True
    return all(detect_blank_page(img)["is_blank"] is True for img in images)
