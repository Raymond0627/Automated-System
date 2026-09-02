import json
import csv
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QMessageBox, QGroupBox,
    QFormLayout, QScrollArea, QLineEdit, QDialog, QListWidget, QListWidgetItem,
    QFileDialog, QAbstractItemView, QInputDialog, QGridLayout, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from company_extractor import (
    load_known_companies, save_known_companies, normalize_company_name,
    KNOWN_COMPANIES_FILE
)

# ---------------------------------------------------------------------------
# Minimal, flat style constants
# ---------------------------------------------------------------------------

BG_ROOT = "#1a1b2e"
BG_INPUT = "#12131f"
BORDER = "#2d2e45"
BORDER_FOCUS = "#565a8a"
TEXT_MAIN = "#e0e0e0"
TEXT_DIM = "#8888aa"
TEXT_FAINT = "#5f6080"
GOOD = "#4caf7d"
WARN = "#d99a4e"
BAD = "#e05a5a"

FIELD_WIDTH = 120

GROUPBOX_STYLE = f"""
    QGroupBox {{
        background-color: transparent;
        border: none;
        border-top: 1px solid {BORDER};
        margin-top: 22px;
        padding-top: 16px;
        font-weight: 600;
        color: {TEXT_MAIN};
        font-size: 9.5pt;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        top: 6px;
        padding: 0;
        color: {TEXT_MAIN};
    }}
    QLabel {{ color: {TEXT_DIM}; font-size: 9pt; background: transparent; }}
    QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
        background-color: {BG_INPUT};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 8px;
        min-height: 20px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
        border: 1px solid {BORDER_FOCUS};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QCheckBox {{ color: {TEXT_MAIN}; font-size: 9pt; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border-radius: 3px;
        border: 1px solid {BORDER_FOCUS};
        background-color: {BG_INPUT};
    }}
    QCheckBox::indicator:checked {{
        background-color: {TEXT_MAIN};
        border: 1px solid {TEXT_MAIN};
    }}
    QPushButton {{
        background-color: {BG_INPUT};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 5px 14px;
        font-size: 9pt;
    }}
    QPushButton:hover {{ border: 1px solid {BORDER_FOCUS}; }}
    QPushButton:disabled {{ color: {TEXT_FAINT}; border: 1px solid {BORDER}; }}
"""

PRIMARY_BUTTON_STYLE = f"""
    QPushButton {{
        background-color: {TEXT_MAIN};
        color: {BG_ROOT};
        border: none;
        border-radius: 4px;
        padding: 7px 20px;
        font-weight: 600;
        font-size: 9pt;
    }}
    QPushButton:hover {{ background-color: #ffffff; }}
"""


def field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8.5pt;")
    return lbl


def make_field_column(label_text, widget, width=FIELD_WIDTH):
    """A vertically stacked label+field, fixed width, for grid symmetry."""
    col = QVBoxLayout()
    col.setSpacing(4)
    col.addWidget(field_label(label_text))
    widget.setFixedWidth(width)
    col.addWidget(widget)
    wrapper = QWidget()
    wrapper.setLayout(col)
    wrapper.setFixedWidth(width)
    return wrapper


class CompanyRosterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Company Roster Manager")
        self.setMinimumSize(600, 520)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_ROOT}; color: {TEXT_MAIN}; }}
            QListWidget {{
                background-color: {BG_INPUT};
                color: {TEXT_MAIN};
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 9pt;
            }}
            QListWidget::item {{ padding: 4px 8px; }}
            QListWidget::item:selected {{ background-color: #2a2c48; }}
            QLineEdit {{
                background-color: {BG_INPUT}; color: {TEXT_MAIN};
                border: 1px solid {BORDER}; border-radius: 4px; padding: 5px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {BORDER_FOCUS}; }}
            QPushButton {{
                background-color: {BG_INPUT}; color: {TEXT_MAIN};
                border: 1px solid {BORDER}; border-radius: 4px; padding: 6px 14px;
            }}
            QPushButton:hover {{ border: 1px solid {BORDER_FOCUS}; }}
            QLabel {{ color: {TEXT_DIM}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("Company Roster Manager")
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_MAIN};")
        layout.addWidget(header)

        desc = QLabel("Manage the company name lookup list used for OCR fuzzy matching.")
        desc.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9pt;")
        layout.addWidget(desc)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(QLabel("Search"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter companies...")
        self.search_input.textChanged.connect(self._filter_list)
        search_row.addWidget(self.search_input, 1)
        self.count_label = QLabel("0 companies")
        self.count_label.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 8.5pt;")
        search_row.addWidget(self.count_label)
        layout.addLayout(search_row)

        self.company_list = QListWidget()
        self.company_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.company_list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.company_list.itemChanged.connect(self._on_item_edited)
        layout.addWidget(self.company_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.add_btn = QPushButton("Add Single")
        self.add_btn.clicked.connect(self._add_single)
        btn_row.addWidget(self.add_btn)

        self.import_csv_btn = QPushButton("Import CSV")
        self.import_csv_btn.clicked.connect(self._import_csv)
        btn_row.addWidget(self.import_csv_btn)

        self.export_csv_btn = QPushButton("Export CSV")
        self.export_csv_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self.export_csv_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()

        layout.addLayout(btn_row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_btn = QPushButton("Done")
        self.close_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.close_btn.setFixedWidth(100)
        self.close_btn.clicked.connect(self._save_and_close)
        close_row.addWidget(self.close_btn)
        layout.addLayout(close_row)

        self._load_companies()

    def _load_companies(self):
        self.all_companies = load_known_companies()
        self._refresh_list()

    def _refresh_list(self, filter_text=""):
        self.company_list.blockSignals(True)
        self.company_list.clear()
        filtered = [c for c in self.all_companies if filter_text.lower() in c.lower()] if filter_text else self.all_companies
        for company in sorted(filtered):
            item = QListWidgetItem(company)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.company_list.addItem(item)
        self.count_label.setText(f"{len(self.all_companies)} companies total")
        self.company_list.blockSignals(False)

    def _filter_list(self, text):
        self._refresh_list(text)

    def _on_item_edited(self, item):
        old_name = item.data(Qt.ItemDataRole.UserRole)
        new_name = item.text().strip()
        if not new_name:
            item.setText(old_name)
            return
        normalized = normalize_company_name(new_name)
        if normalized != old_name:
            if normalized in self.all_companies:
                QMessageBox.information(self, "Duplicate", f"'{normalized}' already exists in the roster.")
                item.setText(old_name)
                return
            idx = self.all_companies.index(old_name) if old_name in self.all_companies else -1
            if idx >= 0:
                self.all_companies[idx] = normalized
            item.setText(normalized)
            item.setData(Qt.ItemDataRole.UserRole, normalized)

    def _add_single(self):
        text, ok = QInputDialog.getText(self, "Add Company", "Company name:")
        if ok and text.strip():
            normalized = normalize_company_name(text.strip())
            if normalized in self.all_companies:
                QMessageBox.information(self, "Duplicate", f"'{normalized}' already exists in the roster.")
                return
            self.all_companies.append(normalized)
            self._refresh_list(self.search_input.text())

    def _import_csv(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Company CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)

            if not rows:
                QMessageBox.warning(self, "Empty File", "The CSV file is empty.")
                return

            header = rows[0][0].strip().lower() if rows[0] else ""
            if header == "company_name":
                data_rows = rows[1:]
            elif len(rows[0]) >= 1 and not any(c.isdigit() for c in rows[0][0][:4]):
                data_rows = rows[1:] if len(rows) > 1 else []
            else:
                data_rows = rows

            added = 0
            skipped = 0
            for row in data_rows:
                if not row or not row[0].strip():
                    continue
                raw = row[0].strip()
                normalized = normalize_company_name(raw)
                if normalized in self.all_companies:
                    skipped += 1
                else:
                    self.all_companies.append(normalized)
                    added += 1

            self._refresh_list(self.search_input.text())
            QMessageBox.information(
                self, "Import Complete",
                f"Added: {added} new companies\nSkipped: {skipped} duplicates"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to read CSV:\n{e}")

    def _delete_selected(self):
        selected = self.company_list.selectedItems()
        if not selected:
            return
        names = [item.text() for item in selected]
        reply = QMessageBox.question(
            self, "Delete Companies",
            f"Delete {len(names)} selected company/companies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            for name in names:
                if name in self.all_companies:
                    self.all_companies.remove(name)
            self._refresh_list(self.search_input.text())

    def _export_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Company Roster", "company_roster.csv", "CSV Files (*.csv)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["company_name"])
                for company in sorted(self.all_companies):
                    writer.writerow([company])
            QMessageBox.information(self, "Exported", f"Exported {len(self.all_companies)} companies to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export CSV:\n{e}")

    def _save_and_close(self):
        save_known_companies(self.all_companies)
        self.accept()


class SettingsTab(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.build_ui()

    # ------------------------------------------------------------------
    def build_ui(self):
        self.setStyleSheet(f"background-color: {BG_ROOT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(48, 36, 48, 36)
        layout.setSpacing(4)

        page_title = QLabel("Settings")
        page_title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        page_title.setStyleSheet(f"color: {TEXT_MAIN};")
        layout.addWidget(page_title)
        layout.addSpacing(8)

        layout.addWidget(self._build_pipeline_group())
        layout.addWidget(self._build_gpu_group())
        layout.addWidget(self._build_qc_group())
        layout.addWidget(self._build_enhance_group())
        layout.addWidget(self._build_roster_group())
        layout.addWidget(self._build_format_group())

        layout.addSpacing(12)
        save_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("accent")
        self.save_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.save_btn.setFixedWidth(160)
        self.save_btn.clicked.connect(self.save_settings)
        save_row.addWidget(self.save_btn)
        save_row.addStretch()
        layout.addLayout(save_row)

        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._toggle_gpu_fields(self.gpu_combo.currentText())
        self._toggle_qc_fields(self.enable_qc_check.isChecked())

    # ------------------------------------------------------------------
    def _build_pipeline_group(self):
        group = QGroupBox("Pipeline Settings")
        group.setStyleSheet(GROUPBOX_STYLE)
        row = QHBoxLayout(group)
        row.setSpacing(32)

        self.conf_spin = QSpinBox()
        self.conf_spin.setRange(0, 100)
        self.conf_spin.setValue(self.config.get("confidence_threshold", 20))
        row.addWidget(make_field_column("Confidence Threshold", self.conf_spin))

        self.page_spin = QSpinBox()
        self.page_spin.setRange(0, 100)
        self.page_spin.setValue(self.config.get("page_index", 0))
        row.addWidget(make_field_column("PDF Page Index", self.page_spin))

        self.ocr_combo = QComboBox()
        self.ocr_combo.addItems(["tesseract"])
        self.ocr_combo.setCurrentText(self.config.get("ocr_engine", "tesseract"))
        row.addWidget(make_field_column("OCR Engine", self.ocr_combo))

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1900, 2100)
        self.year_spin.setValue(self.config.get("earliest_year", 1950))
        row.addWidget(make_field_column("Earliest Valid Year", self.year_spin))

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setSingleStep(10)
        self.dpi_spin.setValue(self.config.get("render_dpi", 150))
        row.addWidget(make_field_column("Render DPI", self.dpi_spin))

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(self.config.get("max_workers", 4))
        row.addWidget(make_field_column("Parallel Workers", self.workers_spin))

        row.addStretch()
        return group

    # ------------------------------------------------------------------
    def _build_gpu_group(self):
        group = QGroupBox("GPU / Acceleration")
        group.setStyleSheet(GROUPBOX_STYLE)
        row = QHBoxLayout(group)
        row.setSpacing(32)

        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(["CPU Only", "Local GPU (CUDA)", "Remote GPU Server"])
        current_gpu = self.config.get("gpu_mode", "cpu")
        gpu_map = {"cpu": "CPU Only", "local_gpu": "Local GPU (CUDA)", "remote": "Remote GPU Server"}
        self.gpu_combo.setCurrentText(gpu_map.get(current_gpu, "CPU Only"))
        self.gpu_combo.currentTextChanged.connect(self._toggle_gpu_fields)
        row.addWidget(make_field_column("Acceleration Mode", self.gpu_combo, width=170))

        self.remote_url_input = QLineEdit()
        self.remote_url_input.setPlaceholderText("http://your-gpu-server:8000")
        self.remote_url_input.setText(self.config.get("remote_gpu_url", ""))
        row.addWidget(make_field_column("Remote Server URL", self.remote_url_input, width=230))

        detect_col = QVBoxLayout()
        detect_col.setSpacing(4)
        detect_col.addWidget(field_label("Diagnostics"))
        self.detect_gpu_btn = QPushButton("Detect CUDA")
        self.detect_gpu_btn.setFixedWidth(120)
        self.detect_gpu_btn.clicked.connect(self._detect_cuda)
        detect_col.addWidget(self.detect_gpu_btn)
        detect_wrap = QWidget()
        detect_wrap.setLayout(detect_col)
        row.addWidget(detect_wrap)

        self.gpu_status_label = QLabel("")
        self.gpu_status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8.5pt;")
        status_col = QVBoxLayout()
        status_col.setSpacing(4)
        status_col.addWidget(field_label("Status"))
        status_col.addWidget(self.gpu_status_label)
        status_wrap = QWidget()
        status_wrap.setLayout(status_col)
        row.addWidget(status_wrap)

        row.addStretch()
        return group

    # ------------------------------------------------------------------
    def _build_qc_group(self):
        group = QGroupBox("Auto QC")
        group.setStyleSheet(GROUPBOX_STYLE)
        outer = QVBoxLayout(group)
        outer.setSpacing(14)

        self.enable_qc_check = QCheckBox("Enable Auto QC during pipeline")
        self.enable_qc_check.setChecked(self.config.get("enable_qc", True))
        self.enable_qc_check.toggled.connect(self._toggle_qc_fields)
        outer.addWidget(self.enable_qc_check)

        row = QHBoxLayout()
        row.setSpacing(32)

        toggles_col = QVBoxLayout()
        toggles_col.setSpacing(10)
        self.enable_docsep = QCheckBox("Auto-remove DOCSEP separator pages")
        self.enable_docsep.setChecked(self.config.get("enable_docsep_removal", True))
        toggles_col.addWidget(self.enable_docsep)
        self.enable_blank_rm = QCheckBox("Remove blank pages on Finalize")
        self.enable_blank_rm.setChecked(self.config.get("enable_blank_removal", True))
        toggles_col.addWidget(self.enable_blank_rm)
        toggles_wrap = QWidget()
        toggles_wrap.setLayout(toggles_col)
        row.addWidget(toggles_wrap)

        self.qc_blank_spin = QDoubleSpinBox()
        self.qc_blank_spin.setRange(0.1, 10.0)
        self.qc_blank_spin.setSingleStep(0.1)
        self.qc_blank_spin.setDecimals(1)
        self.qc_blank_spin.setValue(self.config.get("qc_blank_threshold", 1.5))
        row.addWidget(make_field_column("Blank Page Threshold", self.qc_blank_spin))

        self.qc_rotation_spin = QSpinBox()
        self.qc_rotation_spin.setRange(10, 100)
        self.qc_rotation_spin.setValue(self.config.get("qc_rotation_threshold", 65))
        row.addWidget(make_field_column("Rotation Threshold", self.qc_rotation_spin))

        self.qc_mirror_spin = QSpinBox()
        self.qc_mirror_spin.setRange(1, 50)
        self.qc_mirror_spin.setValue(self.config.get("qc_mirror_threshold", 15))
        row.addWidget(make_field_column("Mirror Delta Threshold %", self.qc_mirror_spin))

        row.addStretch()
        outer.addLayout(row)
        return group

    # ------------------------------------------------------------------
    def _build_enhance_group(self):
        group = QGroupBox("Image Enhancement")
        group.setStyleSheet(GROUPBOX_STYLE)
        outer = QVBoxLayout(group)
        outer.setSpacing(14)

        self.enhance_enable_check = QCheckBox("Enable Auto Enhancement (right-click on page)")
        self.enhance_enable_check.setChecked(self.config.get("enhance_enabled", True))
        outer.addWidget(self.enhance_enable_check)

        row = QHBoxLayout()
        row.setSpacing(32)

        self.enhance_dpi_spin = QSpinBox()
        self.enhance_dpi_spin.setRange(0, 600)
        self.enhance_dpi_spin.setSpecialValueText("Default")
        self.enhance_dpi_spin.setValue(self.config.get("enhance_dpi", 0))
        row.addWidget(make_field_column("DPI Override (0 = default)", self.enhance_dpi_spin))

        self.enhance_denoise_spin = QSpinBox()
        self.enhance_denoise_spin.setRange(0, 20)
        self.enhance_denoise_spin.setValue(self.config.get("enhance_denoise_strength", 5))
        row.addWidget(make_field_column("Denoise Strength (0-20)", self.enhance_denoise_spin))

        self.enhance_sharpen_spin = QDoubleSpinBox()
        self.enhance_sharpen_spin.setRange(0.0, 2.0)
        self.enhance_sharpen_spin.setSingleStep(0.1)
        self.enhance_sharpen_spin.setDecimals(1)
        self.enhance_sharpen_spin.setValue(self.config.get("enhance_sharpen_amount", 0.5))
        row.addWidget(make_field_column("Sharpen Amount (0.0-2.0)", self.enhance_sharpen_spin))

        row.addStretch()
        outer.addLayout(row)
        return group

    # ------------------------------------------------------------------
    def _build_roster_group(self):
        group = QGroupBox("Company Roster")
        group.setStyleSheet(GROUPBOX_STYLE)
        row = QHBoxLayout(group)
        row.setSpacing(16)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        companies = load_known_companies()
        self.roster_count_label = QLabel(f"{len(companies)} companies in lookup roster")
        self.roster_count_label.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 9pt;")
        info_col.addWidget(self.roster_count_label)
        roster_desc = QLabel("Used for OCR fuzzy matching of company names against document content.")
        roster_desc.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 8.5pt;")
        info_col.addWidget(roster_desc)
        info_wrap = QWidget()
        info_wrap.setLayout(info_col)
        row.addWidget(info_wrap)

        row.addStretch()

        self.manage_roster_btn = QPushButton("Manage Companies...")
        self.manage_roster_btn.setFixedWidth(160)
        self.manage_roster_btn.clicked.connect(self._open_roster_manager)
        row.addWidget(self.manage_roster_btn)

        return group

    # ------------------------------------------------------------------
    def _build_format_group(self):
        group = QGroupBox("Output Filename Format")
        group.setStyleSheet(GROUPBOX_STYLE)
        col = QVBoxLayout(group)
        col.setSpacing(6)

        pattern = QLabel("{YYYYMM}{SEQ}_{DIVISION}_{Company_Name}.pdf")
        pattern.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 9pt;")
        col.addWidget(pattern)

        example = QLabel("Example: 2026010001031_NEW_ZEALAND_INS_CORP.pdf")
        example.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 8.5pt;")
        col.addWidget(example)

        layout_row = QHBoxLayout()
        layout_row.setSpacing(8)
        layout_row.addWidget(QLabel("Save location:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Per-company folders (Division / Company)", "company")
        self.layout_combo.addItem("Single folder (all documents)", "flat")
        idx = self.layout_combo.findData(self.config.get("output_layout", "company"))
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch()
        col.addLayout(layout_row)

        return group

    # ------------------------------------------------------------------
    def _toggle_qc_fields(self, enabled: bool):
        self.enable_docsep.setEnabled(enabled)
        self.enable_blank_rm.setEnabled(enabled)
        self.qc_blank_spin.setEnabled(enabled)
        self.qc_rotation_spin.setEnabled(enabled)
        self.qc_mirror_spin.setEnabled(enabled)

    def _toggle_gpu_fields(self, text: str):
        is_remote = text == "Remote GPU Server"
        self.remote_url_input.setEnabled(is_remote)
        self.detect_gpu_btn.setEnabled(text != "Remote GPU Server")

    def _detect_cuda(self):
        self.gpu_status_label.setText("GPU acceleration not available (Tesseract only)")
        self.gpu_status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8.5pt;")

    def _open_roster_manager(self):
        dlg = CompanyRosterDialog(self)
        dlg.exec()
        companies = load_known_companies()
        self.roster_count_label.setText(f"{len(companies)} companies in lookup roster")

    def save_settings(self):
        self.config["confidence_threshold"] = self.conf_spin.value()
        self.config["page_index"] = self.page_spin.value()
        self.config["earliest_year"] = self.year_spin.value()
        self.config["ocr_engine"] = self.ocr_combo.currentText()
        self.config["render_dpi"] = self.dpi_spin.value()
        self.config["max_workers"] = self.workers_spin.value()
        self.config["output_layout"] = self.layout_combo.currentData()
        self.config["enable_qc"] = self.enable_qc_check.isChecked()
        self.config["enable_docsep_removal"] = self.enable_docsep.isChecked()
        self.config["enable_blank_removal"] = self.enable_blank_rm.isChecked()
        self.config["qc_blank_threshold"] = self.qc_blank_spin.value()
        self.config["qc_rotation_threshold"] = self.qc_rotation_spin.value()
        self.config["qc_mirror_threshold"] = self.qc_mirror_spin.value()
        self.config["enhance_enabled"] = self.enhance_enable_check.isChecked()
        self.config["enhance_dpi"] = self.enhance_dpi_spin.value()
        self.config["enhance_denoise_strength"] = self.enhance_denoise_spin.value()
        self.config["enhance_sharpen_amount"] = self.enhance_sharpen_spin.value()

        gpu_text = self.gpu_combo.currentText()
        gpu_save_map = {"CPU Only": "cpu", "Local GPU (CUDA)": "local_gpu", "Remote GPU Server": "remote"}
        self.config["gpu_mode"] = gpu_save_map.get(gpu_text, "cpu")
        self.config["remote_gpu_url"] = self.remote_url_input.text().strip()

        config_file = BASE_DIR / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        QMessageBox.information(self, "Saved", "Settings saved successfully")