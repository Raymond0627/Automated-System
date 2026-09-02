import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QLabel, QStatusBar,
    QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR
from .styles import DARK_THEME
from .dashboard_tab import DashboardTab
from .review_tab import ReviewTab
from .settings_tab import SettingsTab
from session import set_scan_info


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_path = str(BASE_DIR / "config.json")
        self.config = self._load_config()
        self._setup_ui()
        self._check_first_run()

    def _setup_ui(self):
        self.setWindowTitle("Lumeed QScan")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

        icon_path = BASE_DIR / "Lumeed Logo.png"
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            self.setWindowIcon(icon)
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.setWindowIcon(icon)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet(DARK_THEME)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.dashboard_tab = DashboardTab(self.config)
        self.review_tab = ReviewTab(self.config)
        self.settings_tab = SettingsTab(self.config)

        self.dashboard_tab.pipeline_started.connect(self.review_tab.clear_all)
        self.dashboard_tab.doc_update.connect(self.review_tab.add_doc)
        self.dashboard_tab.state_changed.connect(self._sync_scan_to_session)
        self.review_tab.session_restored.connect(self._on_session_restored)

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.review_tab, "Review")
        self.tabs.addTab(self.settings_tab, "Settings")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

        self.statusBar().showMessage("Ready")

    def _load_config(self) -> dict:
        defaults = {
            "input_root": "",
            "output_root": "",
            "flagged_root": "",
            "confidence_threshold": 85,
            "page_index": 0,
            "earliest_year": 1950,
            "ocr_engine": "tesseract",
            "theme": "Dark Blue",
            "enable_qc": True,
            "enable_docsep_removal": True,
            "enable_blank_removal": True,
            "qc_blank_threshold": 1.5,
            "qc_rotation_threshold": 65,
            "qc_mirror_threshold": 15,
            "rename_enabled": False,
            "audit_enabled": True,
            "render_dpi": 200,
            "max_workers": 4,
            "gpu_mode": "cpu",
            "remote_gpu_url": "",
            "enhance_enabled": True,
            "enhance_dpi": 0,
            "enhance_sat_threshold": 15,
            "enhance_color_pct": 2.0,
            "enhance_bw_clahe_clip": 2.0,
            "enhance_bw_clahe_tile": 8,
            "enhance_denoise_strength": 5,
            "enhance_denoise_template": 7,
            "enhance_denoise_search": 21,
            "enhance_sharpen_amount": 1.0,
            "enhance_sharpen_blur": 1.0,
            "enhance_white_point": 98.0,
            "enhance_color_clahe_clip": 2.0,
            "enhance_color_clahe_tile": 8,
        }
        if Path(self.config_path).exists():
            try:
                with open(self.config_path, "r") as f:
                    defaults.update(json.load(f))
            except Exception:
                pass
        return defaults

    def _on_tab_changed(self, index):
        self.statusBar().showMessage(f"Tab: {self.tabs.tabText(index)}")
        tab_name = self.tabs.tabText(index)
        if tab_name == "Review":
            self.review_tab.refresh_completer()

    def _check_first_run(self):
        input_root = self.config.get("input_root", "")
        flagged_root = self.config.get("flagged_root", "")
        if not input_root and not flagged_root:
            QMessageBox.information(
                self,
                "Welcome — First Run Setup",
                "Lumeed QScan needs file paths configured before use.\n\n"
                "Please set your Input Root and Flagged Root folders\n"
                "on the Dashboard tab, then click Save.",
            )
            self.tabs.setCurrentIndex(0)
        else:
            self.tabs.setCurrentIndex(1)

    def _sync_scan_to_session(self):
        state = self.dashboard_tab.get_scan_state()
        if state and state.get("plan"):
            set_scan_info(state)
            self.review_tab._schedule_session_save()
        else:
            set_scan_info(None)

    def _on_session_restored(self, snap):
        scan = (snap or {}).get("scan")
        self.dashboard_tab.resume_scan(scan, self.review_tab)
