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
            "confidence_threshold": 20,
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
            "rename_enabled": True,
            "audit_enabled": True,
            "render_dpi": 150,
            "max_workers": 4,
            "gpu_mode": "cpu",
            "remote_gpu_url": "",
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
