"""Minimal update indicator in the Dashboard header."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from ui.dashboard_tab import DashboardTab
from ui.widgets import UpdateIndicatorButton

_APP = None


def _app():
    global _APP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _config(tmp_path: Path) -> dict:
    return {
        "input_root": "",
        "output_root": str(tmp_path / "out"),
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


def _glyph_color(button):
    button.repaint()
    image = button.grab().toImage()
    # Sample the vertical shaft (center, upper half).
    return image.pixelColor(button.width() // 2, 12)


def test_update_button_turns_red_only_when_update_available(tmp_path):
    _app()
    button = UpdateIndicatorButton(theme="Light Mode")
    button.set_available(False)
    idle = _glyph_color(button)
    assert idle.red() < 120 and idle.green() < 120

    button.set_available(True, "v9.9.9")
    active = _glyph_color(button)
    assert active.red() > 150
    assert active.red() > active.green() + 60
    assert "v9.9.9" in button.toolTip()


def test_dashboard_exposes_update_state_and_click_signal(tmp_path):
    _app()
    dashboard = DashboardTab(_config(tmp_path))
    assert dashboard.update_btn is not None
    assert not dashboard.update_btn._available

    dashboard.set_update_available(True, "v2.0.0")
    assert dashboard.update_btn._available
    assert "v2.0.0" in dashboard.update_btn.toolTip()

    dashboard.set_update_available(False)
    assert not dashboard.update_btn._available

    clicks = []
    dashboard.update_clicked.connect(lambda: clicks.append(True))
    dashboard.update_btn.click()
    assert clicks == [True]
