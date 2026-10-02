import os
import sys
import json
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPixmap

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR, DATA_DIR, ensure_data_dir
from .widgets import PipelineThread
from .styles import (
    get_palette_dict, get_stat_card_style, get_dashboard_card_style,
    get_badge_pill_style, THEME_DARK
)


class StatCard(QFrame):
    """Modern 2026 KPI metric card with uppercase label, bold value, and accent border."""

    def __init__(self, title: str, initial_value: str = "0", accent_color: str = "#4a6fa5", theme: str = THEME_DARK, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.accent_color = accent_color
        self.setObjectName("stat_card")
        self.setFixedHeight(82)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.title_label = QLabel(title.upper())
        self.title_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))

        self.value_label = QLabel(initial_value)
        self.value_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        self.apply_theme(theme)

    def set_value(self, value):
        self.value_label.setText(str(value))

    def apply_theme(self, theme: str):
        self.theme = theme
        p = get_palette_dict(theme)
        self.setStyleSheet(f"""
            QFrame#stat_card {{
                background-color: {p['bg_surface']};
                border: 1px solid {p['border']};
                border-radius: 10px;
            }}
            QFrame#stat_card:hover {{
                border: 1px solid {self.accent_color};
                background-color: {p['bg_card_hover']};
            }}
        """)
        self.title_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']}; letter-spacing: 0.5px;")
        self.value_label.setStyleSheet(f"background: transparent; color: {self.accent_color};")


class DashboardTab(QWidget):
    log_signal = pyqtSignal(str)
    pipeline_finished = pyqtSignal()
    doc_update = pyqtSignal(str, dict)
    pipeline_started = pyqtSignal()
    state_changed = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.pipeline_thread = None
        self._cancel_requested = False
        self._scan_plan = None
        self._processed_paths = set()

        # KPI metric counters
        self._stat_total = 0
        self._stat_auto = 0
        self._stat_flagged = 0
        self._stat_errors = 0

        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(18, 14, 18, 14)

        # -------------------------------------------------------------
        # 1. Header & Status Pill
        # -------------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(12)

        logo_path = BASE_DIR / "Lumeed Logo.png"
        if logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(pixmap.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            logo_label.setFixedSize(46, 46)
            header.addWidget(logo_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Lumeed QScan")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.title_label = title
        title_col.addWidget(title)

        subtitle = QLabel("PDF Auto-Rename & OCR Pipeline")
        subtitle.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        self.subtitle_label = subtitle
        title_col.addWidget(subtitle)
        header.addLayout(title_col)

        header.addStretch()

        # Modern System Status Badge Pill
        self.status_pill = QLabel("●  IDLE")
        self.status_pill.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.status_pill.setFixedHeight(28)
        header.addWidget(self.status_pill)

        main_layout.addLayout(header)

        # -------------------------------------------------------------
        # 2. KPI Summary Metric Cards (4 Tiles)
        # -------------------------------------------------------------
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)

        theme = self.config.get("theme", THEME_DARK)
        p = get_palette_dict(theme)

        self.kpi_total = StatCard("Total Scanned", "0", accent_color=p["accent_primary"], theme=theme)
        self.kpi_auto = StatCard("Auto-Confirmed", "0", accent_color=p["status_done"], theme=theme)
        self.kpi_flagged = StatCard("Needs Review", "0", accent_color=p["status_todo"], theme=theme)
        self.kpi_errors = StatCard("Errors / QC", "0", accent_color=p["status_bad"], theme=theme)

        kpi_row.addWidget(self.kpi_total)
        kpi_row.addWidget(self.kpi_auto)
        kpi_row.addWidget(self.kpi_flagged)
        kpi_row.addWidget(self.kpi_errors)
        main_layout.addLayout(kpi_row)

        # -------------------------------------------------------------
        # 3. Modern Folder Setup & Pipeline Control Card
        # -------------------------------------------------------------
        config_card = QFrame()
        config_card.setObjectName("dashboard_card")
        self.config_card = config_card
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(16, 14, 16, 14)
        config_layout.setSpacing(10)

        # Folder row grid
        folder_grid = QGridLayout()
        folder_grid.setSpacing(8)
        folder_grid.setContentsMargins(0, 0, 0, 0)

        self.input_label = QLabel("Input Folder:")
        self.input_label.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.input_label.setStyleSheet("background: transparent;")
        self.input_var = QLineEdit(self.config.get("input_root", ""))
        self.input_var.setFont(QFont("Segoe UI", 9))
        self.input_var.setStyleSheet(f"background-color: {p['bg_input']}; color: {p['text_main']}; border: 1px solid {p['border_input']}; border-radius: 6px; padding: 4px 10px;")
        btn_in = QPushButton("Browse")
        btn_in.setFixedSize(76, 28)
        btn_in.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn_in.clicked.connect(lambda: self._browse(self.input_var))
        self.input_var.textChanged.connect(self._sync_paths_from_widgets)

        folder_grid.addWidget(self.input_label, 0, 0)
        folder_grid.addWidget(self.input_var, 0, 1)
        folder_grid.addWidget(btn_in, 0, 2)

        self.output_label = QLabel("Output Folder:")
        self.output_label.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.output_label.setStyleSheet("background: transparent;")
        self.output_var = QLineEdit(self.config.get("output_root", ""))
        self.output_var.setFont(QFont("Segoe UI", 9))
        self.output_var.setStyleSheet(f"background-color: {p['bg_input']}; color: {p['text_main']}; border: 1px solid {p['border_input']}; border-radius: 6px; padding: 4px 10px;")
        btn_out = QPushButton("Browse")
        btn_out.setFixedSize(76, 28)
        btn_out.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn_out.clicked.connect(lambda: self._browse(self.output_var))
        self.output_var.textChanged.connect(self._sync_paths_from_widgets)

        folder_grid.addWidget(self.output_label, 1, 0)
        folder_grid.addWidget(self.output_var, 1, 1)
        folder_grid.addWidget(btn_out, 1, 2)

        config_layout.addLayout(folder_grid)

        # Buttons + Progress Row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.run_btn = QPushButton("▶  Run Pipeline")
        self.run_btn.setObjectName("accent")
        self.run_btn.setFixedHeight(32)
        self.run_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.run_btn.clicked.connect(self.run_pipeline)
        actions_row.addWidget(self.run_btn)

        self.scan_btn = QPushButton("⟳  Scan Folder")
        self.scan_btn.setFixedHeight(32)
        self.scan_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.scan_btn.clicked.connect(self.scan_folder)
        actions_row.addWidget(self.scan_btn)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_pipeline)
        actions_row.addWidget(self.cancel_btn)

        actions_row.addSpacing(8)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(28)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Ready")
        actions_row.addWidget(self.progress_bar, 1)

        config_layout.addLayout(actions_row)
        main_layout.addWidget(config_card)

        # -------------------------------------------------------------
        # 4. Activity Stream Card with Pill Badges
        # -------------------------------------------------------------
        log_card = QFrame()
        log_card.setObjectName("dashboard_card")
        self.log_card = log_card
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 12, 16, 14)
        log_layout.setSpacing(8)

        log_toolbar = QHBoxLayout()
        log_title = QLabel("Activity Stream")
        log_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.log_title = log_title
        log_toolbar.addWidget(log_title)

        self.scan_label = QLabel("")
        self.scan_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        log_toolbar.addWidget(self.scan_label)
        log_toolbar.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(64, 26)
        clear_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        clear_btn.clicked.connect(self._clear_log)
        log_toolbar.addWidget(clear_btn)
        log_layout.addLayout(log_toolbar)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(7)
        self.log_table.setHorizontalHeaderLabels(["Time", "Status", "File", "Detected Date", "Confidence", "Blank Pages", "QC"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.log_table.setShowGrid(False)
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setMinimumHeight(150)
        self.log_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {p['bg_surface']};
                alternate-background-color: {p['bg_card_hover']};
                border: 1px solid {p['border']};
                border-radius: 6px;
                gridline-color: transparent;
            }}
        """)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.setColumnWidth(5, 80)
        self.log_table.setColumnWidth(6, 90)
        log_layout.addWidget(self.log_table, 1)

        main_layout.addWidget(log_card, 1)

        self.log_signal.connect(self._append_log)
        self.apply_theme(self.config.get("theme", THEME_DARK))

    def _set_status_pill(self, state: str, text: str):
        theme = self.config.get("theme", THEME_DARK)
        p = get_palette_dict(theme)
        if state == "running":
            color = p["accent_primary"]
            bg = "#1e3a5f" if theme == THEME_DARK else "#dbeafe"
        elif state == "done":
            color = p["status_done"]
            bg = "#193822" if theme == THEME_DARK else "#dcfce7"
        elif state == "error":
            color = p["status_bad"]
            bg = "#3d1c1c" if theme == THEME_DARK else "#fee2e2"
        else:
            color = p["text_secondary"]
            bg = p["bg_input_alt"]

        self.status_pill.setText(f"●  {text.upper()}")
        self.status_pill.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {color};
                border-radius: 14px;
                padding: 4px 14px;
                border: 1px solid {color}40;
            }}
        """)

    def clear_fields(self):
        self.input_var.setText("")
        self.output_var.setText("")
        self.config["input_root"] = ""
        self.config["output_root"] = ""
        self.config["flagged_root"] = ""
        self.scan_label.setText("")
        self._clear_log()
        self._set_status_pill("idle", "IDLE")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")

    def _clear_log(self):
        self.log_table.setRowCount(0)
        self._stat_total = 0
        self._stat_auto = 0
        self._stat_flagged = 0
        self._stat_errors = 0
        self._update_kpi_cards()

    def _update_kpi_cards(self):
        self.kpi_total.set_value(self._stat_total)
        self.kpi_auto.set_value(self._stat_auto)
        self.kpi_flagged.set_value(self._stat_flagged)
        self.kpi_errors.set_value(self._stat_errors)

    def apply_theme(self, theme_name: str):
        self.config["theme"] = theme_name
        p = get_palette_dict(theme_name)

        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(f"color: {p['text_main']}; padding: 0;")
        if hasattr(self, "subtitle_label"):
            self.subtitle_label.setStyleSheet(f"color: {p['text_secondary']}; font-weight: 500;")
        if hasattr(self, "scan_label"):
            self.scan_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt; font-weight: 500;")
        if hasattr(self, "log_title"):
            self.log_title.setStyleSheet(f"color: {p['text_main']};")
        if hasattr(self, "input_label"):
            self.input_label.setStyleSheet(f"background: transparent; color: {p['text_main']};")
        if hasattr(self, "output_label"):
            self.output_label.setStyleSheet(f"background: transparent; color: {p['text_main']};")
        if hasattr(self, "input_var"):
            self.input_var.setStyleSheet(f"background-color: {p['bg_input']}; color: {p['text_main']}; border: 1px solid {p['border_input']}; border-radius: 6px; padding: 4px 10px;")
        if hasattr(self, "output_var"):
            self.output_var.setStyleSheet(f"background-color: {p['bg_input']}; color: {p['text_main']}; border: 1px solid {p['border_input']}; border-radius: 6px; padding: 4px 10px;")

        # Cards
        card_style = get_dashboard_card_style(theme_name)
        if hasattr(self, "config_card"):
            self.config_card.setStyleSheet(card_style)
        if hasattr(self, "log_card"):
            self.log_card.setStyleSheet(card_style)

        # KPI tiles
        if hasattr(self, "kpi_total"):
            self.kpi_total.apply_theme(theme_name)
        if hasattr(self, "kpi_auto"):
            self.kpi_auto.apply_theme(theme_name)
        if hasattr(self, "kpi_flagged"):
            self.kpi_flagged.apply_theme(theme_name)
        if hasattr(self, "kpi_errors"):
            self.kpi_errors.apply_theme(theme_name)

        # Update status pill styling
        if hasattr(self, "status_pill"):
            current_txt = self.status_pill.text().replace("●  ", "").strip()
            state = "running" if "RUNNING" in current_txt or "SCANNING" in current_txt else ("done" if "COMPLETED" in current_txt else "idle")
            self._set_status_pill(state, current_txt if current_txt else "IDLE")

        # Update existing table row labels & pills
        if hasattr(self, "log_table"):
            self.log_table.setStyleSheet(f"""
                QTableWidget {{
                    background-color: {p['bg_surface']};
                    alternate-background-color: {p['bg_card_hover']};
                    border: 1px solid {p['border']};
                    border-radius: 6px;
                    gridline-color: transparent;
                }}
            """)
            for row in range(self.log_table.rowCount()):
                t_item = self.log_table.item(row, 0)
                if t_item:
                    t_item.setForeground(QColor(p['text_secondary']))
                f_item = self.log_table.item(row, 2)
                if f_item:
                    f_item.setForeground(QColor(p['text_main']))
                c_item = self.log_table.item(row, 4)
                if c_item:
                    c_item.setForeground(QColor(p['text_secondary']))

                status_widget = self.log_table.cellWidget(row, 1)
                if status_widget:
                    status_widget.setStyleSheet("background: transparent; border: none;")
                    pill = status_widget.findChild(QLabel, "badge_pill")
                    st_text = status_widget.property("pill_text") or (pill.text() if pill else "")
                    fg, bg = self._get_pill_colors(st_text, theme_name)
                    if pill:
                        pill.setStyleSheet(f"background-color: {bg}; color: {fg}; border-radius: 9px; padding: 2px 10px; min-width: 50px; border: none;")

                qc_widget = self.log_table.cellWidget(row, 6)
                if qc_widget:
                    qc_widget.setStyleSheet("background: transparent; border: none;")
                    pill = qc_widget.findChild(QLabel, "badge_pill")
                    qc_text = qc_widget.property("pill_text") or (pill.text() if pill else "")
                    fg, bg = self._get_qc_pill_colors(qc_text, theme_name)
                    if pill:
                        pill.setStyleSheet(f"background-color: {bg}; color: {fg}; border-radius: 9px; padding: 2px 10px; min-width: 50px; border: none;")

    def _get_pill_colors(self, status: str, theme_name: str) -> tuple:
        p = get_palette_dict(theme_name)
        is_dark = (theme_name == THEME_DARK)
        if status == "AUTO":
            return (p["status_done"], "#193822" if is_dark else "#dcfce7")
        elif status == "FLAGGED":
            return (p["status_todo"], "#3d2a1a" if is_dark else "#ffedd5")
        elif status == "ERROR":
            return (p["status_bad"], "#3d1c1c" if is_dark else "#fee2e2")
        elif status == "DOCSEP":
            return (p["status_warn"], "#3a2d1d" if is_dark else "#fef3c7")
        elif status == "DONE":
            return (p["accent_primary"], "#1e3a5f" if is_dark else "#dbeafe")
        else:
            return (p["text_secondary"], p["bg_card_hover"])

    def _get_qc_pill_colors(self, qc_text: str, theme_name: str) -> tuple:
        p = get_palette_dict(theme_name)
        is_dark = (theme_name == THEME_DARK)
        if qc_text == "FAIL":
            return (p["status_bad"], "#3d1c1c" if is_dark else "#fee2e2")
        else:
            return (p["status_done"], "#193822" if is_dark else "#dcfce7")

    def _browse(self, var: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Folder", var.text() or "/")
        if path:
            var.setText(path)
            self.config["input_root"] = self.input_var.text()
            self.config["output_root"] = self.output_var.text()
            self.config["flagged_root"] = (self.output_var.text() + "/flagged") if self.output_var.text() else ""

    def _sync_paths_from_widgets(self):
        self.config["input_root"] = self.input_var.text()
        self.config["output_root"] = self.output_var.text()
        self.config["flagged_root"] = (self.output_var.text() + "/flagged") if self.output_var.text() else ""

    def _save_config(self):
        ensure_data_dir()
        config_file = DATA_DIR / "config.json"
        # Only persist user preferences, not session-specific folder paths
        to_save = dict(self.config)
        to_save["input_root"] = ""
        to_save["output_root"] = ""
        to_save["flagged_root"] = ""
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)

    def _make_pill_widget(self, text: str, fg: str, bg: str) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background: transparent; border: none;")
        container.setProperty("pill_text", text)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill = QLabel(text)
        pill.setObjectName("badge_pill")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        pill.setStyleSheet(f"""
            QLabel#badge_pill {{
                background-color: {bg};
                color: {fg};
                border-radius: 9px;
                padding: 2px 10px;
                min-width: 50px;
                border: none;
            }}
        """)
        layout.addWidget(pill)
        return container

    def _append_log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        detected_date = ""
        confidence = ""
        filename = ""
        blank_count = ""
        qc_text = ""

        # Extract QC suffix if present
        qc_suffix = ""
        if " | QC:" in msg:
            parts = msg.split(" | QC:", 1)
            msg = parts[0]
            qc_suffix = "QC:" + parts[1]
            if qc_suffix.startswith("QC:FAIL"):
                qc_text = "FAIL"
            elif qc_suffix.startswith("QC:Passed"):
                qc_text = "OK"

        theme = self.config.get("theme", THEME_DARK)
        p = get_palette_dict(theme)

        status = ""

        if "[AUTO]" in msg:
            status = "AUTO"
            self._stat_auto += 1
            parts = msg.split("[AUTO] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if " | " in detail:
                    main_part, blank_part = detail.split(" | ", 1)
                    blank_count = blank_part.replace(" blank", "")
                    detail = main_part
                if " -> " in detail:
                    filename, rest = detail.split(" -> ", 1)
                    if " (" in rest:
                        detected_date = rest.split(" (")[0]
                        confidence = rest.split("(")[1].split(")")[0]
                    else:
                        detected_date = rest
                else:
                    filename = detail
        elif "[FLAGGED]" in msg:
            status = "FLAGGED"
            self._stat_flagged += 1
            parts = msg.split("[FLAGGED] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if " | " in detail:
                    main_part, blank_part = detail.split(" | ", 1)
                    blank_count = blank_part.replace(" blank", "")
                    detail = main_part
                if " (" in detail:
                    filename = detail.rsplit(" (", 1)[0]
                    confidence = detail.rsplit(" (", 1)[1].rstrip(")")
                else:
                    filename = detail
        elif "[ERROR]" in msg:
            status = "ERROR"
            self._stat_errors += 1
            parts = msg.split("[ERROR] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    confidence = detail.split(": ", 1)[1]
                else:
                    confidence = detail
        elif "[BLANK]" in msg:
            status = "BLANK"
            parts = msg.split("[BLANK] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    rest = detail.split(": ", 1)[1]
                    if "ALL BLANK" in rest:
                        blank_count = "ALL"
                    else:
                        blank_count = rest
        elif "[DOCSEP]" in msg:
            status = "DOCSEP"
            parts = msg.split("[DOCSEP] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    confidence = detail.split(": ", 1)[1]
        elif "Done" in msg:
            status = "DONE"
            confidence = msg.replace("--- ", "").replace(" ---", "")
        else:
            return

        status_fg, status_bg = self._get_pill_colors(status, theme)
        self._update_kpi_cards()

        row = self.log_table.rowCount()
        self.log_table.insertRow(row)

        align_center = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter

        time_item = QTableWidgetItem(timestamp)
        time_item.setForeground(QColor(p["text_secondary"]))
        time_item.setFont(QFont("Consolas", 8))
        time_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 0, time_item)

        # Status Pill Badge Widget
        status_pill_widget = self._make_pill_widget(status, status_fg, status_bg)
        self.log_table.setCellWidget(row, 1, status_pill_widget)

        file_item = QTableWidgetItem(filename)
        file_item.setForeground(QColor(p["text_main"]))
        file_item.setFont(QFont("Consolas", 8))
        file_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 2, file_item)

        date_item = QTableWidgetItem(detected_date)
        date_item.setForeground(QColor(p["status_done"]) if detected_date else QColor(p["text_placeholder"]))
        date_item.setFont(QFont("Consolas", 8))
        date_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 3, date_item)

        conf_item = QTableWidgetItem(confidence)
        conf_item.setForeground(QColor(p["text_secondary"]))
        conf_item.setFont(QFont("Consolas", 8))
        conf_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 4, conf_item)

        blank_item = QTableWidgetItem(blank_count)
        blank_item.setForeground(QColor(p["status_todo"]) if blank_count else QColor(p["text_placeholder"]))
        blank_item.setFont(QFont("Consolas", 8))
        blank_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 5, blank_item)

        # QC Pill Badge or clean cell
        if qc_text:
            qc_fg, qc_bg = self._get_qc_pill_colors(qc_text, theme)
            qc_pill_widget = self._make_pill_widget(qc_text, qc_fg, qc_bg)
            self.log_table.setCellWidget(row, 6, qc_pill_widget)
        else:
            qc_item = QTableWidgetItem("-")
            qc_item.setForeground(QColor(p["text_placeholder"]))
            qc_item.setFont(QFont("Segoe UI", 8))
            qc_item.setTextAlignment(align_center)
            self.log_table.setItem(row, 6, qc_item)

        self.log_table.scrollToBottom()

    def _resolve_output_root(self, input_path: str) -> str:
        output = self.output_var.text().strip()
        if not output:
            output = str(Path(input_path) / "output")
            os.makedirs(output, exist_ok=True)
            self.output_var.setText(output)
        else:
            os.makedirs(output, exist_ok=True)
        return output

    def scan_folder(self):
        path = self.input_var.text()
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "Warning", "Please select a valid input folder first.")
            return

        self._set_status_pill("running", "SCANNING")
        from pipeline import parse_folder_structure
        try:
            batches = parse_folder_structure(Path(path))
            divs = len(batches)
            companies_set = set()
            pdfs = 0
            for b in batches:
                for doc in b.documents:
                    pdfs += 1
                    companies_set.add((b.division_code, doc.company_name))
            companies = len(companies_set)
        except Exception as e:
            QMessageBox.critical(self, "Scan Error", f"Failed to scan folder:\n{e}")
            self._set_status_pill("error", "ERROR")
            return

        self._stat_total = pdfs
        self._update_kpi_cards()

        ocr = self.config.get("ocr_engine", "tesseract")
        thresh = self.config.get("confidence_threshold", 20)
        year = self.config.get("earliest_year", 1950)
        self.scan_label.setText(f"OCR: {ocr.upper()}  ·  Threshold: {thresh}%  ·  Year: {year}+  ·  {pdfs} PDFs in {divs} division(s)")
        self._set_status_pill("idle", "SCANNED")

    def run_pipeline(self):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            return

        self.config["input_root"] = self.input_var.text()
        if not os.path.isdir(self.config["input_root"]):
            QMessageBox.critical(self, "Error", "Input folder does not exist")
            return
        output = self._resolve_output_root(self.config["input_root"])
        self.config["output_root"] = output
        self.config["flagged_root"] = os.path.join(output, "flagged")
        self._save_config()

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Running...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.cancel_btn.setEnabled(True)
        self._set_status_pill("running", "RUNNING")
        self.log_signal.emit("=== Pipeline Started ===")
        self.pipeline_started.emit()
        self._scan_plan = None
        self._processed_paths = set()
        self._emit_state_changed()

        self.pipeline_thread = PipelineThread(self.config, 0)
        self.pipeline_thread.progress.connect(self._on_progress)
        self.pipeline_thread.log_message.connect(self._append_log)
        self.pipeline_thread.finished_signal.connect(self._on_finished)
        self.pipeline_thread.error_signal.connect(self._on_error)
        self.pipeline_thread.doc_processed.connect(self.doc_update)
        self.pipeline_thread.doc_completed.connect(self._on_doc_completed)
        self.pipeline_thread.scan_plan.connect(self._on_scan_plan)
        self.pipeline_thread.start()

    def _on_scan_plan(self, paths):
        self._scan_plan = list(paths)
        self._stat_total = len(self._scan_plan)
        self._update_kpi_cards()
        self._emit_state_changed()

    def resume_scan(self, scan_info, rt):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            return
        plan = (scan_info or {}).get("plan") or []
        processed = (scan_info or {}).get("processed") or []
        remaining = [p for p in plan if p not in set(processed)]
        merged_flagged = list(rt.all_flagged_docs) if rt else []
        merged_confirmed = list(rt.auto_confirmed_docs) if rt else []

        self._scan_plan = list(plan)
        self._processed_paths = set(processed)
        self._stat_total = len(self._scan_plan)
        self._update_kpi_cards()
        self._emit_state_changed()

        if not remaining:
            self.log_signal.emit(f"=== Resume Complete: nothing to process ({len(processed)}/{len(plan)} already processed) ===")
            self.log_signal.emit(f"--- Done: {len(merged_confirmed)} confirmed, {len(merged_flagged)} flagged, 0 errors ---")
            self._on_finished(flagged=merged_flagged)
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Resuming...")
        self.progress_bar.setValue(5)
        self.progress_bar.setFormat(f"Resuming ({len(remaining)} remaining)...")
        self.cancel_btn.setEnabled(True)
        self._set_status_pill("running", "RESUMING")
        self.log_signal.emit(f"=== Resuming Pipeline ({len(remaining)} remaining) ===")

        self.pipeline_thread = PipelineThread(
            self.config,
            0,
            plan=plan,
            existing_paths=set(processed),
            merged_flagged=merged_flagged,
            merged_confirmed=merged_confirmed,
        )
        self.pipeline_thread.progress.connect(self._on_progress)
        self.pipeline_thread.log_message.connect(self._append_log)
        self.pipeline_thread.finished_signal.connect(self._on_finished)
        self.pipeline_thread.error_signal.connect(self._on_error)
        self.pipeline_thread.doc_processed.connect(self.doc_update)
        self.pipeline_thread.doc_completed.connect(self._on_doc_completed)
        self.pipeline_thread.start()

    def cancel_pipeline(self):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            self._cancel_requested = True
            self.pipeline_thread.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Cancelling...")
            self.log_signal.emit("Cancelling pipeline...")
            self._set_status_pill("error", "CANCELLING")

    def _on_progress(self, msg: str, pct: int):
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(msg)

    def _on_doc_completed(self, kind: str, path: str):
        if kind == "doc":
            self._processed_paths.add(path)
            self._emit_state_changed()

    def _emit_state_changed(self):
        try:
            self.state_changed.emit()
        except RuntimeError:
            pass

    def get_scan_state(self):
        if not self._scan_plan:
            return None
        return {
            "plan": list(self._scan_plan),
            "processed": sorted(list(self._processed_paths)),
        }

    def _on_finished(self, flagged=None):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶  Run Pipeline")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("✕  Cancel")
        if self._cancel_requested:
            self.progress_bar.setFormat("Cancelled")
            self._set_status_pill("idle", "CANCELLED")
            self._cancel_requested = False
        else:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("Completed")
            self._set_status_pill("done", "COMPLETED")
            self._scan_plan = None
            self._processed_paths = set()
        self._emit_state_changed()
        self.log_signal.emit("=== Pipeline Completed ===")
        self.pipeline_finished.emit()

    def _on_error(self, err_msg):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶  Run Pipeline")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("✕  Cancel")
        self.progress_bar.setFormat("Error")
        self._set_status_pill("error", "ERROR")
        self.log_signal.emit(f"ERROR: {err_msg}")
        QMessageBox.critical(self, "Pipeline Error", err_msg)
