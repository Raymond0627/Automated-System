import json
import csv
import sys
from pathlib import Path
from typing import Tuple

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR, DATA_DIR, ensure_data_dir

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QCheckBox, QComboBox, QMessageBox, QScrollArea, QLineEdit, QDialog,
    QListWidget, QAbstractItemView, QGridLayout, QAbstractSpinBox,
    QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont

from company_extractor import (
    load_known_companies, save_known_companies, normalize_company_name,
    KNOWN_COMPANIES_FILE
)
from .styles import (
    get_palette_dict, get_dashboard_card_style, get_primary_btn_style,
    get_secondary_btn_style, get_combobox_popup_style, THEME_DARK, THEME_LIGHT
)


class NumberStepper(QFrame):
    """Modern 2026 horizontal segmented number stepper: [ − ] Value [ + ]"""
    valueChanged = pyqtSignal(object)

    def __init__(
        self,
        min_val=0,
        max_val=100,
        step=1,
        initial=0,
        is_float=False,
        decimals=1,
        suffix="",
        special_value_text="",
        theme=THEME_LIGHT,
        parent=None
    ):
        super().__init__(parent)
        self.theme = theme
        self.is_float = is_float
        self.setObjectName("number_stepper")
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        # Decrement button
        self.btn_minus = QPushButton("−")
        self.btn_minus.setFixedSize(30, 28)
        self.btn_minus.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.btn_minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_minus.setAutoRepeat(True)
        self.btn_minus.setAutoRepeatDelay(350)
        self.btn_minus.setAutoRepeatInterval(60)

        # Spinbox in center
        if is_float:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
        else:
            self.spin = QSpinBox()

        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setValue(initial)
        if suffix:
            self.spin.setSuffix(suffix)
        if special_value_text:
            self.spin.setSpecialValueText(special_value_text)

        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

        # Increment button
        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedSize(30, 28)
        self.btn_plus.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.btn_plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_plus.setAutoRepeat(True)
        self.btn_plus.setAutoRepeatDelay(350)
        self.btn_plus.setAutoRepeatInterval(60)

        self.btn_minus.clicked.connect(self.spin.stepDown)
        self.btn_plus.clicked.connect(self.spin.stepUp)
        self.spin.valueChanged.connect(self.valueChanged.emit)

        layout.addWidget(self.btn_minus)
        layout.addWidget(self.spin, 1)
        layout.addWidget(self.btn_plus)

        self.apply_theme(theme)

    def value(self):
        return self.spin.value()

    def setValue(self, v):
        self.spin.setValue(v)

    def setRange(self, mn, mx):
        self.spin.setRange(mn, mx)

    def setSingleStep(self, s):
        self.spin.setSingleStep(s)

    def setDecimals(self, d):
        if hasattr(self.spin, "setDecimals"):
            self.spin.setDecimals(d)

    def setSuffix(self, s):
        self.spin.setSuffix(s)

    def setSpecialValueText(self, t):
        self.spin.setSpecialValueText(t)

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self.btn_minus.setEnabled(enabled)
        self.spin.setEnabled(enabled)
        self.btn_plus.setEnabled(enabled)

    def apply_theme(self, theme: str):
        self.theme = theme
        p = get_palette_dict(theme)
        self.setStyleSheet(f"""
            QFrame#number_stepper {{
                background-color: {p['bg_input']};
                border: 1px solid {p['border_input']};
                border-radius: 7px;
            }}
            QFrame#number_stepper:hover {{
                border: 1px solid {p['border_focus']};
            }}
        """)
        btn_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {p['text_secondary']};
                border: none;
                border-radius: 5px;
                padding: 0px;
                font-size: 13pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {p['bg_card_hover']};
                color: {p['text_main']};
            }}
            QPushButton:pressed {{
                background-color: {p['accent_primary']}30;
                color: {p['accent_primary']};
            }}
            QPushButton:disabled {{
                color: {p['text_placeholder']};
            }}
        """
        self.btn_minus.setStyleSheet(btn_style)
        self.btn_plus.setStyleSheet(btn_style)

        spin_style = f"""
            QSpinBox, QDoubleSpinBox {{
                background-color: transparent;
                border: none;
                color: {p['text_main']};
                padding: 0 4px;
                font-size: 9pt;
                font-weight: 700;
            }}
            QSpinBox:focus, QDoubleSpinBox:focus {{
                border: none;
            }}
            QSpinBox:disabled, QDoubleSpinBox:disabled {{
                color: {p['text_placeholder']};
            }}
        """
        self.spin.setStyleSheet(spin_style)


class CompanyRosterDialog(QDialog):
    def __init__(self, parent=None, theme: str = THEME_LIGHT):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Company Roster Manager")
        self.setMinimumSize(600, 520)
        p = get_palette_dict(theme)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {p['bg_window']}; color: {p['text_main']}; }}
            QListWidget {{
                background-color: {p['bg_surface']};
                color: {p['text_main']};
                border: 1px solid {p['border']};
                border-radius: 6px;
                font-size: 9pt;
            }}
            QListWidget::item {{ padding: 5px 8px; }}
            QListWidget::item:selected {{ background-color: {p['bg_tree_selected']}; color: {p['text_accent']}; }}
            QLineEdit {{
                background-color: {p['bg_input']}; color: {p['text_main']};
                border: 1px solid {p['border_input']}; border-radius: 6px; padding: 5px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {p['border_focus']}; }}
            QPushButton {{
                background-color: {p['bg_input']}; color: {p['text_main']};
                border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 14px;
            }}
            QPushButton:hover {{ border: 1px solid {p['border_focus']}; }}
            QLabel {{ color: {p['text_secondary']}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        header = QLabel("Company Roster Manager")
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {p['text_main']};")
        layout.addWidget(header)

        desc = QLabel("Manage the company name lookup list used for OCR fuzzy matching.")
        desc.setStyleSheet(f"color: {p['text_secondary']}; font-size: 9pt;")
        layout.addWidget(desc)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(QLabel("Search"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter companies...")
        self.search_input.textChanged.connect(self._filter_list)
        search_row.addWidget(self.search_input, 1)
        self.count_label = QLabel("0 companies")
        self.count_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        search_row.addWidget(self.count_label)
        layout.addLayout(search_row)

        self.company_list = QListWidget()
        self.company_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.company_list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.company_list.itemChanged.connect(self._on_item_edited)
        layout.addWidget(self.company_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.add_btn = QPushButton("Add Company...")
        self.add_btn.clicked.connect(self._add_company)
        btn_row.addWidget(self.add_btn)

        self.import_btn = QPushButton("Import CSV...")
        self.import_btn.clicked.connect(self._import_csv)
        btn_row.addWidget(self.import_btn)

        self.export_btn = QPushButton("Export CSV...")
        self.export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self.export_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()

        self.close_btn = QPushButton("Done")
        self.close_btn.setObjectName("accent")
        self.close_btn.clicked.connect(self._save_and_close)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

        self.all_companies = load_known_companies()
        self.all_companies.sort()
        self._populate_list(self.all_companies)

    def _populate_list(self, companies):
        self.company_list.blockSignals(True)
        self.company_list.clear()
        for name in companies:
            self.company_list.addItem(name)
        self.company_list.blockSignals(False)
        self.count_label.setText(f"{len(companies)} of {len(self.all_companies)} companies")

    def _filter_list(self, text: str):
        query = text.strip().lower()
        if not query:
            self._populate_list(self.all_companies)
            return
        filtered = [c for c in self.all_companies if query in c.lower()]
        self._populate_list(filtered)

    def _refresh_list(self, query=""):
        self.all_companies.sort()
        if query:
            self._filter_list(query)
        else:
            self._populate_list(self.all_companies)

    def _on_item_edited(self, item):
        pass

    def _add_company(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add Company", "Company Name:")
        if ok and name.strip():
            normalized = normalize_company_name(name.strip())
            if normalized in self.all_companies:
                QMessageBox.information(self, "Duplicate", f"'{normalized}' already exists.")
                return
            self.all_companies.append(normalized)
            self._refresh_list(self.search_input.text())

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Company CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)

            if not rows:
                QMessageBox.warning(self, "Empty File", "The selected file contains no data.")
                return

            has_header = any(
                keyword in rows[0][0].lower()
                for keyword in ["company", "name", "customer", "vendor", "account"]
            )
            data_rows = rows[1:] if has_header else rows

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
            f"Remove {len(names)} selected company/companies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for name in names:
                if name in self.all_companies:
                    self.all_companies.remove(name)
            self._refresh_list(self.search_input.text())

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Company CSV", "known_companies.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Company Name"])
                for name in self.all_companies:
                    writer.writerow([name])
            QMessageBox.information(self, "Export Complete", f"Exported {len(self.all_companies)} companies.")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export CSV:\n{e}")

    def _save_and_close(self):
        save_known_companies(self.all_companies)
        self.accept()


class SettingsTab(QWidget):
    theme_changed = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._cards = []
        self._card_titles = []
        self._card_subtitles = []
        self._field_labels = []
        self._steppers = []
        self.build_ui()

    def _create_card(self, title: str, subtitle: str = "") -> Tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("dashboard_card")
        self._cards.append(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(12)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._card_titles.append(title_lbl)
        header_col.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
            self._card_subtitles.append(sub_lbl)
            header_col.addWidget(sub_lbl)

        card_layout.addLayout(header_col)
        return card, card_layout

    def make_field_column(self, label_text: str, widget: QWidget, width: int = 0) -> QWidget:
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel(label_text)
        self._field_labels.append(lbl)
        lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        col.addWidget(lbl)
        if width > 0:
            widget.setFixedWidth(width)
        col.addWidget(widget)
        wrapper = QWidget()
        wrapper.setLayout(col)
        return wrapper

    def build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(14)

        # Page Header
        header_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        page_title = QLabel("Settings & Preferences")
        page_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.page_title = page_title
        title_col.addWidget(page_title)

        page_desc = QLabel("Configure OCR thresholds, image enhancement, and workflow rules")
        page_desc.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        self.page_desc = page_desc
        title_col.addWidget(page_desc)
        header_row.addLayout(title_col)
        header_row.addStretch()

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("accent")
        self.save_btn.setFixedHeight(34)
        self.save_btn.setFixedWidth(150)
        self.save_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.save_btn.clicked.connect(self.save_settings)
        header_row.addWidget(self.save_btn)
        layout.addLayout(header_row)

        theme = self.config.get("theme", THEME_LIGHT)

        # ------------------------------------------------------------------
        # Cards Grid Layout (2 Cards per Horizontal Row)
        # ------------------------------------------------------------------
        cards_grid = QGridLayout()
        cards_grid.setHorizontalSpacing(14)
        cards_grid.setVerticalSpacing(14)
        cards_grid.setContentsMargins(0, 0, 0, 0)

        # Row 0, Left: 1. Appearance & Theme Card
        theme_card, theme_layout = self._create_card(
            "Appearance & Theme",
            "Choose between Dark Mode and high-contrast Light Mode"
        )
        theme_row = QVBoxLayout()
        theme_row.setSpacing(10)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems([THEME_LIGHT, THEME_DARK])
        if theme in (THEME_DARK, THEME_LIGHT):
            self.theme_combo.setCurrentText(theme)
        else:
            self.theme_combo.setCurrentText(THEME_LIGHT)
        self.theme_combo.currentTextChanged.connect(self._on_theme_selected)
        theme_row.addWidget(self.make_field_column("Color Theme", self.theme_combo))

        theme_desc = QLabel("Theme updates instantly across all windows and controls.")
        self.theme_desc_label = theme_desc
        theme_row.addWidget(theme_desc)
        theme_layout.addLayout(theme_row)
        theme_layout.addStretch()
        cards_grid.addWidget(theme_card, 0, 0)

        # Row 0, Right: 2. Pipeline Settings Card
        pipe_card, pipe_layout = self._create_card(
            "OCR & Pipeline Engine",
            "Confidence thresholds, page index, and parallel worker concurrency"
        )
        pipe_grid = QGridLayout()
        pipe_grid.setHorizontalSpacing(16)
        pipe_grid.setVerticalSpacing(10)

        self.conf_spin = NumberStepper(
            min_val=0, max_val=100, step=5,
            initial=self.config.get("confidence_threshold", 85),
            suffix=" %", theme=theme
        )
        self._steppers.append(self.conf_spin)
        pipe_grid.addWidget(self.make_field_column("Confidence Threshold", self.conf_spin), 0, 0)

        self.page_spin = NumberStepper(
            min_val=0, max_val=100, step=1,
            initial=self.config.get("page_index", 0),
            theme=theme
        )
        self._steppers.append(self.page_spin)
        pipe_grid.addWidget(self.make_field_column("PDF Page Index", self.page_spin), 0, 1)

        self.ocr_combo = QComboBox()
        self.ocr_combo.addItems(["tesseract"])
        self.ocr_combo.setCurrentText(self.config.get("ocr_engine", "tesseract"))
        pipe_grid.addWidget(self.make_field_column("OCR Engine", self.ocr_combo), 0, 2)

        self.year_spin = NumberStepper(
            min_val=1900, max_val=2100, step=1,
            initial=self.config.get("earliest_year", 1950),
            theme=theme
        )
        self._steppers.append(self.year_spin)
        pipe_grid.addWidget(self.make_field_column("Earliest Valid Year", self.year_spin), 1, 0)

        self.dpi_spin = NumberStepper(
            min_val=72, max_val=600, step=10,
            initial=self.config.get("render_dpi", 150),
            suffix=" DPI", theme=theme
        )
        self._steppers.append(self.dpi_spin)
        pipe_grid.addWidget(self.make_field_column("Render DPI", self.dpi_spin), 1, 1)

        self.workers_spin = NumberStepper(
            min_val=1, max_val=16, step=1,
            initial=self.config.get("max_workers", 4),
            suffix=" workers", theme=theme
        )
        self._steppers.append(self.workers_spin)
        pipe_grid.addWidget(self.make_field_column("Parallel Workers", self.workers_spin), 1, 2)

        pipe_layout.addLayout(pipe_grid)
        cards_grid.addWidget(pipe_card, 0, 1)

        # Row 1, Left: 3. Company Lookup Roster Card (Prominently placed in view)
        roster_card, roster_layout = self._create_card(
            "Company Lookup Roster",
            "Maintain recognized companies list for fuzzy name extraction"
        )
        roster_content = QVBoxLayout()
        roster_content.setSpacing(10)

        roster_info = QHBoxLayout()
        roster_info.setSpacing(14)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        companies = load_known_companies()
        self.roster_count_label = QLabel(f"{len(companies)} companies in lookup roster")
        info_col.addWidget(self.roster_count_label)
        self.roster_desc = QLabel("Used for OCR fuzzy matching of company names.")
        info_col.addWidget(self.roster_desc)
        roster_info.addLayout(info_col)
        roster_info.addStretch()

        self.manage_roster_btn = QPushButton("Manage Companies...")
        self.manage_roster_btn.setFixedHeight(36)
        self.manage_roster_btn.setFixedWidth(180)
        self.manage_roster_btn.setObjectName("accent")
        self.manage_roster_btn.setStyleSheet(get_primary_btn_style(theme))
        self.manage_roster_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.manage_roster_btn.clicked.connect(self._open_roster_manager)
        roster_info.addWidget(self.manage_roster_btn)
        roster_content.addLayout(roster_info)

        roster_layout.addLayout(roster_content)
        roster_layout.addStretch()
        cards_grid.addWidget(roster_card, 1, 0)

        # Row 1, Right: 4. Output Filename Format Card
        format_card, format_layout = self._create_card(
            "Output Structure & Filename Format",
            "Control output layout and standardized PDF naming structure"
        )
        format_col = QVBoxLayout()
        format_col.setSpacing(6)

        self.pattern_label = QLabel("{YYYYMM}{SEQ}_{DIVISION}_{Company_Name}.pdf")
        self.pattern_label.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        format_col.addWidget(self.pattern_label)

        self.example_label = QLabel("Example: 2026010001031_NEW_ZEALAND_INS_CORP.pdf")
        format_col.addWidget(self.example_label)

        layout_row = QHBoxLayout()
        layout_row.setSpacing(10)
        self.save_loc_label = QLabel("Save Location:")
        layout_row.addWidget(self.save_loc_label)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Mirror input folders (same tree)", "mirror")
        self.layout_combo.addItem("Per-company folders (Division / Company)", "company")
        self.layout_combo.addItem("Single folder (all documents)", "flat")
        idx = self.layout_combo.findData(self.config.get("output_layout", "flat"))
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout_row.addWidget(self.layout_combo, 1)
        format_col.addLayout(layout_row)

        format_layout.addLayout(format_col)
        format_layout.addStretch()
        cards_grid.addWidget(format_card, 1, 1)

        # Row 2, Left: 5. Hardware Acceleration Card
        gpu_card, gpu_layout = self._create_card(
            "Hardware Acceleration",
            "Configure CPU, local GPU (CUDA), or remote GPU server"
        )
        gpu_grid = QGridLayout()
        gpu_grid.setHorizontalSpacing(16)
        gpu_grid.setVerticalSpacing(10)

        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(["CPU Only", "Local GPU (CUDA)", "Remote GPU Server"])
        current_gpu = self.config.get("gpu_mode", "cpu")
        gpu_map = {"cpu": "CPU Only", "local_gpu": "Local GPU (CUDA)", "remote": "Remote GPU Server"}
        self.gpu_combo.setCurrentText(gpu_map.get(current_gpu, "CPU Only"))
        self.gpu_combo.currentTextChanged.connect(self._toggle_gpu_fields)
        gpu_grid.addWidget(self.make_field_column("Acceleration Mode", self.gpu_combo), 0, 0)

        self.remote_url_input = QLineEdit()
        self.remote_url_input.setPlaceholderText("http://your-gpu-server:8000")
        self.remote_url_input.setText(self.config.get("remote_gpu_url", ""))
        gpu_grid.addWidget(self.make_field_column("Remote Server URL", self.remote_url_input), 0, 1)

        self.detect_gpu_btn = QPushButton("Detect CUDA")
        self.detect_gpu_btn.setFixedHeight(34)
        self.detect_gpu_btn.setMinimumWidth(120)
        self.detect_gpu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detect_gpu_btn.setStyleSheet(get_secondary_btn_style(theme))
        self.detect_gpu_btn.clicked.connect(self._detect_cuda)
        gpu_grid.addWidget(self.make_field_column("Diagnostics", self.detect_gpu_btn), 0, 2)

        self.gpu_status_label = QLabel("")
        self.gpu_status_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        gpu_layout.addLayout(gpu_grid)
        gpu_layout.addWidget(self.gpu_status_label)
        gpu_layout.addStretch()
        cards_grid.addWidget(gpu_card, 2, 0)

        # Row 2, Right: 6. Auto Quality Control (QC) Card
        qc_card, qc_layout = self._create_card(
            "Auto Quality Control (QC)",
            "Blank page detection, rotation alignment, and separator cleanup"
        )
        toggles_row = QVBoxLayout()
        toggles_row.setSpacing(6)

        self.enable_qc_check = QCheckBox("Enable Auto QC during pipeline")
        self.enable_qc_check.setChecked(self.config.get("enable_qc", True))
        self.enable_qc_check.toggled.connect(self._toggle_qc_fields)
        toggles_row.addWidget(self.enable_qc_check)

        toggles_sub = QHBoxLayout()
        toggles_sub.setSpacing(16)
        self.enable_docsep = QCheckBox("Auto-remove DOCSEP pages")
        self.enable_docsep.setChecked(self.config.get("enable_docsep_removal", True))
        toggles_sub.addWidget(self.enable_docsep)

        self.enable_blank_rm = QCheckBox("Remove blank pages on Finalize")
        self.enable_blank_rm.setChecked(self.config.get("enable_blank_removal", True))
        toggles_sub.addWidget(self.enable_blank_rm)
        toggles_sub.addStretch()
        toggles_row.addLayout(toggles_sub)
        qc_layout.addLayout(toggles_row)

        qc_grid = QGridLayout()
        qc_grid.setHorizontalSpacing(16)
        qc_grid.setVerticalSpacing(10)

        self.qc_blank_spin = NumberStepper(
            min_val=0.1, max_val=10.0, step=0.1,
            initial=self.config.get("qc_blank_threshold", 1.5),
            is_float=True, decimals=1, suffix=" %", theme=theme
        )
        self._steppers.append(self.qc_blank_spin)
        qc_grid.addWidget(self.make_field_column("Blank Threshold", self.qc_blank_spin), 0, 0)

        self.qc_rotation_spin = NumberStepper(
            min_val=10, max_val=100, step=5,
            initial=self.config.get("qc_rotation_threshold", 65),
            suffix=" %", theme=theme
        )
        self._steppers.append(self.qc_rotation_spin)
        qc_grid.addWidget(self.make_field_column("Rotation Threshold", self.qc_rotation_spin), 0, 1)

        qc_layout.addLayout(qc_grid)
        cards_grid.addWidget(qc_card, 2, 1)

        # Row 3 (Full Width): 7. Image Enhancement Card
        enh_card, enh_layout = self._create_card(
            "Image Enhancement",
            "Preprocessing and image filtering for scanned document legibility"
        )
        self.enhance_enable_check = QCheckBox("Enable Auto Enhancement (right-click page action)")
        self.enhance_enable_check.setChecked(self.config.get("enhance_enabled", True))
        enh_layout.addWidget(self.enhance_enable_check)

        enh_grid = QGridLayout()
        enh_grid.setHorizontalSpacing(16)
        enh_grid.setVerticalSpacing(10)

        self.enhance_dpi_spin = NumberStepper(
            min_val=0, max_val=600, step=25,
            initial=self.config.get("enhance_dpi", 0),
            special_value_text="Default", suffix=" DPI", theme=theme
        )
        self._steppers.append(self.enhance_dpi_spin)
        enh_grid.addWidget(self.make_field_column("DPI Override", self.enhance_dpi_spin), 0, 0)

        self.enhance_denoise_spin = NumberStepper(
            min_val=0, max_val=20, step=1,
            initial=self.config.get("enhance_denoise_strength", 5),
            theme=theme
        )
        self._steppers.append(self.enhance_denoise_spin)
        enh_grid.addWidget(self.make_field_column("Denoise (0-20)", self.enhance_denoise_spin), 0, 1)

        self.enhance_sharpen_spin = NumberStepper(
            min_val=0.0, max_val=2.0, step=0.1,
            initial=self.config.get("enhance_sharpen_amount", 1.0),
            is_float=True, decimals=1, theme=theme
        )
        self._steppers.append(self.enhance_sharpen_spin)
        enh_grid.addWidget(self.make_field_column("Sharpen (0-2)", self.enhance_sharpen_spin), 0, 2)

        enh_layout.addLayout(enh_grid)
        cards_grid.addWidget(enh_card, 3, 0, 1, 2)

        layout.addLayout(cards_grid)

        layout.addSpacing(8)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._toggle_gpu_fields(self.gpu_combo.currentText())
        self._toggle_qc_fields(self.enable_qc_check.isChecked())
        self.apply_theme(theme)

    def _on_theme_selected(self, theme_name: str):
        self.config["theme"] = theme_name
        self.apply_theme(theme_name)
        self.theme_changed.emit(theme_name)

    def apply_theme(self, theme_name: str):
        self.config["theme"] = theme_name
        p = get_palette_dict(theme_name)
        self.setStyleSheet(f"background-color: {p['bg_window']};")

        if hasattr(self, "page_title"):
            self.page_title.setStyleSheet(f"color: {p['text_main']};")
        if hasattr(self, "page_desc"):
            self.page_desc.setStyleSheet(f"color: {p['text_secondary']};")
        if hasattr(self, "theme_desc_label"):
            self.theme_desc_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        if hasattr(self, "roster_count_label"):
            self.roster_count_label.setStyleSheet(f"color: {p['text_main']}; font-size: 9pt; font-weight: bold;")
        if hasattr(self, "roster_desc"):
            self.roster_desc.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        if hasattr(self, "pattern_label"):
            self.pattern_label.setStyleSheet(f"color: {p['text_accent']}; font-size: 9.5pt;")
        if hasattr(self, "example_label"):
            self.example_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        if hasattr(self, "save_loc_label"):
            self.save_loc_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 9pt; font-weight: 500;")
        if hasattr(self, "gpu_status_label"):
            self.gpu_status_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        if hasattr(self, "save_btn"):
            self.save_btn.setStyleSheet(get_primary_btn_style(theme_name))
        if hasattr(self, "manage_roster_btn"):
            self.manage_roster_btn.setStyleSheet(get_primary_btn_style(theme_name))
        if hasattr(self, "detect_gpu_btn"):
            self.detect_gpu_btn.setStyleSheet(get_secondary_btn_style(theme_name))

        for lbl in getattr(self, "_card_titles", []):
            lbl.setStyleSheet(f"color: {p['text_main']};")
        for lbl in getattr(self, "_card_subtitles", []):
            lbl.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")
        for lbl in getattr(self, "_field_labels", []):
            lbl.setStyleSheet(f"color: {p['text_secondary']}; font-weight: 600; font-size: 8.5pt;")

        card_style = get_dashboard_card_style(theme_name)
        for card in getattr(self, "_cards", []):
            card.setStyleSheet(card_style)

        for stepper in getattr(self, "_steppers", []):
            stepper.apply_theme(theme_name)

        popup_style = get_combobox_popup_style(theme_name)
        for cb_name in ["theme_combo", "ocr_combo", "gpu_combo", "layout_combo"]:
            if hasattr(self, cb_name):
                cb = getattr(self, cb_name)
                cb.view().setStyleSheet(popup_style)

    def _toggle_qc_fields(self, enabled: bool):
        self.enable_docsep.setEnabled(enabled)
        self.enable_blank_rm.setEnabled(enabled)
        self.qc_blank_spin.setEnabled(enabled)
        self.qc_rotation_spin.setEnabled(enabled)

    def _toggle_gpu_fields(self, text: str):
        is_remote = text == "Remote GPU Server"
        self.remote_url_input.setEnabled(is_remote)
        self.detect_gpu_btn.setEnabled(text != "Remote GPU Server")

    def _detect_cuda(self):
        p = get_palette_dict(self.config.get("theme", THEME_LIGHT))
        self.gpu_status_label.setText("GPU acceleration not available (Tesseract only)")
        self.gpu_status_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8.5pt;")

    def _open_roster_manager(self):
        dlg = CompanyRosterDialog(self, theme=self.config.get("theme", THEME_LIGHT))
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
        self.config.pop("qc_mirror_threshold", None)
        self.config["enhance_enabled"] = self.enhance_enable_check.isChecked()
        self.config["enhance_dpi"] = self.enhance_dpi_spin.value()
        self.config["enhance_denoise_strength"] = self.enhance_denoise_spin.value()
        self.config["enhance_sharpen_amount"] = self.enhance_sharpen_spin.value()

        gpu_text = self.gpu_combo.currentText()
        gpu_save_map = {"CPU Only": "cpu", "Local GPU (CUDA)": "local_gpu", "Remote GPU Server": "remote"}
        self.config["gpu_mode"] = gpu_save_map.get(gpu_text, "cpu")
        self.config["remote_gpu_url"] = self.remote_url_input.text().strip()
        self.config["theme"] = self.theme_combo.currentText()

        config_file = DATA_DIR / "config.json"
        ensure_data_dir()
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        QMessageBox.information(self, "Saved", "Settings saved successfully")
