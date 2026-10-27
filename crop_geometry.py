"""Crop mapping helpers.

The crop overlay works in *display* (pixmap) pixels of the **full original
page**.  ``page.set_cropbox`` expects an absolute rectangle in the page's
unrotated MediaBox coordinates.  This module performs that conversion, always
relative to the original page - never to a crop that was already applied - and
clamps the result so PyMuPDF never raises ``CropBox not in MediaBox``.
"""

import fitz


def _display_size(page) -> tuple:
    """Size of the full (uncropped) page as rendered, with rotation applied."""
    mb = page.mediabox
    base = fitz.Rect(0, 0, mb.width, mb.height)
    rotated = base * page.rotation_matrix
    return abs(rotated.width), abs(rotated.height)


def cropbox_rect_from_display(
    page,
    display_w: float,
    display_h: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
    min_points: float = 2.0,
) -> "fitz.Rect":
    """Map a selection in display pixels to an absolute CropBox rectangle.

    Coordinates are relative to the **full original page** (the MediaBox),
    regardless of any crop currently applied to ``page``.
    """
    if display_w <= 0 or display_h <= 0:
        raise ValueError("display size must be positive")

    mb = page.mediabox
    disp_w, disp_h = _display_size(page)

    # Normalise the selection and reject degenerate boxes before transforming.
    u0 = min(max(left, 0.0), display_w) / display_w
    u1 = min(max(right, 0.0), display_w) / display_w
    v0 = min(max(top, 0.0), display_h) / display_h
    v1 = min(max(bottom, 0.0), display_h) / display_h
    if u0 > u1:
        u0, u1 = u1, u0
    if v0 > v1:
        v0, v1 = v1, v0

    # Selection in rotated display space (relative to the page origin).
    disp_rect = fitz.Rect(u0 * disp_w, v0 * disp_h, u1 * disp_w, v1 * disp_h)

    # Display space -> unrotated page space.
    if page.rotation:
        unrot = disp_rect * page.derotation_matrix
    else:
        unrot = disp_rect

    rect = fitz.Rect(
        mb.x0 + unrot.x0,
        mb.y0 + unrot.y0,
        mb.x0 + unrot.x1,
        mb.y0 + unrot.y1,
    )

    # Clamp into the MediaBox. Using the exact box values avoids the float
    # overshoot that makes set_cropbox raise "CropBox not in MediaBox".
    rect = fitz.Rect(
        max(mb.x0, rect.x0),
        max(mb.y0, rect.y0),
        min(mb.x1, rect.x1),
        min(mb.y1, rect.y1),
    )

    if rect.width < min_points or rect.height < min_points:
        raise ValueError("crop selection is too small")
    return rect
