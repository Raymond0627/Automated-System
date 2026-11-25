"""Date-off runs must not OCR every page of a scanned document."""

import sys
from pathlib import Path

import fitz
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import date_extractor


def _make_pdf(path: Path, pages: int) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _run(path: Path, limit: int, monkeypatch) -> int:
    calls = {"n": 0}

    def fake_pdf_to_image(*args, **kwargs):
        return np.zeros((120, 120, 3), dtype=np.uint8)

    def fake_blank_page(*args, **kwargs):
        return {"is_blank": False, "confidence": 0, "reason": "test"}

    def fake_run_ocr(*args, **kwargs):
        calls["n"] += 1
        return {"text": ["SCANNED PAGE"]}

    monkeypatch.setattr(date_extractor, "pdf_to_image", fake_pdf_to_image)
    monkeypatch.setattr(date_extractor, "detect_blank_page", fake_blank_page)
    monkeypatch.setattr(date_extractor, "run_ocr", fake_run_ocr)

    date_extractor.extract_document_date(
        str(path),
        include_date=False,
        skip_ocr=False,
        ocr_timeout=1,
        ocr_page_limit=limit,
    )
    return calls["n"]


def test_date_off_limits_ocr_text_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    _make_pdf(pdf, 5)

    assert _run(pdf, 2, monkeypatch) == 2


def test_date_off_zero_limit_keeps_legacy_full_scan(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    _make_pdf(pdf, 5)

    assert _run(pdf, 0, monkeypatch) == 5
