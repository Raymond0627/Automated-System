import json
import sys
import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QLabel, QStatusBar,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR, DATA_DIR, ensure_data_dir
from .styles import get_theme_stylesheet, get_theme_palette, get_palette_dict, THEME_DARK, THEME_LIGHT
from .dashboard_tab import DashboardTab
from .review_tab import ReviewTab
from .settings_tab import SettingsTab
from session import set_scan_info


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_data_dir()
        self.config_path = str(DATA_DIR / "config.json")
        self._seed_config_if_needed()
        self.config = self._load_config()
        self._startup_checks_done = False
        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._startup_checks_done:
            self._startup_checks_done = True
            self.tabs.setCurrentIndex(0)
            # Defer popups so the main window and dashboard are fully rendered and painted on screen first
            QTimer.singleShot(350, self._run_startup_checks)

    def _run_startup_checks(self):
        # 1. First check if an unsaved session exists to restore
        restored = self.review_tab._restore_session_if_present()
        # 2. If no session was restored, check if first run setup is needed
        if not restored:
            self._check_first_run()
        if self.config.get("output_root"):
            self.review_tab._start_reviewed_scan()

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
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.dashboard_tab = DashboardTab(self.config)
        self.review_tab = ReviewTab(self.config)
        self.settings_tab = SettingsTab(self.config)

        self.settings_tab.theme_changed.connect(self.apply_theme)

        self.dashboard_tab.pipeline_started.connect(self.review_tab.clear_all)
        self.dashboard_tab.doc_update.connect(self.review_tab.add_doc)
        self.dashboard_tab.pipeline_finished.connect(self._on_pipeline_finished)
        self.dashboard_tab.state_changed.connect(self._sync_scan_to_session)
        self.review_tab.session_restored.connect(self._on_session_restored)

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.review_tab, "Review")
        self.tabs.addTab(self.settings_tab, "Settings")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

        self.apply_theme(self.config.get("theme", THEME_LIGHT))

        self.statusBar().showMessage("Ready")

    def apply_theme(self, theme_name: str):
        self.config["theme"] = theme_name
        stylesheet = get_theme_stylesheet(theme_name)
        palette = get_theme_palette(theme_name)

        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.setStyleSheet(stylesheet)
            app.setPalette(palette)
        else:
            self.setStyleSheet(stylesheet)
            self.setPalette(palette)

        self.tabs.setStyleSheet(stylesheet)

        if hasattr(self, "dashboard_tab"):
            self.dashboard_tab.apply_theme(theme_name)
        if hasattr(self, "review_tab"):
            self.review_tab.apply_theme(theme_name)
        if hasattr(self, "settings_tab"):
            self.settings_tab.apply_theme(theme_name)
        if self.statusBar():
            p = get_palette_dict(theme_name)
            self.statusBar().setStyleSheet(f"background-color: {p['bg_window']}; color: {p['text_secondary']}; font-weight: 500; border-top: 1px solid {p['border']};")

    def _seed_config_if_needed(self):
        data_cfg = Path(self.config_path)
        resource_cfg = BASE_DIR / "config.json"
        if not resource_cfg.exists():
            return
        if not data_cfg.exists():
            try:
                data_cfg.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resource_cfg, data_cfg)
            except Exception:
                pass
            return
        # Merge missing keys from the resource config into an existing user
        # config so new sections (e.g. added providers) appear without
        # overwriting saved user values.
        try:
            with open(resource_cfg, "r", encoding="utf-8") as f:
                defaults = json.load(f)
            with open(data_cfg, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            changed = False
            for key, value in defaults.items():
                if key not in user_cfg:
                    user_cfg[key] = value
                    changed = True
                elif isinstance(value, dict) and isinstance(user_cfg[key], dict):
                    for sub_key, sub_val in value.items():
                        if sub_key not in user_cfg[key]:
                            user_cfg[key][sub_key] = sub_val
                            changed = True
            if changed:
                with open(data_cfg, "w", encoding="utf-8") as f:
                    json.dump(user_cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_config(self) -> dict:
        defaults = {
            "input_root": "",
            "output_root": "",
            "flagged_root": "",
            "division_code": "",
            "first_run_completed": True,
            "confidence_threshold": 85,
            "page_index": 0,
            "earliest_year": 1950,
            "ocr_engine": "tesseract",
            "theme": "Light Mode",
            "enable_qc": True,
            "enable_docsep_removal": True,
            "enable_blank_removal": True,
            "qc_blank_threshold": 1.5,
            "qc_rotation_threshold": 65,
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
            "auto_save_enabled": False,
            "batch_size": 50,
            "output_layout": "mirror",
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
        if not self.config.get("first_run_completed", False):
            self.config["first_run_completed"] = True
            self.tabs.setCurrentIndex(0)
            QMessageBox.information(
                self,
                "Welcome — First Run Setup",
                "Lumeed QScan needs file paths configured before use.\n\n"
                "Please set your Input Root and Output Root folders\n"
                "on the Dashboard tab to begin.",
            )
            # Persist that first run has been acknowledged
            self.dashboard_tab._save_config()
        self.tabs.setCurrentIndex(0)

    def closeEvent(self, event):
        # Guarantee unsaved session is safely persisted before closing
        try:
            if hasattr(self, "review_tab"):
                self.review_tab._save_session_now()
        except Exception:
            pass
        super().closeEvent(event)

    def _on_pipeline_finished(self):
        if hasattr(self, "review_tab"):
            self.review_tab._start_reviewed_scan()
            if self.review_tab.active_tab != "reviewed":
                self.review_tab._on_auto_refresh()

    def _sync_scan_to_session(self):
        state = self.dashboard_tab.get_scan_state()
        if state and state.get("plan"):
            set_scan_info(state)
            self.review_tab._schedule_session_save()
        else:
            set_scan_info(None)
            self.review_tab._schedule_session_save()

    def _on_session_restored(self, snap):
        dashboard_data = (snap or {}).get("dashboard", {})
        if dashboard_data and hasattr(self, "dashboard_tab"):
            in_root = dashboard_data.get("input_root", "")
            out_root = dashboard_data.get("output_root", "")
            self.dashboard_tab.input_var.setText(in_root)
            self.dashboard_tab.output_var.setText(out_root)
            self.config["input_root"] = in_root
            self.config["output_root"] = out_root
            self.config["flagged_root"] = dashboard_data.get("flagged_root", "")
        scan = (snap or {}).get("scan")
        if scan and ((scan.get("plan") or scan.get("processed"))):
            self.dashboard_tab.resume_scan(scan, self.review_tab)
