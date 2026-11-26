"""Single Leverage setting: 20-100, fixed refill at 10, live auto-save."""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

import ui.settings_tab as settings_module
from review_window import (
    LEVERAGE_DEFAULT, LEVERAGE_MAX, LEVERAGE_MIN, REFILL_AT, clamp_leverage,
    normalize_review_window,
)
from ui.dashboard_tab import DashboardTab
from ui.settings_tab import SettingsTab
from ui.widgets import BackpressureGate

_APP = None


def _app():
    global _APP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _config(**overrides) -> dict:
    config = {
        "input_root": "",
        "output_root": "",
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
        "max_workers": 1,
        "batch_size": 50,
        "review_window_high": 50,
        "review_window_low": 10,
        "output_layout": "flat",
    }
    config.update(overrides)
    return config


def test_leverage_is_clamped_to_20_100():
    assert clamp_leverage(0) == LEVERAGE_MIN
    assert clamp_leverage(19) == LEVERAGE_MIN
    assert clamp_leverage(20) == 20
    assert clamp_leverage(75) == 75
    assert clamp_leverage(100) == 100
    assert clamp_leverage(500) == LEVERAGE_MAX
    assert clamp_leverage(None) == LEVERAGE_DEFAULT
    assert clamp_leverage("30") == 30


def test_normalize_forces_refill_at_ten():
    config = {"review_window_high": 500, "review_window_low": 0}
    high = normalize_review_window(config)
    assert high == LEVERAGE_MAX
    assert config["review_window_high"] == LEVERAGE_MAX
    assert config["review_window_low"] == REFILL_AT == 10


def test_settings_has_one_auto_saving_leverage_control(tmp_path, monkeypatch):
    _app()
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_module, "ensure_data_dir", lambda: tmp_path)
    config = _config(review_window_high=500, review_window_low=0)
    tab = SettingsTab(config)

    assert tab.review_high_spin.spin.minimum() == LEVERAGE_MIN
    assert tab.review_high_spin.spin.maximum() == LEVERAGE_MAX
    assert tab.review_high_spin.value() == LEVERAGE_MAX
    assert config["review_window_low"] == REFILL_AT
    assert not hasattr(tab, "review_low_spin")

    emitted = []
    tab.review_leverage_changed.connect(emitted.append)
    tab.review_high_spin.setValue(30)

    assert config["review_window_high"] == 30
    assert config["review_window_low"] == REFILL_AT
    assert emitted == [30]
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["review_window_high"] == 30
    assert saved["review_window_low"] == 10


def test_leverage_change_applies_to_running_gate_immediately():
    _app()
    dashboard = DashboardTab(_config())

    class _Review:
        def __init__(self, count):
            self.pending_docs = [{"original_path": f"doc_{i}.pdf"} for i in range(count)]
            self.auto_confirmed_docs = []

    review = _Review(50)
    dashboard.review_tab = review
    dashboard._review_gate = BackpressureGate(high=50, low=10)
    dashboard._review_gate.set_backlog(50)
    assert dashboard._review_gate.paused

    dashboard.set_review_leverage(80)
    assert dashboard._review_gate.high == 80
    assert not dashboard._review_gate.paused
    assert dashboard.config["review_window_high"] == 80

    review.pending_docs = [{"original_path": f"doc_{i}.pdf"} for i in range(80)]
    dashboard._sync_review_gate()
    assert dashboard._review_gate.paused

    dashboard.set_review_leverage(30)
    assert dashboard._review_gate.high == 30
    assert dashboard._review_gate.paused
    assert dashboard.config["review_window_high"] == 30
    assert dashboard.config["review_window_low"] == REFILL_AT
