"""End-to-end review-window behaviour for PipelineThread.

Processing must stop at the window size while the reviewer works and refill
automatically once the review queue drains.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ui.widgets as widgets
from ui.widgets import BackpressureGate, PipelineThread


def _app():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _ReviewQueueSim:
    """Stands in for the Review tab: every processed doc joins the backlog."""

    def __init__(self, gate):
        self.gate = gate
        self.lock = threading.Lock()
        self.completed = []

    def worker(self, info, config, render_dpi):
        with self.lock:
            self.completed.append(info["original_path"])
            self.gate.set_backlog(len(self.completed))
        return {
            "status": "flagged",
            "flagged_data": {
                "original_path": info["original_path"],
                "original_filename": info["original_filename"],
                "division_code": info.get("division_code", ""),
                "company_name": "Acme",
            },
            "passed_data": None,
            "log_messages": [],
            "blank_pages": [],
            "docsep_pages": [],
        }


def _make_config(tmp_path):
    input_root = tmp_path / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    plan = []
    for idx in range(6):
        pdf = input_root / f"doc_{idx}.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        plan.append(str(pdf))
    config = {
        "input_root": str(input_root),
        "output_root": str(tmp_path / "output"),
        "flagged_root": str(tmp_path / "flagged"),
        "confidence_threshold": 70,
        "page_index": 0,
        "earliest_year": 1990,
        "ocr_engine": "tesseract",
        "render_dpi": 72,
        "max_workers": 2,
        "batch_size": 10,
    }
    return config, plan


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_pipeline_pauses_at_window_and_resumes_on_drain(tmp_path, monkeypatch):
    _app()
    config, plan = _make_config(tmp_path)
    gate = BackpressureGate(high=3, low=1)
    sim = _ReviewQueueSim(gate)
    monkeypatch.setattr(widgets, "_process_doc_worker", sim.worker)

    thread = PipelineThread(config, 0, plan=plan, gate=gate)
    runner = threading.Thread(target=thread.run, daemon=True)
    runner.start()

    assert _wait_until(lambda: len(sim.completed) >= 3), "first window never filled"
    time.sleep(0.4)
    assert len(sim.completed) == 3, "pipeline submitted past the review window"

    stop = threading.Event()

    def drain():
        while not stop.is_set():
            gate.set_backlog(0)
            time.sleep(0.02)

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    try:
        runner.join(timeout=10)
    finally:
        stop.set()

    assert not runner.is_alive(), "pipeline did not finish after the queue drained"
    assert len(sim.completed) == 6


def test_cancel_while_paused_exits_cleanly(tmp_path, monkeypatch):
    _app()
    config, plan = _make_config(tmp_path)
    gate = BackpressureGate(high=2, low=1)
    sim = _ReviewQueueSim(gate)
    monkeypatch.setattr(widgets, "_process_doc_worker", sim.worker)

    thread = PipelineThread(config, 0, plan=plan, gate=gate)
    runner = threading.Thread(target=thread.run, daemon=True)
    runner.start()

    assert _wait_until(lambda: len(sim.completed) >= 2), "first window never filled"
    time.sleep(0.3)
    assert len(sim.completed) == 2

    thread.cancel()
    runner.join(timeout=5)
    assert not runner.is_alive(), "pipeline did not exit after cancel while paused"
