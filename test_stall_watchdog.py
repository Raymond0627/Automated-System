"""A document that stops making progress must not stall the whole folder."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ui.widgets as widgets
from ui.widgets import PipelineThread

_APP = None


def _app():
    global _APP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "flagged_root": "",
        "division_code": "003",
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
        "max_workers": 1,
        "batch_size": 50,
        "review_window_high": 50,
        "review_window_low": 10,
        "output_layout": "flat",
        "doc_stall_timeout_sec": 0.5,
    }


def _result(info: dict) -> dict:
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


def test_hung_document_is_skipped_and_remaining_docs_finish(tmp_path, monkeypatch):
    _app()
    plan = [str(tmp_path / f"doc_{idx}.pdf") for idx in range(3)]
    release = threading.Event()

    def worker(info, cfg, render_dpi):
        info["_started"] = True
        info["_last_activity"] = time.time()
        if info["original_filename"] == "doc_1.pdf":
            release.wait(10)  # never reports activity; watchdog must give up
        return _result(info)

    monkeypatch.setattr(widgets, "_process_doc_worker", worker)
    thread = PipelineThread(_config(tmp_path), 0, plan=plan, existing_paths=set())
    completed = []
    finished = []
    thread.doc_completed.connect(
        lambda kind, path: completed.append(path) if kind == "doc" else None
    )
    thread.finished_signal.connect(lambda flagged: finished.append(flagged))
    runner = threading.Thread(target=thread.run, daemon=True)
    try:
        runner.start()
        deadline = time.time() + 10
        while time.time() < deadline and not finished:
            _app().processEvents()
            time.sleep(0.01)
        _app().processEvents()
        runner.join(timeout=5)
        assert finished, "pipeline never finished after a hung document"
        assert not runner.is_alive()
        assert sorted(completed) == sorted(plan)
    finally:
        release.set()
        runner.join(timeout=5)
