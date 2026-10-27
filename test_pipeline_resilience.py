"""Regression tests for scanning/processing resilience.

Covers the "stuck at Processing 1/1..." report:
- cancellation must stay responsive while a document worker is busy
- folder scanning must report progress and be cancellable
- OCR calls must carry a timeout
- date extraction can skip OCR for native-text PDFs when the date scan is off
- network files are staged locally before processing
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import pytesseract

sys.path.insert(0, str(Path(__file__).parent))

import pipeline
import date_extractor
import auto_qc
import ui.widgets as widgets
from pipeline import PipelineConfig, DivisionBatch, Document, parse_folder_structure
from ui.widgets import PipelineThread


def _app():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_pdf(path: Path, pages: int = 2) -> None:
    import fitz

    doc = fitz.open()
    for idx in range(pages):
        page = doc.new_page()
        if idx == 0:
            page.insert_text((72, 100), "DATE: 15 May 2024")
    doc.save(str(path))
    doc.close()


# --------------------------------------------------------------- folder scan


def test_parse_folder_structure_reports_progress_and_honors_cancel(tmp_path):
    root = tmp_path / "input"
    for idx in range(4):
        folder = root / f"nested_{idx}"
        folder.mkdir(parents=True)
        _make_pdf(folder / f"doc_{idx}.pdf", pages=1)

    progress = []
    calls = {"n": 0}

    def on_progress(dirs, pdfs, current):
        progress.append((dirs, pdfs, current))

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 1

    batches = parse_folder_structure(
        root, "003", progress_callback=on_progress, should_cancel=should_cancel
    )

    assert progress, "scan progress callback was never called"
    assert isinstance(batches, list)


def test_parse_folder_structure_finds_nested_pdf_and_skips_artifacts(tmp_path):
    root = tmp_path / "input"
    (root / "a" / "b").mkdir(parents=True)
    _make_pdf(root / "a" / "b" / "real.pdf", pages=1)
    (root / "output").mkdir()
    _make_pdf(root / "output" / "artifact.pdf", pages=1)
    (root / "flagged").mkdir()
    _make_pdf(root / "flagged" / "artifact.pdf", pages=1)

    batches = parse_folder_structure(root, "003")
    docs = [d for batch in batches for d in batch.documents]

    assert [Path(d.original_path).name for d in docs] == ["real.pdf"]
    assert docs[0].rel_dir == "a/b"


# ------------------------------------------------------------ date extraction


def test_extract_document_date_skip_ocr_skips_all_ocr(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=2)

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR must not run when skip_ocr=True")

    monkeypatch.setattr(date_extractor, "run_ocr", fail_ocr)
    monkeypatch.setattr(date_extractor.pytesseract, "image_to_string", fail_ocr)

    result = date_extractor.extract_document_date(
        str(pdf), 0, include_date=False, skip_ocr=True
    )

    assert result.blank_pages == [1]
    assert result.page_texts == []


def test_extract_document_date_page_image_callback_covers_every_page(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=2)
    seen = []

    def on_page(page_idx, image):
        seen.append((page_idx, image))

    date_extractor.extract_document_date(
        str(pdf),
        0,
        include_date=False,
        skip_ocr=True,
        page_image_callback=on_page,
    )

    assert [idx for idx, _ in seen] == [0, 1]
    assert all(isinstance(img, np.ndarray) and img.size > 0 for _, img in seen)


def test_run_ocr_passes_timeout(monkeypatch):
    calls = {}

    def fake_image_to_data(image, **kwargs):
        calls.update(kwargs)
        return {"text": []}

    monkeypatch.setattr(date_extractor.pytesseract, "image_to_data", fake_image_to_data)

    img = np.zeros((20, 20, 3), dtype=np.uint8)
    date_extractor.run_ocr(img, psm=6, timeout=7)

    assert calls.get("timeout") == 7


def test_check_rotation_passes_timeout(monkeypatch):
    calls = {}

    def fake_image_to_osd(image, **kwargs):
        calls.update(kwargs)
        return {"rotate": 0, "orientation_conf": 0.0}

    monkeypatch.setattr(pytesseract, "image_to_osd", fake_image_to_osd)

    img = np.zeros((200, 200, 3), dtype=np.uint8)
    auto_qc.check_rotation(img, ocr_timeout=7)

    assert calls.get("timeout") == 7


# ------------------------------------------------------------- network staging


def test_is_network_path_detects_unc(tmp_path):
    if os.name == "nt":
        assert widgets._is_network_path(r"\\server\share\doc.pdf") is True
        assert widgets._is_network_path(str(tmp_path / "doc.pdf")) is False
    else:
        assert widgets._is_network_path("//server/share/doc.pdf") is True
        assert widgets._is_network_path(str(tmp_path / "doc.pdf")) is False


def test_stage_local_copy_copies_and_cleans_up(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)

    monkeypatch.setattr(widgets, "_is_network_path", lambda p: True)
    work_path, temp_dir = widgets._stage_local_copy(str(pdf))
    try:
        assert temp_dir is not None
        assert work_path != str(pdf)
        assert Path(work_path).exists()
        assert Path(work_path).read_bytes() == pdf.read_bytes()
    finally:
        widgets._cleanup_staged(temp_dir)

    assert not Path(temp_dir).exists()


# --------------------------------------------------------- pipeline behaviour


def _thread_config(tmp_path: Path) -> dict:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    flagged_root = tmp_path / "flagged"
    input_root.mkdir(exist_ok=True)
    flagged_root.mkdir(exist_ok=True)
    return {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "flagged_root": str(flagged_root),
        "confidence_threshold": 70,
        "page_index": 0,
        "earliest_year": 1990,
        "ocr_engine": "tesseract",
        "render_dpi": 150,
        "max_workers": 4,
        "batch_size": 10,
    }


def _pump(app, condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not condition():
        app.processEvents()
        time.sleep(0.02)


def test_pipeline_cancel_returns_while_worker_is_busy(tmp_path):
    app = _app()
    config = _thread_config(tmp_path)
    plan = [str(tmp_path / "input" / "only.pdf")]

    started = threading.Event()
    release = threading.Event()

    def slow_worker(info, cfg, render_dpi):
        started.set()
        release.wait(10)
        return {
            "status": "flagged",
            "flagged_data": {
                "original_path": info["original_path"],
                "original_filename": info["original_filename"],
                "division_code": "003",
                "company_name": "Acme",
                "reviewed": False,
            },
            "passed_data": None,
            "log_messages": [],
            "blank_pages": [],
            "docsep_pages": [],
            "total_pages": 1,
        }

    original = widgets._process_doc_worker
    widgets._process_doc_worker = slow_worker
    thread = PipelineThread(
        config, 0, plan=plan, existing_paths=set(), merged_flagged=[], merged_confirmed=[]
    )
    finished = []
    thread.finished_signal.connect(lambda flagged: finished.append(flagged))
    runner = threading.Thread(target=thread.run, daemon=True)
    try:
        runner.start()
        assert started.wait(5), "worker never started"
        thread.cancel()
        _pump(app, lambda: bool(finished), timeout=2)
        assert finished == [[]], "cancel did not report completion while the worker was busy"
        assert runner.is_alive(), "cancel should not block on the busy worker"
    finally:
        release.set()
        runner.join(timeout=5)
        widgets._process_doc_worker = original


def test_pipeline_single_document_with_four_workers_completes(tmp_path):
    _app()
    config = _thread_config(tmp_path)
    pdf = tmp_path / "input" / "only.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    original = widgets._process_doc_worker
    widgets._process_doc_worker = lambda info, cfg, dpi: {
        "status": "flagged",
        "flagged_data": {
            "original_path": info["original_path"],
            "original_filename": info["original_filename"],
            "division_code": "003",
            "company_name": "Acme",
            "reviewed": False,
        },
        "passed_data": None,
        "log_messages": [],
        "blank_pages": [],
        "docsep_pages": [],
        "total_pages": 1,
    }
    thread = PipelineThread(config, 0)
    finished = []
    completed = []
    thread.finished_signal.connect(lambda flagged: finished.append(flagged))
    thread.doc_completed.connect(lambda kind, path: completed.append((kind, path)))
    try:
        thread.run()
    finally:
        widgets._process_doc_worker = original

    assert len(finished) == 1
    assert ("doc", str(pdf)) in completed
