"""
Blank Page Detector Module
Detects whether a scanned document page is blank (back side with no content).
Ignores scanner artifacts (black borders) and analyzes inner content area.
"""

import cv2
import numpy as np
from typing import Dict, Any, Optional


def detect_blank_page(
    image: np.ndarray,
    border_crop: float = 0.08,
    blank_threshold: float = 0.92,
    content_threshold: float = 0.80,
    min_content_area: float = 0.0005,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Analyzes a scanned document page image and determines if it's blank.

    Args:
        image: BGR numpy array from pdf_to_image()
        border_crop: Fraction of image to crop from edges (default 8%)
        blank_threshold: White pixel ratio above which page is BLANK (default 0.92)
        content_threshold: White pixel ratio below which page is NOT_BLANK (default 0.80)
        min_content_area: Minimum fraction of image that must contain content (default 0.05%)
        debug: If True, return detailed metrics

    Returns:
        dict with keys: is_blank, confidence, reason
        - is_blank: True (blank), False (has content), "needs_review" (uncertain)
        - confidence: 0-100
        - reason: explanation string
        - metrics: (only if debug=True) detailed detection metrics
    """
    default_metrics = {
        "white_ratio": 0,
        "content_area": 0,
        "edge_ratio": 0,
        "std_dev": 0,
        "num_components": 0,
    }

    try:
        if image is None or image.size == 0:
            result = {
                "is_blank": "needs_review",
                "confidence": 0,
                "reason": "Image is empty or invalid"
            }
            if debug:
                result["metrics"] = default_metrics
            return result

        h, w = image.shape[:2]
        if h < 50 or w < 50:
            result = {
                "is_blank": "needs_review",
                "confidence": 0,
                "reason": "Image too small for analysis"
            }
            if debug:
                result["metrics"] = default_metrics
            return result

        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        # Crop outer border (scanner artifacts)
        margin_y = int(h * border_crop)
        margin_x = int(w * border_crop)

        if margin_y == 0 or margin_x == 0:
            result = {
                "is_blank": "needs_review",
                "confidence": 0,
                "reason": "Border crop too small"
            }
            if debug:
                result["metrics"] = default_metrics
            return result

        inner = gray[margin_y:h - margin_y, margin_x:w - margin_x]

        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(inner, (5, 5), 0)

        # Otsu threshold to separate content from background
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Calculate white pixel ratio (content appears as black on white)
        total_pixels = thresh.size
        white_pixels = cv2.countNonZero(thresh)
        white_ratio = white_pixels / total_pixels

        # Connected component analysis
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            cv2.bitwise_not(thresh), connectivity=8
        )

        # Filter out tiny components (noise/dust)
        min_area = total_pixels * min_content_area
        significant_components = 0
        total_content_area = 0

        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area >= min_area:
                significant_components += 1
                total_content_area += area

        content_area_ratio = total_content_area / total_pixels

        # Edge detection for form lines, stamps, borders
        edges = cv2.Canny(inner, 50, 150)
        edge_pixels = cv2.countNonZero(edges)
        edge_ratio = edge_pixels / total_pixels

        # Standard deviation analysis (blank pages have low std_dev)
        std_dev = float(np.std(inner))

        # Color variance analysis (check uniformity)
        if len(image.shape) == 3:
            inner_color = image[margin_y:h - margin_y, margin_x:w - margin_x]
            channel_stds = [np.std(inner_color[:,:,c]) for c in range(3)]
            avg_color_std = np.mean(channel_stds)
        else:
            avg_color_std = std_dev

        # Build metrics dict
        metrics = {
            "white_ratio": round(white_ratio, 4),
            "content_area": round(content_area_ratio, 6),
            "edge_ratio": round(edge_ratio, 6),
            "std_dev": round(std_dev, 2),
            "num_components": significant_components,
            "avg_color_std": round(avg_color_std, 2),
        }

        # Decision logic

        # Very blank: high white ratio, no components, low std_dev
        if (white_ratio >= blank_threshold and
            significant_components == 0 and
            content_area_ratio < 0.001 and
            std_dev < 15):
            confidence = min(99, int(white_ratio * 100))
            result = {
                "is_blank": True,
                "confidence": confidence,
                "reason": f"White: {white_ratio*100:.1f}%, StdDev: {std_dev:.1f}, No content detected"
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Clearly has content
        if (white_ratio <= content_threshold or
            significant_components >= 3 or
            content_area_ratio > 0.02):
            confidence = min(99, int((1 - white_ratio) * 100))
            result = {
                "is_blank": False,
                "confidence": confidence,
                "reason": f"Found {significant_components} regions ({content_area_ratio*100:.2f}% area), {white_ratio*100:.1f}% white"
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Edge content (form lines, stamps, logos)
        if edge_ratio > 0.005:
            confidence = min(95, int(70 + edge_ratio * 5000))
            result = {
                "is_blank": False,
                "confidence": confidence,
                "reason": f"Detected edges/lines ({edge_ratio*100:.3f}%)"
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Low standard deviation suggests uniform/blank
        if std_dev < 8 and white_ratio > 0.88:
            confidence = min(90, int(60 + (std_dev - 8) * -3))
            result = {
                "is_blank": True,
                "confidence": confidence,
                "reason": f"Very uniform image (StdDev: {std_dev:.1f}), likely blank"
            }
            if debug:
                result["metrics"] = metrics
            return result

        # Borderline case
        confidence = int(50 + (white_ratio - 0.85) * 200)
        confidence = max(10, min(90, confidence))

        if white_ratio > 0.90:
            result = {
                "is_blank": "needs_review",
                "confidence": confidence,
                "reason": f"Ambiguous: {white_ratio*100:.1f}% white, {significant_components} marks, StdDev: {std_dev:.1f}"
            }
        else:
            result = {
                "is_blank": "needs_review",
                "confidence": confidence,
                "reason": f"Ambiguous: {white_ratio*100:.1f}% white, {content_area_ratio*100:.2f}% content"
            }

        if debug:
            result["metrics"] = metrics
        return result

    except Exception as e:
        result = {
            "is_blank": "needs_review",
            "confidence": 0,
            "reason": f"Analysis failed: {str(e)}"
        }
        if debug:
            result["metrics"] = default_metrics
        return result


def detect_blank_page_batch(
    images: list,
    border_crop: float = 0.08,
    debug: bool = False
) -> Dict[int, Dict[str, Any]]:
    """
    Analyzes multiple page images from a single document.

    Args:
        images: List of BGR numpy arrays (one per page)
        border_crop: Fraction of image to crop from edges
        debug: If True, return detailed metrics

    Returns:
        dict mapping page index to detection result
    """
    results = {}
    for i, img in enumerate(images):
        results[i] = detect_blank_page(img, border_crop, debug=debug)
    return results


def is_entirely_blank(images: list, border_crop: float = 0.08) -> bool:
    """
    Checks if ALL pages in a document are blank.

    Args:
        images: List of BGR numpy arrays (one per page)
        border_crop: Fraction of image to crop from edges

    Returns:
        True if all pages are blank, False otherwise
    """
    if not images:
        return True

    for img in images:
        result = detect_blank_page(img, border_crop)
        if result["is_blank"] is not True:
            return False

    return True
