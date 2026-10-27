"""Tests for large-document handling (200 MB / 1000+ page PDFs).

Root causes covered:
- the rotation QC check (Tesseract OSD) costs ~0.5 s/page and must be
  switchable off;
- the worker must not keep every rendered page image in memory;
- the blank-page verifier must not re-open the whole PDF per blank page.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import auto_qc
import date_extractor


def _ink_image(size=(400, 400)):
    """A page image with plenty of ink so check_blank() reports not-blank."""
    rng = np.random.default_rng(11)
    return rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)


# --------------------------------------------------------------- rotation QC


def test_run_qc_skips_rotation_when_disabled(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("check_rotation must not run when rotation is disabled")

    monkeypatch.setattr(auto_qc, "check_rotation", fail)

    result = auto_qc.run_qc(_ink_image(), rotation_enabled=False)

    assert result["qc_status"] == "passed"
    assert result["rotation_detected"] == "skipped"
    assert result["rotation_confidence"] == 0.0


def test_run_qc_runs_rotation_when_enabled(monkeypatch):
    calls = {"n": 0}

    def fake_rotation(*args, **kwargs):
        calls["n"] += 1
        return {"angle": 90, "confidence": 99.0, "is_rotated": True, "threshold": 65}

    monkeypatch.setattr(auto_qc, "check_rotation", fake_rotation)

    result = auto_qc.run_qc(_ink_image(), rotation_enabled=True)

    assert calls["n"] == 1
    assert result["qc_status"] == "failed"
    assert "rotated" in result["qc_failure_reasons"]


def test_run_qc_defaults_to_rotation_enabled(monkeypatch):
    """Existing callers that do not pass the flag keep the old behaviour."""
    calls = {"n": 0}

    def fake_rotation(*args, **kwargs):
        calls["n"] += 1
        return {"angle": 0, "confidence": 0.0, "is_rotated": False, "threshold": 65}

    monkeypatch.setattr(auto_qc, "check_rotation", fake_rotation)

    auto_qc.run_qc(_ink_image())

    assert calls["n"] == 1


# ------------------------------------------------- blank-page re-open bug


def test_blank_scan_opens_pdf_once(tmp_path, monkeypatch):
    """A blank page must not cause the whole PDF to be re-opened."""
    import fitz

    pdf = tmp_path / "blanks.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    opened = {"n": 0}
    real_open = fitz.open

    def counting_open(*args, **kwargs):
        opened["n"] += 1
        return real_open(*args, **kwargs)

    # Every page is blank; the verifier would previously re-open per page.
    monkeypatch.setattr(fitz, "open", counting_open)

    result = date_extractor.extract_document_date(
        str(pdf), include_date=False, skip_ocr=True
    )

    assert result.blank_pages == [0, 1, 2]
    # one open for the scan; no per-blank-page re-open
    assert opened["n"] == 1, f"PDF opened {opened['n']} times, expected 1"


# ----------------------------------------------------- worker memory behaviour


def _make_pdf(path: Path, pages: int = 2) -> None:
    import fitz

    doc = fitz.open()
    for idx in range(pages):
        page = doc.new_page()
        if idx == 0:
            page.insert_text((72, 100), "DATE: 15 May 2024")
    doc.save(str(path))
    doc.close()


def _doc_info(pdf: Path) -> dict:
    return {
        "original_path": str(pdf),
        "original_filename": pdf.name,
        "division_code": "003",
        "company_name": "",
        "rel_path": "",
    }


def _worker_config(tmp_path: Path, rotation: bool) -> dict:
    return {
        "input_root": str(tmp_path),
        "output_root": str(tmp_path / "out"),
        "flagged_root": str(tmp_path / "flagged"),
        "confidence_threshold": 70,
        "company_confidence_threshold": 90,
        "page_index": 0,
        "earliest_year": 1950,
        "ocr_engine": "tesseract",
        "render_dpi": 150,
        "enable_docsep_removal": False,
        "enable_qc": True,
        "document_date_enabled": False,
        "qc_blank_threshold": 1.5,
        "qc_rotation_threshold": 65,
        "qc_rotation_enabled": rotation,
        "gpu_mode": "cpu",
        "remote_gpu_url": "",
    }


def _stub_qc_result(total_pages: int) -> dict:
    return {
        "blank_detected": False, "blank_ink_ratio": 0.0,
        "rotation_detected": "none", "rotation_confidence": 0.0,
        "mirrored_detected": False, "mirror_delta": 0.0,
        "mirror_needs_review": False, "qc_status": "passed",
        "qc_failure_reasons": "", "pages_checked": total_pages,
        "total_pages": total_pages,
    }


def test_worker_does_not_cache_page_images(tmp_path, monkeypatch):
    import ui.widgets as widgets

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=3)

    captured = {}
    real_extract = widgets.extract_document_date

    def spy_extract(*args, **kwargs):
        captured["page_image_callback"] = kwargs.get("page_image_callback")
        return real_extract(*args, **kwargs)

    def spy_qc(page_images, **kwargs):
        captured["page_images"] = page_images
        captured["qc_kwargs"] = kwargs
        return _stub_qc_result(3)

    monkeypatch.setattr(widgets, "extract_document_date", spy_extract)
    monkeypatch.setattr(widgets, "_run_qc_on_pages", spy_qc)
    monkeypatch.setattr(widgets, "detect_docsep_flag", lambda *a, **k: {"docsep_pages": []})
    monkeypatch.setattr(
        widgets, "get_company_name_for_filename",
        lambda *a, **k: {"company_name": "Acme", "company_confidence": 99, "tier_used": "letterhead"},
    )

    widgets._process_doc_worker(_doc_info(pdf), _worker_config(tmp_path, rotation=False), 150)

    assert captured["page_image_callback"] is None, (
        "worker must not ask the scanner to keep every page image in memory"
    )
    assert "page_images" not in captured, (
        "rotation disabled: the redundant QC page pass must be skipped"
    )


def test_worker_runs_qc_without_cached_images_when_rotation_enabled(tmp_path, monkeypatch):
    import ui.widgets as widgets

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=3)

    captured = {}
    real_extract = widgets.extract_document_date

    def spy_extract(*args, **kwargs):
        captured["page_image_callback"] = kwargs.get("page_image_callback")
        return real_extract(*args, **kwargs)

    def spy_qc(page_images, **kwargs):
        captured["page_images"] = page_images
        captured["qc_kwargs"] = kwargs
        return _stub_qc_result(3)

    monkeypatch.setattr(widgets, "extract_document_date", spy_extract)
    monkeypatch.setattr(widgets, "_run_qc_on_pages", spy_qc)
    monkeypatch.setattr(widgets, "detect_docsep_flag", lambda *a, **k: {"docsep_pages": []})
    monkeypatch.setattr(
        widgets, "get_company_name_for_filename",
        lambda *a, **k: {"company_name": "Acme", "company_confidence": 99, "tier_used": "letterhead"},
    )

    widgets._process_doc_worker(_doc_info(pdf), _worker_config(tmp_path, rotation=True), 150)

    assert captured["page_image_callback"] is None
    assert captured["page_images"] == [], (
        "QC must render pages itself instead of holding them all in memory"
    )
    assert captured["qc_kwargs"].get("rotation_enabled") is True


# ----------------------------------------------------------- config plumbing


def test_pipeline_config_disables_rotation_by_default(tmp_path):
    from pipeline import PipelineConfig

    cfg = PipelineConfig(
        input_root=str(tmp_path / "in"),
        output_root=str(tmp_path / "out"),
    )

    assert cfg.qc_rotation_enabled is False


def _batch_for(pdf: Path):
    from pipeline import DivisionBatch, Document

    doc = Document(
        original_path=str(pdf),
        division_code="003",
        company_name="Acme",
        original_filename=pdf.name,
    )
    return DivisionBatch(division_code="003", documents=[doc]), doc


def test_extract_dates_for_batch_skips_qc_pass_when_rotation_disabled(tmp_path, monkeypatch):
    import pipeline
    from pipeline import PipelineConfig
    from date_extractor import DateResult

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)
    cfg = PipelineConfig(input_root=str(tmp_path / "in"), output_root=str(tmp_path / "out"))
    cfg.enable_docsep_removal = False
    cfg.enable_qc = True
    cfg.qc_rotation_enabled = False
    batch, doc = _batch_for(pdf)

    monkeypatch.setattr(
        pipeline, "extract_document_date",
        lambda *a, **k: DateResult(
            date=None, confidence=0, method="skipped", candidates=[],
            raw_ocr_text="", blank_pages=[], all_blank=False,
        ),
    )
    monkeypatch.setattr(
        pipeline, "get_company_name_for_filename",
        lambda *a, **k: {"company_name": "Acme", "company_confidence": 50, "tier_used": "x"},
    )
    called = {"n": 0}
    monkeypatch.setattr(
        pipeline, "run_qc_on_pdf",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"qc_status": "passed"},
    )

    pipeline.extract_dates_for_batch(batch, cfg)

    assert called["n"] == 0, "rotation disabled: the second raster pass must be skipped"
    assert doc.flagged_data is not None
    assert doc.flagged_data["qc"]["qc_status"] == "passed"
    assert doc.flagged_data["qc"]["rotation_detected"] == "skipped"


def test_extract_dates_for_batch_forwards_rotation_flag_when_enabled(tmp_path, monkeypatch):
    import pipeline
    from pipeline import PipelineConfig
    from date_extractor import DateResult

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)
    cfg = PipelineConfig(input_root=str(tmp_path / "in"), output_root=str(tmp_path / "out"))
    cfg.enable_docsep_removal = False
    cfg.enable_qc = True
    cfg.qc_rotation_enabled = True
    batch, _doc = _batch_for(pdf)

    monkeypatch.setattr(
        pipeline, "extract_document_date",
        lambda *a, **k: DateResult(
            date=None, confidence=0, method="skipped", candidates=[],
            raw_ocr_text="", blank_pages=[], all_blank=False,
        ),
    )
    monkeypatch.setattr(
        pipeline, "get_company_name_for_filename",
        lambda *a, **k: {"company_name": "Acme", "company_confidence": 50, "tier_used": "x"},
    )
    captured = {}
    monkeypatch.setattr(
        pipeline, "run_qc_on_pdf",
        lambda *a, **k: captured.update(k) or {"qc_status": "passed"},
    )

    pipeline.extract_dates_for_batch(batch, cfg)

    assert captured.get("rotation_enabled") is True


# --------------------------------------------------- rasterisation hot path


def test_pdf_to_image_does_not_encode_png(tmp_path, monkeypatch):
    """Rendering must hand back raw samples, not a PNG round-trip.

    PNG encode+decode measured ~0.5 s/page on a 1335-page scan (the real
    cause of the multi-minute stall), versus ~0.01 s/page for raw samples.
    """
    import fitz

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)

    def fail(*args, **kwargs):
        raise AssertionError("pdf_to_image must not encode the page to PNG")

    monkeypatch.setattr(fitz.Pixmap, "tobytes", fail)

    img = date_extractor.pdf_to_image(str(pdf), 0, dpi=100)

    assert img is not None
    assert img.ndim == 3
    assert img.shape[2] == 3, "expected an OpenCV BGR image"


def test_pdf_to_image_matches_direct_render(tmp_path):
    import cv2
    import fitz
    import numpy as np

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)

    img = date_extractor.pdf_to_image(str(pdf), 0, dpi=100)

    doc = fitz.open(str(pdf))
    pix = doc[0].get_pixmap(dpi=100)
    raw = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    expected = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
    doc.close()

    assert img.shape == expected.shape
    assert np.array_equal(img, expected)


# ------------------------------------------------------------- page progress


def test_extract_document_date_emits_page_progress(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=3)
    seen = []

    date_extractor.extract_document_date(
        str(pdf),
        include_date=False,
        skip_ocr=True,
        page_progress_callback=lambda idx, total: seen.append((idx, total)),
    )

    assert seen == [(0, 3), (1, 3), (2, 3)]


# --------------------------------------------- single PDF in the input folder


def test_single_pdf_in_folder_completes_through_pipeline(tmp_path):
    """Regression for \"stuck at Processing 1/1\" with one PDF in the folder.

    This drives the real worker (not a stub) through PipelineThread.run() and
    asserts the run finishes and reports Complete.
    """
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    import ui.widgets as widgets

    input_root = tmp_path / "input"
    output_root = tmp_path / "out"
    flagged_root = tmp_path / "flagged"
    input_root.mkdir()
    flagged_root.mkdir()
    pdf = input_root / "only.pdf"
    _make_pdf(pdf, pages=3)

    config = _worker_config(tmp_path, rotation=False)
    config.update({
        "input_root": str(input_root),
        "output_root": str(output_root),
        "flagged_root": str(flagged_root),
    })

    thread = widgets.PipelineThread(config, 0)
    finished = []
    progress_msgs = []
    thread.finished_signal.connect(lambda flagged: finished.append(flagged))
    thread.progress.connect(lambda msg, pct: progress_msgs.append((msg, pct)))

    start = time.time()
    thread.run()
    elapsed = time.time() - start

    assert finished, "pipeline never finished for a single-PDF folder"
    assert any(msg == "Complete!" for msg, _ in progress_msgs), progress_msgs
    assert elapsed < 60, f"single 3-page PDF took {elapsed:.1f}s"
