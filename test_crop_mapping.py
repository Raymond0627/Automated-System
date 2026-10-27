"""Tests for crop geometry: always map from the original page, never the current crop."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fitz
import pytest

from crop_geometry import cropbox_rect_from_display


def _page(width=100, height=200, rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if rotation:
        page.set_rotation(rotation)
    return doc, page


def _approx(rect, values, tol=0.2):
    assert rect.x0 == pytest.approx(values[0], abs=tol)
    assert rect.y0 == pytest.approx(values[1], abs=tol)
    assert rect.x1 == pytest.approx(values[2], abs=tol)
    assert rect.y1 == pytest.approx(values[3], abs=tol)


def test_full_selection_returns_whole_mediabox():
    doc, page = _page()
    rect = cropbox_rect_from_display(page, 500, 1000, 0, 0, 500, 1000)
    _approx(rect, (0, 0, 100, 200))
    doc.close()


def test_left_half_selection():
    doc, page = _page()
    rect = cropbox_rect_from_display(page, 500, 1000, 0, 0, 250, 1000)
    _approx(rect, (0, 0, 50, 200))
    doc.close()


def test_recrop_uses_original_not_current_crop():
    """After a previous left crop, re-cropping still maps from the full page."""
    doc, page = _page()
    page.set_cropbox(fitz.Rect(30, 0, 100, 200))  # prior crop removed 30pt from the left

    rect = cropbox_rect_from_display(page, 500, 1000, 0, 0, 250, 1000)

    _approx(rect, (0, 0, 50, 200)), "must be relative to the original page, not the cropbox"
    page.set_cropbox(rect)  # must be accepted
    doc.close()


def test_overshoot_is_clamped_and_never_raises():
    doc, page = _page()
    page.set_cropbox(fitz.Rect(30, 0, 100, 200))

    # Selection runs past every edge (and past the float-rounded mediabox).
    rect = cropbox_rect_from_display(page, 500, 1000, -50, -50, 550, 1050)

    assert 0 <= rect.x0 < rect.x1 <= 100.001
    assert 0 <= rect.y0 < rect.y1 <= 200.001
    page.set_cropbox(rect)  # previously raised: CropBox not in MediaBox
    doc.close()


def test_left_half_on_rotated_page_swaps_axes():
    doc, page = _page(rotation=90)  # display is 200x100
    rect = cropbox_rect_from_display(page, 1000, 500, 0, 0, 500, 500)

    # Left half of the rotated view crops half of the unrotated height.
    assert rect.x0 >= -0.001 and rect.x1 <= 100.001
    assert rect.y0 >= -0.001 and rect.y1 <= 200.001
    assert rect.width == pytest.approx(100, abs=1.0)
    assert rect.height == pytest.approx(100, abs=1.0)
    page.set_cropbox(rect)
    doc.close()


def test_min_size_rejected():
    doc, page = _page()
    with pytest.raises(ValueError):
        cropbox_rect_from_display(page, 500, 1000, 10, 10, 10.5, 10.5)
    doc.close()
