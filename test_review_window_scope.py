"""Regression tests for scoping the review window to the current scan.

The 1.9.7 backpressure gate could be pre-filled by documents left in the
Review tab by an earlier folder/session.  A small follow-up run (2-6 PDFs)
then started with zero/partial capacity, so it looked permanently "stuck on
process".  The window must only count documents that belong to the run that
is currently processed.
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

import fitz

import ui.widgets as widgets
from ui.dashboard_tab import DashboardTab
from ui.review_tab import ReviewTab
from ui.widgets import BackpressureGate

_APP = None


def _app():
    """Keep a module-level reference: a temporary QApplication is GC'd and
    constructing widgets afterwards aborts the process under pytest."""
    global _APP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _make_pdfs(root: Path, count: int, prefix: str = "DOC") -> list:
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        doc = fitz.open()
        doc.new_page()
        path = root / f"{prefix}_{index:03d}.pdf"
        doc.save(str(path))
        doc.close()
        paths.append(str(path))
    return paths


def _fake_worker(info, config, render_dpi):
    return {
        "status": "confirmed",
        "passed_data": {
            "original_path": info["original_path"],
            "original_filename": info["original_filename"],
            "division_code": info.get("division_code", ""),
            "company_name": "Acme",
            "company_confidence": 100,
            "detected_date": "2020-01-01",
            "method": "auto",
        },
        "flagged_data": None,
        "log_messages": [],
        "blank_pages": [],
        "docsep_pages": [],
    }


def _config(input_root: Path, base: Path) -> dict:
    return {
        "input_root": str(input_root),
        "output_root": str(base / "output"),
        "flagged_root": "",
        "division_code": "155",
        "confidence_threshold": 85,
        "company_confidence_threshold": 90,
        "page_index": 0,
        "earliest_year": 1950,
        "ocr_engine": "tesseract",
        "theme": "Light Mode",
        "enable_qc": False,
        "enable_docsep_removal": False,
        "enable_blank_removal": False,
        "qc_rotation_enabled": False,
        "rename_enabled": False,
        "document_date_enabled": False,
        "audit_enabled": False,
        "render_dpi": 72,
        "max_workers": 2,
        "batch_size": 50,
        "review_window_high": 50,
        "review_window_low": 10,
        "output_layout": "flat",
    }


def _stale_doc(base: Path, index: int) -> dict:
    return {
        "original_path": str(base / "old_folder" / f"OLD_{index:03d}.pdf"),
        "original_filename": f"OLD_{index:03d}.pdf",
        "division_code": "155",
        "company_name": "Old",
        "company_confidence": 100,
        "method": "auto",
    }


def test_gate_counts_only_documents_from_current_plan(tmp_path):
    _app()
    input_root = tmp_path / "input"
    plan = _make_pdfs(input_root, 2)
    dashboard = DashboardTab(_config(input_root, tmp_path))
    review = ReviewTab(_config(input_root, tmp_path))
    dashboard.review_tab = review
    dashboard.pipeline_thread = None
    dashboard._scan_plan = list(plan)
    dashboard._review_gate = BackpressureGate(high=3, low=1)

    review.auto_confirmed_docs = [
        {"original_path": plan[0], "original_filename": "DOC_000.pdf"},
        _stale_doc(tmp_path, 0),
        _stale_doc(tmp_path, 1),
        _stale_doc(tmp_path, 2),
    ]
    dashboard._sync_review_gate()
    assert dashboard._review_gate.backlog == 1
    assert not dashboard._review_gate.paused

    review.auto_confirmed_docs.append(
        {"original_path": plan[1], "original_filename": "DOC_001.pdf"}
    )
    dashboard._sync_review_gate()
    assert dashboard._review_gate.backlog == 2


def test_small_run_completes_despite_stale_review_backlog(tmp_path, monkeypatch):
    _app()
    input_root = tmp_path / "input"
    _make_pdfs(input_root, 6)
    monkeypatch.setattr(widgets, "_process_doc_worker", _fake_worker)

    dashboard = DashboardTab(_config(input_root, tmp_path))
    review = ReviewTab(_config(input_root, tmp_path))
    dashboard.review_tab = review
    dashboard.doc_update.connect(review.add_doc)
    # Deliberately do NOT connect pipeline_started to clear_all: this keeps a
    # pre-existing queue in place, like a restored session or a follow-up run
    # started from a previous folder's review state.
    dashboard.pipeline_thread = None

    for index in range(50):
        review.add_doc("passed", _stale_doc(tmp_path, index))

    state = {"finished": False}
    dashboard.pipeline_finished.connect(lambda: state.update(finished=True))
    dashboard._start_pipeline()

    deadline = time.time() + 15
    while time.time() < deadline and not state["finished"]:
        _app().processEvents()
        time.sleep(0.01)
    _app().processEvents()

    assert state["finished"], "small run stayed blocked by stale review docs"
    assert not dashboard.pipeline_thread.isRunning()
    assert len(review.auto_confirmed_docs) == 56


def test_new_run_ignores_docs_until_its_plan_arrives(tmp_path):
    _app()
    input_root = tmp_path / "input"
    plan = _make_pdfs(input_root, 1)
    dashboard = DashboardTab(_config(input_root, tmp_path))
    dashboard.review_tab = ReviewTab(_config(input_root, tmp_path))
    dashboard._scan_plan = list(plan)
    dashboard._accepting_docs = False

    forwarded = []
    dashboard.doc_update.connect(lambda kind, data: forwarded.append(data))
    stale = _stale_doc(tmp_path, 0)
    dashboard._on_doc_processed("passed", stale)
    dashboard._on_doc_completed("doc", stale["original_path"])
    assert forwarded == []
    assert dashboard._processed_paths == set()

    dashboard._accepting_docs = True
    dashboard._on_doc_processed("passed", stale)
    assert forwarded == [stale]


def test_run_button_is_available_while_paused(tmp_path):
    _app()
    input_root = tmp_path / "input"
    plan = _make_pdfs(input_root, 2)
    dashboard = DashboardTab(_config(input_root, tmp_path))
    review = ReviewTab(_config(input_root, tmp_path))
    dashboard.review_tab = review
    dashboard._scan_plan = list(plan)
    dashboard._review_gate = BackpressureGate(high=2, low=1)

    class _RunningThread:
        def isRunning(self):
            return True

    dashboard.pipeline_thread = _RunningThread()
    review.pending_docs = [
        {"original_path": plan[0], "original_filename": "DOC_000.pdf"},
        {"original_path": plan[1], "original_filename": "DOC_001.pdf"},
    ]
    dashboard._sync_review_gate()

    assert dashboard._review_gate.paused
    assert dashboard.run_btn.isEnabled()
    assert "Start New Run" in dashboard.run_btn.text()
