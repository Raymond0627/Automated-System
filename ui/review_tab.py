import os
import json
import fitz
from pathlib import Path
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QScrollArea, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QDialog, QTextEdit, QMessageBox, QFileDialog,
    QMenu, QInputDialog, QFrame, QLineEdit, QCompleter
)
from PyQt6.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap, QImage, QAction, QShortcut, QKeySequence

import sys
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR
from pipeline import (
    PipelineConfig, parse_folder_structure, load_flagged_index,
    update_document_from_review, finalize_all_divisions, save_confirmed_documents
)
from enhance import enhance_page
from company_extractor import normalize_company_name, learn_company_name, load_known_companies, save_known_companies

from .widgets import DocCardWidget, PassedDocCardWidget


class ClickableLabel(QLabel):
    clicked = pyqtSignal(int)

    def __init__(self, page_num, parent=None):
        super().__init__(parent)
        self._page_num = page_num
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._page_num)
        super().mousePressEvent(event)


class FinalizeWorker(QThread):
    done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, passed_docs, all_flagged_docs, config, document_modified, current_pdf_doc, active_index, active_list, pending_docs, keep_input_structure=False):
        super().__init__()
        self.passed_docs = passed_docs
        self.all_flagged_docs = all_flagged_docs
        self.config = config
        self.document_modified = document_modified
        self.current_pdf_doc = current_pdf_doc
        self.active_index = active_index
        self.active_list = active_list
        self.pending_docs = pending_docs
        self.keep_input_structure = keep_input_structure

    def run(self):
        _log = open(Path(__file__).parent.parent / "finalize_debug.log", "a", encoding="utf-8")
        _log.write("=== FinalizeWorker.run START ===\n")
        _log.flush()
        try:
            pipeline_config = PipelineConfig(
                input_root=self.config["input_root"],
                output_root=self.config["output_root"],
                flagged_root=self.config["flagged_root"],
                confidence_threshold=self.config["confidence_threshold"],
                page_index=self.config["page_index"],
                earliest_year=self.config["earliest_year"],
                ocr_engine=self.config.get("ocr_engine", "tesseract"),
            )
            pipeline_config.enable_docsep_removal = self.config.get("enable_docsep_removal", True)
            pipeline_config.enable_blank_removal = self.config.get("enable_blank_removal", True)
            pipeline_config.keep_input_structure = self.keep_input_structure

            def _norm(p):
                return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""

            _log.write("Parsing folder structure...\n")
            _log.flush()
            batches = parse_folder_structure(pipeline_config.input_root)

            _log.write("Matching passed docs...\n")
            _log.flush()
            passed_lookup = {_norm(pd.get("original_path")): pd for pd in self.passed_docs if pd.get("original_path")}

            matched_doc_paths = set()
            for batch in batches:
                for doc in batch.documents:
                    pd = passed_lookup.get(_norm(doc.original_path))
                    if pd:
                        matched_doc_paths.add(_norm(doc.original_path))
                        try:
                            doc.confirmed_date = date.fromisoformat(pd["detected_date"])
                            doc.confirmed_method = pd.get("method", "auto")
                            doc.status = "confirmed"
                            if pd.get("modified_path"):
                                doc.original_path = pd["modified_path"]
                            doc.blank_pages = pd.get("blank_pages", [])
                            doc.docsep_pages = pd.get("docsep_pages", [])
                            if pd.get("company_name"):
                                doc.company_name = pd["company_name"]
                        except (ValueError, KeyError):
                            pass

            unmatched_passed = [pd.get("original_path") for pd in self.passed_docs
                                if _norm(pd.get("original_path")) not in matched_doc_paths]
            if unmatched_passed:
                print(f"[Finalize] WARNING: {len(unmatched_passed)}/{len(self.passed_docs)} "
                      f"passed docs did not match any parsed batch doc:")
                for p in unmatched_passed:
                    print("   MISSING MATCH:", p)

            _log.write("Loading flagged data...\n")
            _log.flush()
            flagged_data = load_flagged_index(pipeline_config)
            flagged_lookup = {_norm(fd.get("original_path")): fd for fd in flagged_data if fd.get("original_path")}
            for batch in batches:
                for doc in batch.documents:
                    fd = flagged_lookup.get(_norm(doc.original_path))
                    if fd:
                        update_document_from_review(doc, fd)

            _log.write("Finalizing all divisions...\n")
            _log.flush()
            finalize_all_divisions(batches, pipeline_config)
            _log.write("Finalize ALL DIVISIONS DONE\n")
            _log.flush()

            _log.write("Saving confirmed documents...\n")
            _log.flush()
            save_confirmed_documents(batches, pipeline_config)
            _log.write("SAVE CONFIRMED DOCUMENTS DONE\n")
            _log.flush()

            _log.write("Clearing index files...\n")
            _log.flush()
            for fname in ("passed_index.json", "flagged_index.json"):
                p = Path(self.config["flagged_root"]) / fname
                if p.exists():
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump([], f)

            _log.write("Emitting done signal...\n")
            _log.flush()
            self.done.emit()
            _log.write("Done signal emitted\n")
            _log.flush()
        except Exception as e:
            _log.write(f"EXCEPTION in run: {e}\n")
            import traceback
            _log.write(traceback.format_exc() + "\n")
            _log.flush()
            self.error.emit(str(e))
        _log.write("=== FinalizeWorker.run END ===\n")
        _log.close()


class ReviewTab(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.all_flagged_docs = []
        self.pending_docs = []
        self.passed_docs = []
        self.active_list = None
        self.active_index = -1
        self.current_pdf_doc = None
        self.pending_cards = []
        self.passed_cards = []
        self.ocr_dialog = None
        self.reviewed_passed = set()
        self.zoom_level = 100
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self.render_preview)
        self.page_labels = []
        self.document_modified = False
        self.undo_stack = []
        self.undo_max = 50
        self.view_mode = "one_page"
        self.variable_pages_per_row = 3
        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(6, 6, 4, 6)
        left_layout.setSpacing(4)

        self.month_combo = QComboBox()
        self.month_combo.addItems(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        self.month_combo.setFixedHeight(26)
        self.month_combo.setFont(QFont("Segoe UI", 9))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(1900, 2100)
        self.year_spin.setValue(2024)
        self.year_spin.setFixedHeight(26)
        self.year_spin.setFont(QFont("Segoe UI", 9))
        self.confirm_btn = QPushButton("OK")
        self.confirm_btn.setObjectName("accent")
        self.confirm_btn.setFixedHeight(26)
        self.confirm_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.confirm_btn.clicked.connect(self.confirm_date)
        self.ocr_btn = QPushButton("OCR")
        self.ocr_btn.setFixedHeight(26)
        self.ocr_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.ocr_btn.clicked.connect(self.toggle_ocr_panel)

        self.add_roster_btn = QPushButton("Add to Roster")
        self.add_roster_btn.setFixedHeight(26)
        self.add_roster_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.add_roster_btn.clicked.connect(self._add_to_roster)

        self.company_input = QLineEdit()
        self.company_input.setFixedHeight(26)
        self.company_input.setFont(QFont("Segoe UI", 9))
        self.company_input.setMinimumWidth(200)
        self.company_input.setPlaceholderText("Company name...")

        known_companies = load_known_companies()
        self._company_completer = QCompleter(known_companies, self)
        self._company_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._company_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._company_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.company_input.setCompleter(self._company_completer)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(4)
        nav_row.setContentsMargins(0, 0, 0, 4)
        self.doc_nav_label = QLabel("No docs")
        self.doc_nav_label.setFont(QFont("Segoe UI", 8))
        self.doc_nav_label.setStyleSheet("color: #8888aa;")
        nav_row.addWidget(self.doc_nav_label, 1)
        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedSize(48, 24)
        self.prev_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.prev_btn.clicked.connect(self.prev_document)
        nav_row.addWidget(self.prev_btn)
        self.next_btn = QPushButton(">")
        self.next_btn.setFixedSize(48, 24)
        self.next_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.next_btn.clicked.connect(self.next_document)
        nav_row.addWidget(self.next_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedSize(80, 24)
        self.refresh_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.refresh_btn.clicked.connect(self.refresh_review)
        nav_row.addWidget(self.refresh_btn)
        left_layout.addLayout(nav_row)

        list_splitter = QSplitter(Qt.Orientation.Vertical)
        list_splitter.setHandleWidth(4)

        pending_container = QWidget()
        pending_layout = QVBoxLayout(pending_container)
        pending_layout.setContentsMargins(0, 0, 0, 0)
        pending_layout.setSpacing(2)
        self.pending_label = QLabel("Pending Review (0)")
        self.pending_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.pending_label.setStyleSheet("color: #ff9800; padding: 2px 4px;")
        pending_layout.addWidget(self.pending_label)
        self.pending_list = QListWidget()
        self.pending_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pending_list.currentRowChanged.connect(self._on_pending_selected)
        pending_layout.addWidget(self.pending_list, 1)
        list_splitter.addWidget(pending_container)

        passed_container = QWidget()
        passed_layout = QVBoxLayout(passed_container)
        passed_layout.setContentsMargins(0, 0, 0, 0)
        passed_layout.setSpacing(2)
        self.passed_label = QLabel("Auto-Confirmed (0)")
        self.passed_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.passed_label.setStyleSheet("color: #4caf50; padding: 2px 4px;")
        passed_layout.addWidget(self.passed_label)
        self.passed_list = QListWidget()
        self.passed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.passed_list.currentRowChanged.connect(self._on_passed_selected)
        passed_layout.addWidget(self.passed_list, 1)
        list_splitter.addWidget(passed_container)

        list_splitter.setSizes([250, 250])
        left_layout.addWidget(list_splitter, 1)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 6, 6, 6)
        right_layout.setSpacing(4)

        preview_group = QGroupBox("Document Preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setSpacing(2)

        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        info_row.addStretch()

        self.page_label = QLabel("No document loaded")
        self.page_label.setFont(QFont("Segoe UI", 8))
        self.page_label.setStyleSheet("color: #8888aa;")
        info_row.addWidget(self.page_label)
        preview_layout.addLayout(info_row)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_scroll.setStyleSheet("QScrollArea { background-color: #12131f; border: 1px solid #2d2e45; border-radius: 6px; }")
        self.preview_scroll.installEventFilter(self)

        self.preview_container = QWidget()
        self.preview_container.setStyleSheet("background-color: #12131f;")
        self.preview_container.installEventFilter(self)
        self.preview_container_layout = QVBoxLayout(self.preview_container)
        self.preview_container_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_container_layout.setContentsMargins(20, 10, 20, 10)
        self.preview_container_layout.setSpacing(8)

        self.preview_placeholder = QLabel("Select a document to preview")
        self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_placeholder.setStyleSheet("color: #555570; font-size: 12pt;")
        self.preview_placeholder.setMinimumHeight(300)
        self.preview_container_layout.addWidget(self.preview_placeholder)

        self.preview_scroll.setWidget(self.preview_container)
        preview_layout.addWidget(self.preview_scroll, 1)

        right_layout.addWidget(preview_group, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([260, 740])

        main_layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 1, 12, 15)
        self.count_label = QLabel("No documents")
        self.count_label.setStyleSheet("color: #8888aa;")
        bottom.addWidget(self.count_label)
        sep = QLabel("|")
        sep.setStyleSheet("color: #333455; padding: 0 4px;")
        bottom.addWidget(sep)
        bottom.addWidget(QLabel("Date:"))
        bottom.addWidget(self.month_combo)
        bottom.addWidget(self.year_spin)
        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #333455; padding: 0 4px;")
        bottom.addWidget(sep2)
        bottom.addWidget(QLabel("Company:"))
        bottom.addWidget(self.company_input)
        bottom.addWidget(self.confirm_btn)
        bottom.addWidget(self.add_roster_btn)
        bottom.addWidget(self.ocr_btn)
        bottom.addStretch()
        self.finalize_btn = QPushButton("FINALIZE")
        self.finalize_btn.setObjectName("success")
        self.finalize_btn.setFixedHeight(26)
        self.finalize_btn.setFont(QFont("Segoe UI", 8))
        self.finalize_btn.clicked.connect(self.finalize_all)
        bottom.addWidget(self.finalize_btn)
        main_layout.addLayout(bottom)

        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self._undo)

    def refresh_review(self):
        _prev_flagged = list(self.all_flagged_docs) if self.all_flagged_docs else []
        _prev_passed = list(self.passed_docs) if self.passed_docs else []

        self.pending_list.clear()
        self.passed_list.clear()
        self.pending_cards = []
        self.passed_cards = []
        self.active_list = None
        self.active_index = -1
        self.current_pdf_doc = None
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []

        flagged_file = Path(self.config["flagged_root"]) / "flagged_index.json"
        if flagged_file.exists():
            try:
                with open(flagged_file, "r", encoding="utf-8") as f:
                    all_flagged = json.load(f)
                if not all_flagged:
                    all_flagged = _prev_flagged
            except Exception:
                all_flagged = _prev_flagged
        else:
            all_flagged = _prev_flagged

        self.all_flagged_docs = all_flagged
        self.pending_docs = [d for d in all_flagged if not d.get("reviewed")]

        passed_file = Path(self.config["flagged_root"]) / "passed_index.json"
        if passed_file.exists():
            try:
                with open(passed_file, "r", encoding="utf-8") as f:
                    self.passed_docs = json.load(f)
                if not self.passed_docs:
                    self.passed_docs = _prev_passed
            except Exception:
                self.passed_docs = _prev_passed
        else:
            self.passed_docs = _prev_passed

        for i, doc in enumerate(self.pending_docs):
            card = DocCardWidget(doc, i)
            card.clicked.connect(self._on_pending_card_clicked)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 62))
            self.pending_list.addItem(item)
            self.pending_list.setItemWidget(item, card)
            self.pending_cards.append(card)

        for i, doc in enumerate(self.passed_docs):
            card = PassedDocCardWidget(doc, i)
            card.clicked.connect(self._on_passed_card_clicked)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 62))
            self.passed_list.addItem(item)
            self.passed_list.setItemWidget(item, card)
            self.passed_cards.append(card)
            if i in self.reviewed_passed:
                card.set_reviewed(True)

        self.pending_label.setText(f"Pending Review ({len(self.pending_docs)})")
        self.passed_label.setText(f"Auto-Confirmed ({len(self.passed_docs)})")
        self.count_label.setText(f"{len(self.pending_docs)} pending | {len(self.passed_docs)} passed")

        if self.pending_docs:
            self.pending_list.setCurrentRow(0)

    def clear_all(self):
        self.pending_list.blockSignals(True)
        self.passed_list.blockSignals(True)
        self.pending_list.clear()
        self.passed_list.clear()
        self.pending_cards = []
        self.passed_cards = []
        self.all_flagged_docs = []
        self.pending_docs = []
        self.passed_docs = []
        self.active_list = None
        self.active_index = -1
        if self.current_pdf_doc:
            try:
                self.current_pdf_doc.close()
            except Exception:
                pass
            self.current_pdf_doc = None
        self.undo_stack.clear()
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []
        self.preview_placeholder.setText("Select a document to preview")
        self.preview_placeholder.setStyleSheet("color: #555570; font-size: 12pt;")
        self.pending_label.setText("Pending Review (0)")
        self.passed_label.setText("Auto-Confirmed (0)")
        self.count_label.setText("0 pending | 0 passed")
        self.doc_nav_label.setText("No docs")
        self.page_label.setText("No document loaded")
        self.pending_list.blockSignals(False)
        self.passed_list.blockSignals(False)

    def add_doc(self, doc_type: str, data: dict):
        if doc_type == "pending":
            self.all_flagged_docs.append(data)
            idx = len(self.pending_docs)
            self.pending_docs.append(data)
            card = DocCardWidget(data, idx)
            card.clicked.connect(self._on_pending_card_clicked)
            self.pending_list.blockSignals(True)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 62))
            self.pending_list.addItem(item)
            self.pending_list.setItemWidget(item, card)
            self.pending_list.blockSignals(False)
            self.pending_cards.append(card)
            self.pending_label.setText(f"Pending Review ({len(self.pending_docs)})")
        elif doc_type == "passed":
            idx = len(self.passed_docs)
            self.passed_docs.append(data)
            card = PassedDocCardWidget(data, idx)
            card.clicked.connect(self._on_passed_card_clicked)
            self.passed_list.blockSignals(True)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 62))
            self.passed_list.addItem(item)
            self.passed_list.setItemWidget(item, card)
            self.passed_list.blockSignals(False)
            self.passed_cards.append(card)
            self.passed_label.setText(f"Auto-Confirmed ({len(self.passed_docs)})")
        self.count_label.setText(f"{len(self.pending_docs)} pending | {len(self.passed_docs)} passed")

    def _on_pending_card_clicked(self, idx: int):
        self.pending_list.setCurrentRow(idx)

    def _on_passed_card_clicked(self, idx: int):
        self.passed_list.setCurrentRow(idx)

    def _on_pending_selected(self, row: int):
        if row < 0:
            return
        self.active_list = "pending"
        self.active_index = row
        for i, card in enumerate(self.pending_cards):
            card.set_selected(i == row)
        for i, card in enumerate(self.passed_cards):
            card.set_selected(False)
            if i in self.reviewed_passed:
                card.set_reviewed(True)
        self.load_document(self.pending_docs[row])

    def _on_passed_selected(self, row: int):
        if row < 0:
            return
        self.active_list = "passed"
        self.active_index = row
        self.reviewed_passed.add(row)
        for i, card in enumerate(self.passed_cards):
            card.set_selected(i == row)
            if i in self.reviewed_passed:
                card.set_reviewed(True)
        for card in self.pending_cards:
            card.set_selected(False)
        self.load_document(self.passed_docs[row])

    def load_document(self, doc: dict):
        if self.current_pdf_doc:
            try:
                self.current_pdf_doc.close()
            except Exception:
                pass
            self.current_pdf_doc = None

        self.current_page = 0
        self.document_modified = False
        self.undo_stack.clear()

        total = len(self.pending_docs) + len(self.passed_docs)
        if self.active_list == "pending":
            self.doc_nav_label.setText(f"Pending {self.active_index + 1} of {len(self.pending_docs)}")
        else:
            self.doc_nav_label.setText(f"Passed {self.active_index + 1} of {len(self.passed_docs)}")

        mod_path = doc.get("modified_path", "")
        pdf_path = mod_path if (mod_path and os.path.exists(mod_path)) else doc.get("original_path", "")
        loaded = False
        if pdf_path and os.path.exists(pdf_path):
            try:
                self.current_pdf_doc = fitz.open(pdf_path)
                self.total_pages = len(self.current_pdf_doc)
                loaded = True
            except Exception:
                pass

        if not loaded:
            flagged_pdf = Path(self.config["flagged_root"]) / doc.get("division_code", "") / doc.get("company_name", "") / doc.get("original_filename", "")
            if flagged_pdf.exists():
                try:
                    self.current_pdf_doc = fitz.open(str(flagged_pdf))
                    self.total_pages = len(self.current_pdf_doc)
                    loaded = True
                except Exception:
                    pass

        if loaded:
            self.render_preview()
        else:
            self.preview_placeholder.setText("PDF not found")
            self.preview_placeholder.setStyleSheet("color: #ff5555; font-size: 12pt;")
            self.page_label.setText("PDF not found")

        if self.active_list == "passed":
            date_str = doc.get("detected_date", "")
        else:
            date_str = doc.get("best_guess_date", "") or doc.get("confirmed_date", "")

        if date_str:
            self._set_date_to_widgets(date_str)

        company_name = doc.get("company_name", "")
        self._set_company_to_widgets(company_name)

    def _set_date_to_widgets(self, date_str: str):
        if date_str and len(date_str) >= 7:
            try:
                parts = date_str.split("-")
                year = int(parts[0])
                month = int(parts[1])
                self.year_spin.setValue(year)
                self.month_combo.setCurrentIndex(month - 1)
            except (ValueError, IndexError):
                pass

    def _get_date_from_widgets(self) -> str:
        month = self.month_combo.currentIndex() + 1
        year = self.year_spin.value()
        return f"{year}-{month:02d}-01"

    def _set_company_to_widgets(self, company_name: str):
        self.company_input.setText(company_name or "")

    def _get_company_from_widgets(self) -> str:
        return self.company_input.text().strip()

    def _get_blank_pages(self):
        if self.active_list == "pending" and self.active_index >= 0:
            return self.pending_docs[self.active_index].get("blank_pages", [])
        elif self.active_list == "passed" and self.active_index >= 0:
            return self.passed_docs[self.active_index].get("blank_pages", [])
        return []

    def _get_docsep_pages(self):
        if self.active_list == "pending" and self.active_index >= 0:
            return self.pending_docs[self.active_index].get("docsep_pages", [])
        elif self.active_list == "passed" and self.active_index >= 0:
            return self.passed_docs[self.active_index].get("docsep_pages", [])
        return []

    def _create_page_label(self, page_num, pixmap, blank_pages, docsep_pages):
        lbl = ClickableLabel(page_num)
        lbl.setPixmap(pixmap)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl.customContextMenuRequested.connect(lambda pos, pn=page_num, lb=lbl: self._show_page_menu(pos, pn, lb))

        is_blank = page_num in blank_pages
        is_docsep = page_num in docsep_pages

        if is_blank:
            lbl.setStyleSheet("background-color: #1e1f35; border: 3px solid #ff4444; border-radius: 4px; padding: 4px;")
            blank_badge = QLabel("BLANK", lbl)
            blank_badge.setStyleSheet("background-color: #ff4444; color: white; font-weight: bold; font-size: 10px; padding: 2px 8px; border-radius: 3px;")
            blank_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            blank_badge.move(8, 8)
            blank_badge.adjustSize()
            blank_badge.show()
        elif is_docsep:
            lbl.setStyleSheet("background-color: #1e1f35; border: 3px solid #ffab00; border-radius: 4px; padding: 4px;")
            docsep_badge = QLabel("DOCSEP", lbl)
            docsep_badge.setStyleSheet("background-color: #ffab00; color: #1e1f35; font-weight: bold; font-size: 10px; padding: 2px 8px; border-radius: 3px;")
            docsep_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            docsep_badge.move(8, 8)
            docsep_badge.adjustSize()
            docsep_badge.show()
        else:
            lbl.setStyleSheet("background-color: #1e1f35; border: 1px solid #3a3b55; border-radius: 4px; padding: 4px;")

        return lbl

    def _make_page_pixmap(self, page_num, page_width):
        page = self.current_pdf_doc[page_num]
        dpi = max(25, int(150 * (self.zoom_level / 100)))
        pix = page.get_pixmap(dpi=dpi)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(img)
        if pixmap.width() > page_width:
            pixmap = pixmap.scaledToWidth(page_width, Qt.TransformationMode.SmoothTransformation)
        return pixmap

    def render_preview(self):
        if not self.current_pdf_doc:
            return
        try:
            for lbl in self.page_labels:
                lbl.hide()
                lbl.deleteLater()
            self.page_labels = []

            while self.preview_container_layout.count():
                item = self.preview_container_layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()

            total_pages = len(self.current_pdf_doc)
            blank_pages = self._get_blank_pages()
            docsep_pages = self._get_docsep_pages()
            preview_width = max(400, self.preview_scroll.viewport().width() - 50)

            if self.view_mode == "one_page":
                for page_num in range(total_pages):
                    pixmap = self._make_page_pixmap(page_num, preview_width)
                    lbl = self._create_page_label(page_num, pixmap, blank_pages, docsep_pages)
                    self.preview_container_layout.addWidget(lbl)
                    self.page_labels.append(lbl)

            elif self.view_mode == "two_pages":
                col_width = (preview_width - 8) // 2
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                for page_num in range(total_pages):
                    pixmap = self._make_page_pixmap(page_num, col_width)
                    lbl = self._create_page_label(page_num, pixmap, blank_pages, docsep_pages)
                    row.addWidget(lbl)
                    self.page_labels.append(lbl)
                    if len(self.page_labels) % 2 == 0 or page_num == total_pages - 1:
                        row_container = QWidget()
                        items_in_row = 2 if len(self.page_labels) % 2 == 0 else 1
                        content_width = col_width * items_in_row + 8 * (items_in_row - 1)
                        row_container.setFixedWidth(content_width)
                        row_container.setLayout(row)
                        self.preview_container_layout.addWidget(row_container, alignment=Qt.AlignmentFlag.AlignHCenter)
                        row = QHBoxLayout()
                        row.setContentsMargins(0, 0, 0, 0)
                        row.setSpacing(8)

            elif self.view_mode == "two_pages_cover":
                if total_pages > 0:
                    pixmap = self._make_page_pixmap(0, preview_width)
                    lbl = self._create_page_label(0, pixmap, blank_pages, docsep_pages)
                    self.preview_container_layout.addWidget(lbl)
                    self.page_labels.append(lbl)
                col_width = (preview_width - 8) // 2
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                for page_num in range(1, total_pages):
                    pixmap = self._make_page_pixmap(page_num, col_width)
                    lbl = self._create_page_label(page_num, pixmap, blank_pages, docsep_pages)
                    row.addWidget(lbl)
                    self.page_labels.append(lbl)
                    pages_in_row = len([w for w in row.children() if hasattr(w, 'pixmap')])
                    is_last = page_num == total_pages - 1
                    if is_last or pages_in_row == 2:
                        row_container = QWidget()
                        content_width = col_width * pages_in_row + 8 * (pages_in_row - 1)
                        row_container.setFixedWidth(content_width)
                        row_container.setLayout(row)
                        self.preview_container_layout.addWidget(row_container, alignment=Qt.AlignmentFlag.AlignHCenter)
                        row = QHBoxLayout()
                        row.setContentsMargins(0, 0, 0, 0)
                        row.setSpacing(8)

            elif self.view_mode == "variable":
                target_page_width = max(150, 400 * self.zoom_level / 100)
                n = max(1, int(preview_width / target_page_width))
                col_width = (preview_width - 8 * (n - 1)) // n
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                count = 0
                for page_num in range(total_pages):
                    pixmap = self._make_page_pixmap(page_num, col_width)
                    lbl = self._create_page_label(page_num, pixmap, blank_pages, docsep_pages)
                    row.addWidget(lbl)
                    self.page_labels.append(lbl)
                    count += 1
                    if count == n or page_num == total_pages - 1:
                        row_container = QWidget()
                        content_width = col_width * count + 8 * (count - 1)
                        row_container.setFixedWidth(content_width)
                        row_container.setLayout(row)
                        self.preview_container_layout.addWidget(row_container, alignment=Qt.AlignmentFlag.AlignHCenter)
                        row = QHBoxLayout()
                        row.setContentsMargins(0, 0, 0, 0)
                        row.setSpacing(8)
                        count = 0

            blank_count = len([p for p in blank_pages if p < total_pages])
            docsep_count = len([p for p in docsep_pages if p < total_pages])
            status = f"{total_pages} page(s)"
            if blank_count:
                status += f" | {blank_count} blank"
            if docsep_count:
                status += f" | {docsep_count} DOCSEP"
            status += f" | Zoom: {self.zoom_level}%  (Ctrl+Scroll to zoom)"
            self.page_label.setText(status)
        except Exception as e:
            self.preview_placeholder.setText(f"Error rendering: {e}")
            self.preview_placeholder.setStyleSheet("color: #ff5555; font-size: 12pt;")

    def _show_page_menu(self, pos, page_num, lbl):
        if not self.current_pdf_doc:
            return
        menu = QMenu(self)
        delete_act = QAction("Delete Page", self)
        delete_act.triggered.connect(lambda: self._delete_page(page_num))
        menu.addAction(delete_act)
        menu.addSeparator()
        rot_l = QAction("Rotate Left", self)
        rot_l.triggered.connect(lambda: self._rotate_page(page_num, -90))
        menu.addAction(rot_l)
        rot_r = QAction("Rotate Right", self)
        rot_r.triggered.connect(lambda: self._rotate_page(page_num, 90))
        menu.addAction(rot_r)
        menu.addSeparator()
        flip_h = QAction("Flip Horizontal", self)
        flip_h.triggered.connect(lambda: self._flip_page(page_num, "h"))
        menu.addAction(flip_h)
        flip_v = QAction("Flip Vertical", self)
        flip_v.triggered.connect(lambda: self._flip_page(page_num, "v"))
        menu.addAction(flip_v)
        menu.addSeparator()
        enhance_act = QAction("Auto Enhance Page", self)
        enhance_act.triggered.connect(lambda: self._enhance_page(page_num))
        menu.addAction(enhance_act)
        menu.addSeparator()
        mark_blank = QAction("Mark as Blank Page", self)
        mark_blank.triggered.connect(lambda: self._mark_blank(page_num))
        menu.addAction(mark_blank)
        remove_mark = QAction("Remove Mark", self)
        remove_mark.triggered.connect(lambda: self._remove_mark(page_num))
        menu.addAction(remove_mark)
        menu.addSeparator()
        ins_before = QAction(f"Insert Before Page {page_num + 1}", self)
        ins_before.triggered.connect(lambda: self._insert_image_page(page_num, "before"))
        menu.addAction(ins_before)
        ins_after = QAction(f"Insert After Page {page_num + 1}", self)
        ins_after.triggered.connect(lambda: self._insert_image_page(page_num, "after"))
        menu.addAction(ins_after)
        menu.addSeparator()
        view_menu = menu.addMenu("View Mode")
        view_modes = [
            ("One page", "one_page"),
            ("Two pages", "two_pages"),
            ("Two pages with cover sheet", "two_pages_cover"),
            ("Variable number of pages", "variable"),
        ]
        for label, mode in view_modes:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(self.view_mode == mode)
            act.triggered.connect(lambda checked, m=mode: self._set_view_mode(m))
            view_menu.addAction(act)
        menu.exec(lbl.mapToGlobal(pos))

    def _set_view_mode(self, mode):
        self.view_mode = mode
        self.render_preview()

    def _delete_page(self, page_num):
        if not self.current_pdf_doc or len(self.current_pdf_doc) <= 1:
            QMessageBox.warning(self, "Cannot Delete", "Cannot delete the only remaining page.")
            return
        self._push_undo()
        self.current_pdf_doc.delete_page(page_num)
        self.document_modified = True
        self.render_preview()

    def _rotate_page(self, page_num, degrees):
        if not self.current_pdf_doc:
            return
        self._push_undo()
        page = self.current_pdf_doc[page_num]
        current = page.rotation or 0
        page.set_rotation((current + degrees) % 360)
        self.document_modified = True
        self.render_preview()

    def _flip_page(self, page_num, direction):
        if not self.current_pdf_doc:
            return
        import cv2
        import numpy as np
        import tempfile
        self._push_undo()
        try:
            page = self.current_pdf_doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            if direction == "h":
                img = cv2.flip(img, 1)
            else:
                img = cv2.flip(img, 0)
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            cv2.imwrite(tmp.name, img_rgb)
            page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
            rect = page.rect
            img_pix = fitz.Pixmap(tmp.name)
            scale = min(rect.width / img_pix.width, rect.height / img_pix.height)
            w, h = int(img_pix.width * scale), int(img_pix.height * scale)
            x = (rect.width - w) / 2
            y = (rect.height - h) / 2
            page.insert_image(fitz.Rect(x, y, x + w, y + h), pixmap=img_pix)
            img_pix = None
            tmp.close()
            os.unlink(tmp.name)
            self.document_modified = True
            self.render_preview()
        except Exception as e:
            QMessageBox.critical(self, "Flip Error", f"Failed to flip page:\n{e}")

    def _enhance_page(self, page_num):
        if not self.current_pdf_doc:
            return
        self._push_undo()
        try:
            page = self.current_pdf_doc[page_num]
            dpi = self.config.get("render_dpi", 150)
            enhance_page(page, dpi=dpi, config=self.config)
            self.document_modified = True
            self.render_preview()
        except Exception as e:
            QMessageBox.critical(self, "Enhance Error", f"Failed to enhance page:\n{e}")

    def _get_current_doc(self):
        if self.active_list == "pending" and self.active_index >= 0:
            return self.pending_docs[self.active_index]
        elif self.active_list == "passed" and self.active_index >= 0:
            return self.passed_docs[self.active_index]
        return None

    def _mark_blank(self, page_num):
        doc = self._get_current_doc()
        if not doc:
            return
        self._push_undo()
        blank_pages = doc.setdefault("blank_pages", [])
        if page_num not in blank_pages:
            blank_pages.append(page_num)
        docsep_pages = doc.get("docsep_pages", [])
        if page_num in docsep_pages:
            docsep_pages.remove(page_num)
        self.render_preview()

    def _remove_mark(self, page_num):
        doc = self._get_current_doc()
        if not doc:
            return
        self._push_undo()
        blank_pages = doc.get("blank_pages", [])
        if page_num in blank_pages:
            blank_pages.remove(page_num)
        docsep_pages = doc.get("docsep_pages", [])
        if page_num in docsep_pages:
            docsep_pages.remove(page_num)
        self.render_preview()

    def _insert_image_page(self, page_num, position):
        if not self.current_pdf_doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            self._push_undo()
            img_pix = fitz.Pixmap(path)
            w, h = img_pix.width, img_pix.height
            aspect = min(612 / w, 792 / h)
            w, h = int(w * aspect), int(h * aspect)
            insert_at = page_num + 1 if position == "after" else page_num
            new_page = self.current_pdf_doc.new_page(insert_at, width=612, height=792)
            img_rect = fitz.Rect((612 - w) / 2, (792 - h) / 2, (612 + w) / 2, (792 + h) / 2)
            new_page.insert_image(img_rect, pixmap=img_pix)
            self.document_modified = True
            self.render_preview()
        except Exception as e:
            QMessageBox.critical(self, "Insert Error", f"Failed to insert image:\n{e}")

    def _push_undo(self):
        if not self.current_pdf_doc:
            return
        doc = self._get_current_doc()
        state = {
            "pdf": self.current_pdf_doc.tobytes(),
            "blank_pages": list(doc.get("blank_pages", [])) if doc else [],
            "docsep_pages": list(doc.get("docsep_pages", [])) if doc else [],
        }
        self.undo_stack.append(state)
        if len(self.undo_stack) > self.undo_max:
            self.undo_stack.pop(0)

    def _undo(self):
        if not self.undo_stack or not self.current_pdf_doc:
            return
        state = self.undo_stack.pop()
        self.current_pdf_doc.close()
        self.current_pdf_doc = fitz.open("pdf", state["pdf"])
        doc = self._get_current_doc()
        if doc:
            doc["blank_pages"] = state["blank_pages"]
            doc["docsep_pages"] = state["docsep_pages"]
        self.document_modified = True
        self.render_preview()

    def zoom_in(self):
        if self.zoom_level < 300:
            self.zoom_level = min(300, self.zoom_level + 5)
            self._zoom_timer.start(50)

    def zoom_out(self):
        if self.zoom_level > 1:
            self.zoom_level = max(1, self.zoom_level - 5)
            self._zoom_timer.start(50)

    def zoom_reset(self):
        self.zoom_level = 100
        self.render_preview()

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_in()
                elif delta < 0:
                    self.zoom_out()
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def prev_document(self):
        if self.active_list == "pending" and self.active_index > 0:
            self.pending_list.setCurrentRow(self.active_index - 1)
        elif self.active_list == "passed" and self.active_index > 0:
            self.passed_list.setCurrentRow(self.active_index - 1)

    def next_document(self):
        if self.active_list == "pending" and self.active_index < len(self.pending_docs) - 1:
            self.pending_list.setCurrentRow(self.active_index + 1)
        elif self.active_list == "passed" and self.active_index < len(self.passed_docs) - 1:
            self.passed_list.setCurrentRow(self.active_index + 1)

    def _add_to_roster(self):
        company_text = self._get_company_from_widgets()
        if not company_text:
            QMessageBox.information(self, "No Company Name", "Enter a company name in the Company field first.")
            return
        normalized = normalize_company_name(company_text)
        companies = load_known_companies()
        if normalized in companies:
            QMessageBox.information(self, "Already Exists", f"'{normalized}' is already in the roster.")
            return
        companies.append(normalized)
        save_known_companies(companies)
        self._company_completer.model().setStringList(companies)
        QMessageBox.information(self, "Added", f"'{normalized}' added to the company roster.")

    def confirm_date(self):
        if self.active_index < 0 or not self.active_list:
            return
        dt = self._get_date_from_widgets()
        company_text = self._get_company_from_widgets()

        if self.active_list == "pending":
            doc = self.pending_docs[self.active_index]
            original_company = doc.get("company_name", "")
            if company_text and company_text != original_company:
                confirmed_company = normalize_company_name(company_text)
            else:
                confirmed_company = original_company

            doc["confirmed_date"] = dt
            doc["company_name"] = confirmed_company
            doc["reviewed"] = True
            doc["review_timestamp"] = str(datetime.now())
            self._save_flagged_updates()

            card = self.pending_cards[self.active_index]
            card.set_selected(False)

            self.passed_docs.append({
                "original_path": doc.get("original_path", ""),
                "division_code": doc.get("division_code", ""),
                "company_name": confirmed_company,
                "original_filename": doc.get("original_filename", ""),
                "detected_date": dt,
                "confidence": doc.get("confidence", 0),
                "method": "manual",
                "blank_pages": doc.get("blank_pages", []),
                "docsep_pages": doc.get("docsep_pages", []),
                "final_filename": "",
            })

            if self.document_modified and self.current_pdf_doc:
                mod_path = Path(self.config["flagged_root"]) / doc.get("division_code", "") / confirmed_company / doc.get("original_filename", "")
                mod_path.parent.mkdir(parents=True, exist_ok=True)
                self.current_pdf_doc.save(str(mod_path), incremental=False, garbage=4, deflate=True)
                self.passed_docs[-1]["modified_path"] = str(mod_path)

            self._save_passed_updates()

            self.refresh_review()

        elif self.active_list == "passed":
            doc = self.passed_docs[self.active_index]
            original_company = doc.get("company_name", "")
            if company_text and company_text != original_company:
                confirmed_company = normalize_company_name(company_text)
            else:
                confirmed_company = original_company

            doc["detected_date"] = dt
            doc["company_name"] = confirmed_company
            if self.document_modified and self.current_pdf_doc:
                mod_path = Path(self.config["flagged_root"]) / doc.get("division_code", "") / confirmed_company / doc.get("original_filename", "")
                mod_path.parent.mkdir(parents=True, exist_ok=True)
                self.current_pdf_doc.save(str(mod_path), incremental=False, garbage=4, deflate=True)
                doc["modified_path"] = str(mod_path)
            self._save_passed_updates()

            card = self.passed_cards[self.active_index]
            card.set_selected(False)

            self.refresh_review()

    def toggle_ocr_panel(self):
        if self.active_index < 0 or not self.active_list:
            return
        if self.active_list == "pending":
            doc = self.pending_docs[self.active_index]
        else:
            doc = self.passed_docs[self.active_index]
        raw = doc.get("raw_ocr_text", "")

        if self.ocr_dialog:
            self.ocr_dialog.close()
            self.ocr_dialog = None
            self.ocr_btn.setText("OCR")
            return

        self.ocr_btn.setText("Hide OCR")
        self.ocr_dialog = QDialog(self)
        self.ocr_dialog.setWindowTitle(f"OCR Text - {doc.get('original_filename', '')}")
        self.ocr_dialog.setMinimumSize(600, 400)
        self.ocr_dialog.setStyleSheet("""
            QDialog { background-color: #1a1b2e; }
            QTextEdit { background-color: #12131f; color: #a0ff90; border: 1px solid #2d2e45; border-radius: 6px; }
        """)

        layout = QVBoxLayout(self.ocr_dialog)
        text_edit = QTextEdit()
        text_edit.setPlainText(raw)
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 9))
        layout.addWidget(text_edit)

        self.ocr_dialog.finished.connect(lambda: setattr(self, 'ocr_dialog', None))
        self.ocr_dialog.finished.connect(lambda: self.ocr_btn.setText("OCR"))
        self.ocr_dialog.show()

    def _valid_date(self, s: str) -> bool:
        try:
            parts = s.split("-")
            if len(parts) != 3:
                return False
            date(int(parts[0]), int(parts[1]), int(parts[2]))
            return True
        except (ValueError, IndexError):
            return False

    def _save_flagged_updates(self):
        flagged_file = Path(self.config["flagged_root"]) / "flagged_index.json"
        flagged_file.parent.mkdir(parents=True, exist_ok=True)
        with open(flagged_file, "w", encoding="utf-8") as f:
            json.dump(self.all_flagged_docs, f, indent=2, ensure_ascii=False)

    def _save_passed_updates(self):
        passed_file = Path(self.config["flagged_root"]) / "passed_index.json"
        passed_file.parent.mkdir(parents=True, exist_ok=True)
        with open(passed_file, "w", encoding="utf-8") as f:
            json.dump(self.passed_docs, f, indent=2, ensure_ascii=False)

    def finalize_all(self):
        if not self.passed_docs and not any(d.get("reviewed") for d in self.all_flagged_docs):
            QMessageBox.information(self, "Info", "No documents to finalize")
            return

        total_blank = sum(len(pd.get("blank_pages", [])) for pd in self.passed_docs)
        total_docsep = sum(len(pd.get("docsep_pages", [])) for pd in self.passed_docs)
        msg = f"Finalize {len(self.passed_docs)} document(s)?"
        if total_blank > 0:
            msg += f"\n\n{total_blank} blank page(s) will be removed."
        if total_docsep > 0:
            msg += f"\n{total_docsep} DOCSEP separator page(s) will be removed."
        if total_blank > 0 or total_docsep > 0:
            msg += "\n\nThis action cannot be undone."

        reply = QMessageBox.question(self, "Confirm Finalize", msg)
        if reply != QMessageBox.StandardButton.Yes:
            return

        folder_reply = QMessageBox.question(
            self, "Folder Structure",
            "Auto-create folders by company name?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        keep_input_structure = (folder_reply == QMessageBox.StandardButton.No)

        self.finalize_btn.setEnabled(False)
        self.finalize_btn.setText("Processing...")

        self._finalize_worker = FinalizeWorker(
            passed_docs=list(self.passed_docs),
            all_flagged_docs=list(self.all_flagged_docs),
            config=self.config,
            document_modified=self.document_modified,
            current_pdf_doc=self.current_pdf_doc,
            active_index=self.active_index,
            active_list=self.active_list,
            pending_docs=list(self.pending_docs),
            keep_input_structure=keep_input_structure,
        )
        self._finalize_worker.done.connect(self._on_finalize_done)
        self._finalize_worker.error.connect(self._on_finalize_error)
        self._finalize_worker.start()

    def _on_finalize_done(self):
        import traceback
        _log = open(Path(__file__).parent.parent / "finalize_debug.log", "a", encoding="utf-8")
        _log.write("=== _on_finalize_done START ===\n")
        _log.flush()
        try:
            self.pending_docs = []
            _log.write("1\n"); _log.flush()
            self.passed_docs = []
            _log.write("2\n"); _log.flush()
            self.all_flagged_docs = []
            _log.write("3\n"); _log.flush()
            self.pending_cards = []
            _log.write("4\n"); _log.flush()
            self.passed_cards = []
            _log.write("5\n"); _log.flush()
            self.pending_list.clear()
            _log.write("6\n"); _log.flush()
            self.passed_list.clear()
            _log.write("7\n"); _log.flush()
            self.active_list = None
            _log.write("8\n"); _log.flush()
            self.active_index = -1
            _log.write("9\n"); _log.flush()
            self.current_pdf_doc = None
            _log.write("10\n"); _log.flush()
            self.document_modified = False
            _log.write("11\n"); _log.flush()
            self.undo_stack.clear()
            _log.write("12\n"); _log.flush()
            for lbl in self.page_labels:
                lbl.deleteLater()
            _log.write("13\n"); _log.flush()
            self.page_labels = []
            _log.write("14\n"); _log.flush()
            self.preview_placeholder.setText("Select a document to preview")
            _log.write("15\n"); _log.flush()
            self.preview_placeholder.setStyleSheet("color: #555570; font-size: 12pt;")
            _log.write("16\n"); _log.flush()
            self.preview_placeholder.show()
            _log.write("17\n"); _log.flush()
            self.pending_label.setText("Pending Review (0)")
            _log.write("18\n"); _log.flush()
            self.passed_label.setText("Auto-Confirmed (0)")
            _log.write("19\n"); _log.flush()
            self.count_label.setText("No documents")
            _log.write("20\n"); _log.flush()
            self.page_label.setText("No document loaded")
            _log.write("21\n"); _log.flush()
            self.finalize_btn.setEnabled(True)
            _log.write("22\n"); _log.flush()
            self.finalize_btn.setText("FINALIZE")
            _log.write("23\n"); _log.flush()
            QMessageBox.information(self, "Finalize Complete", "All documents have been processed and moved to the output folder.")
            _log.write("24\n"); _log.flush()
        except Exception as e:
            _log.write(f"EXCEPTION: {e}\n")
            _log.write(traceback.format_exc() + "\n")
            _log.flush()
        _log.write("=== _on_finalize_done END ===\n")
        _log.close()

    def _on_finalize_error(self, msg):
        _log = open(Path(__file__).parent.parent / "finalize_debug.log", "a", encoding="utf-8")
        _log.write(f"=== _on_finalize_error: {msg} ===\n")
        _log.close()
        self.finalize_btn.setEnabled(True)
        self.finalize_btn.setText("FINALIZE")
        QMessageBox.critical(self, "Finalize Error", f"Failed to finalize:\n{msg}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_pdf_doc:
            QTimer.singleShot(100, self.render_preview)

    def closeEvent(self, event):
        _log = open(Path(__file__).parent.parent / "finalize_debug.log", "a", encoding="utf-8")
        _log.write(f"=== ReviewTab closeEvent ===\n")
        _log.close()
        super().closeEvent(event)
