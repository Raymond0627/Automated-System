import os
import json
import fitz
from pathlib import Path
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QScrollArea, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QDialog, QTextEdit, QMessageBox, QFileDialog,
    QMenu
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QFont, QPixmap, QImage, QAction, QShortcut, QKeySequence

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline import (
    PipelineConfig, parse_folder_structure, load_flagged_index,
    update_document_from_review, finalize_all_divisions, save_confirmed_documents
)

from .widgets import DocCardWidget, PassedDocCardWidget


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
        self.zoom_level = 100
        self.page_labels = []
        self.document_modified = False
        self.undo_stack = []
        self.undo_max = 50
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
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_scroll.setStyleSheet("QScrollArea { background-color: #12131f; border: 1px solid #2d2e45; border-radius: 6px; }")
        self.preview_scroll.installEventFilter(self)

        self.preview_container = QWidget()
        self.preview_container.setStyleSheet("background-color: #12131f;")
        self.preview_container.installEventFilter(self)
        self.preview_container_layout = QVBoxLayout(self.preview_container)
        self.preview_container_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_container_layout.setContentsMargins(10, 10, 10, 10)
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
        bottom.addWidget(self.confirm_btn)
        bottom.addWidget(self.ocr_btn)
        bottom.addStretch()
        self.finalize_btn = QPushButton("Finalize & Rename All")
        self.finalize_btn.setObjectName("success")
        self.finalize_btn.setFixedHeight(26)
        self.finalize_btn.setFont(QFont("Segoe UI", 8))
        self.finalize_btn.clicked.connect(self.finalize_all)
        bottom.addWidget(self.finalize_btn)
        main_layout.addLayout(bottom)

        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self._undo)

    def refresh_review(self):
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
            except Exception:
                all_flagged = []
        else:
            all_flagged = []

        self.all_flagged_docs = all_flagged
        self.pending_docs = [d for d in all_flagged if not d.get("reviewed")]

        passed_file = Path(self.config["flagged_root"]) / "passed_index.json"
        if passed_file.exists():
            try:
                with open(passed_file, "r", encoding="utf-8") as f:
                    self.passed_docs = json.load(f)
            except Exception:
                self.passed_docs = []
        else:
            self.passed_docs = []

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
        for card in self.passed_cards:
            card.set_selected(False)
        self.load_document(self.pending_docs[row])

    def _on_passed_selected(self, row: int):
        if row < 0:
            return
        self.active_list = "passed"
        self.active_index = row
        for i, card in enumerate(self.passed_cards):
            card.set_selected(i == row)
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

    def render_preview(self):
        if not self.current_pdf_doc:
            return
        try:
            for lbl in self.page_labels:
                lbl.deleteLater()
            self.page_labels = []

            preview_width = max(400, self.preview_scroll.viewport().width() - 30)
            dpi = int(150 * (self.zoom_level / 100))

            for page_num in range(len(self.current_pdf_doc)):
                page = self.current_pdf_doc[page_num]
                pix = page.get_pixmap(dpi=dpi)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                pixmap = QPixmap.fromImage(img)

                if pixmap.width() > preview_width:
                    pixmap = pixmap.scaledToWidth(preview_width, Qt.TransformationMode.SmoothTransformation)

                lbl = QLabel()
                lbl.setPixmap(pixmap)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("background-color: #1e1f35; border: 1px solid #3a3b55; border-radius: 4px; padding: 4px;")
                lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                lbl.customContextMenuRequested.connect(lambda pos, pn=page_num, lb=lbl: self._show_page_menu(pos, pn, lb))
                self.preview_container_layout.addWidget(lbl)
                self.page_labels.append(lbl)

            total_pages = len(self.current_pdf_doc)
            self.page_label.setText(f"{total_pages} page(s) | Zoom: {self.zoom_level}%  (Ctrl+Scroll to zoom)")
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
        ins_before = QAction(f"Insert Before Page {page_num + 1}", self)
        ins_before.triggered.connect(lambda: self._insert_image_page(page_num, "before"))
        menu.addAction(ins_before)
        ins_after = QAction(f"Insert After Page {page_num + 1}", self)
        ins_after.triggered.connect(lambda: self._insert_image_page(page_num, "after"))
        menu.addAction(ins_after)
        menu.exec(lbl.mapToGlobal(pos))

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
        self._push_undo()
        page = self.current_pdf_doc[page_num]
        rect = page.rect
        if direction == "h":
            page.add_transformation(fitz.Matrix(-1, 0, 0, 1, rect.width, 0))
        else:
            page.add_transformation(fitz.Matrix(1, 0, 0, -1, 0, rect.height))
        page.clean_contents()
        self.document_modified = True
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
        self.undo_stack.append(self.current_pdf_doc.tobytes())
        if len(self.undo_stack) > self.undo_max:
            self.undo_stack.pop(0)

    def _undo(self):
        if not self.undo_stack or not self.current_pdf_doc:
            return
        data = self.undo_stack.pop()
        self.current_pdf_doc.close()
        self.current_pdf_doc = fitz.open("pdf", data)
        self.document_modified = True
        self.render_preview()

    def zoom_in(self):
        if self.zoom_level < 300:
            self.zoom_level = min(300, self.zoom_level + 25)
            self.render_preview()

    def zoom_out(self):
        if self.zoom_level > 25:
            self.zoom_level = max(25, self.zoom_level - 25)
            self.render_preview()

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

    def confirm_date(self):
        if self.active_index < 0 or not self.active_list:
            return
        dt = self._get_date_from_widgets()

        if self.active_list == "pending":
            doc = self.pending_docs[self.active_index]
            doc["confirmed_date"] = dt
            doc["reviewed"] = True
            doc["review_timestamp"] = str(datetime.now())
            self._save_flagged_updates()

            card = self.pending_cards[self.active_index]
            card.set_selected(False)

            self.passed_docs.append({
                "original_path": doc.get("original_path", ""),
                "division_code": doc.get("division_code", ""),
                "company_name": doc.get("company_name", ""),
                "original_filename": doc.get("original_filename", ""),
                "detected_date": dt,
                "confidence": doc.get("confidence", 0),
                "method": "manual",
                "blank_pages": doc.get("blank_pages", []),
                "final_filename": "",
            })

            if self.document_modified and self.current_pdf_doc:
                mod_path = Path(self.config["flagged_root"]) / doc.get("division_code", "") / doc.get("company_name", "") / doc.get("original_filename", "")
                mod_path.parent.mkdir(parents=True, exist_ok=True)
                self.current_pdf_doc.save(str(mod_path), incremental=False, garbage=4, deflate=True)
                self.passed_docs[-1]["modified_path"] = str(mod_path)

            self._save_passed_updates()

            self.refresh_review()

        elif self.active_list == "passed":
            doc = self.passed_docs[self.active_index]
            doc["detected_date"] = dt
            if self.document_modified and self.current_pdf_doc:
                mod_path = Path(self.config["flagged_root"]) / doc.get("division_code", "") / doc.get("company_name", "") / doc.get("original_filename", "")
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
        reply = QMessageBox.question(self, "Confirm", "Finalize and rename all confirmed documents?")
        if reply != QMessageBox.StandardButton.Yes:
            return

        pipeline_config = PipelineConfig(
            input_root=self.config["input_root"],
            output_root=self.config["output_root"],
            flagged_root=self.config["flagged_root"],
            confidence_threshold=self.config["confidence_threshold"],
            page_index=self.config["page_index"],
            earliest_year=self.config["earliest_year"],
            ocr_engine=self.config.get("ocr_engine", "tesseract"),
        )

        batches = parse_folder_structure(pipeline_config.input_root)

        for batch in batches:
            for doc in batch.documents:
                for pd in self.passed_docs:
                    if pd.get("original_path") == doc.original_path:
                        try:
                            doc.confirmed_date = date.fromisoformat(pd["detected_date"])
                            doc.confirmed_method = pd.get("method", "auto")
                            doc.status = "confirmed"
                            if pd.get("modified_path"):
                                doc.original_path = pd["modified_path"]
                                doc.blank_pages = []
                            else:
                                doc.blank_pages = pd.get("blank_pages", [])
                        except (ValueError, KeyError):
                            pass
                        break

        flagged_data = load_flagged_index(pipeline_config)
        for batch in batches:
            for doc in batch.documents:
                for fd in flagged_data:
                    if fd.get("original_path") == doc.original_path:
                        update_document_from_review(doc, fd)
                        break

        finalize_all_divisions(batches, pipeline_config)

        save_confirmed_documents(batches, pipeline_config)

        if self.document_modified and self.current_pdf_doc and self.active_index >= 0:
            if self.active_list == "pending":
                cur_orig = self.pending_docs[self.active_index].get("original_path", "")
            else:
                cur_orig = self.passed_docs[self.active_index].get("original_path", "")
            if cur_orig:
                for batch in batches:
                    for d in batch.documents:
                        if d.original_path == cur_orig:
                            out = Path(pipeline_config.output_root) / batch.division_code / d.company_name / d.original_filename
                            self.current_pdf_doc.save(str(out), incremental=False, garbage=4, deflate=True)
                            break

        for fname in ("passed_index.json", "flagged_index.json"):
            p = Path(self.config["flagged_root"]) / fname
            if p.exists():
                with open(p, "w", encoding="utf-8") as f:
                    json.dump([], f)

        self.refresh_review()

        log_path = Path(self.config["output_root"]) / "rename_log.csv"
        QMessageBox.information(self, "Done", f"Files renamed. Check {log_path}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_pdf_doc:
            QTimer.singleShot(100, self.render_preview)
