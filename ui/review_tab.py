import os
import json
import cv2
import numpy as np
import fitz
from pathlib import Path
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QScrollArea, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QDialog, QTextEdit, QMessageBox, QFileDialog, QCheckBox
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QFont, QColor, QPixmap, QImage, QPainter, QPen

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from blank_page_detector import detect_blank_page
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
        self.debug_mode = False
        self.debug_overrides = {}
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

        self.debug_checkbox = QCheckBox("Show Detection Debug")
        self.debug_checkbox.setStyleSheet("color: #8888aa; font-size: 8pt;")
        self.debug_checkbox.toggled.connect(self._toggle_debug_mode)
        info_row.addWidget(self.debug_checkbox)

        self.override_blank_btn = QPushButton("Mark Blank")
        self.override_blank_btn.setFixedWidth(80)
        self.override_blank_btn.setStyleSheet("font-size: 8pt; padding: 3px 8px; background-color: #5a2020; color: #ff8888;")
        self.override_blank_btn.clicked.connect(lambda: self._override_page(True))
        self.override_blank_btn.setEnabled(False)
        info_row.addWidget(self.override_blank_btn)

        self.override_content_btn = QPushButton("Mark Content")
        self.override_content_btn.setFixedWidth(90)
        self.override_content_btn.setStyleSheet("font-size: 8pt; padding: 3px 8px; background-color: #1a4a1a; color: #88ff88;")
        self.override_content_btn.clicked.connect(lambda: self._override_page(False))
        self.override_content_btn.setEnabled(False)
        info_row.addWidget(self.override_content_btn)

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

        total = len(self.pending_docs) + len(self.passed_docs)
        if self.active_list == "pending":
            self.doc_nav_label.setText(f"Pending {self.active_index + 1} of {len(self.pending_docs)}")
        else:
            self.doc_nav_label.setText(f"Passed {self.active_index + 1} of {len(self.passed_docs)}")

        pdf_path = doc.get("original_path", "")
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

            blank_page_indices = []
            filename = ""
            if self.active_list == "pending" and self.active_index >= 0:
                filename = self.pending_docs[self.active_index].get("original_filename", "")
            elif self.active_list == "passed" and self.active_index >= 0:
                filename = self.passed_docs[self.active_index].get("original_filename", "")

            for page_num in range(len(self.current_pdf_doc)):
                page = self.current_pdf_doc[page_num]
                pix = page.get_pixmap(dpi=dpi)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                pixmap = QPixmap.fromImage(img)

                if pixmap.width() > preview_width:
                    pixmap = pixmap.scaledToWidth(preview_width, Qt.TransformationMode.SmoothTransformation)

                img_array = np.array(QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888))
                if len(img_array.shape) == 3:
                    img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

                override_key = f"{filename}_{page_num}"
                if override_key in self.debug_overrides:
                    is_blank = self.debug_overrides[override_key]
                    blank_result = {
                        "is_blank": is_blank,
                        "confidence": 100,
                        "reason": "Manual override",
                        "metrics": {"white_ratio": 0, "content_area": 0, "edge_ratio": 0, "std_dev": 0, "num_components": 0}
                    }
                else:
                    blank_result = detect_blank_page(img_array, debug=self.debug_mode)

                if blank_result["is_blank"] is True:
                    blank_page_indices.append(page_num)
                    overlay = QPixmap(pixmap.size())
                    overlay.fill(QColor(0, 0, 0, 128))
                    painter = QPainter(overlay)
                    painter.setPen(QPen(QColor(255, 100, 100)))
                    painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
                    painter.drawText(overlay.rect(), Qt.AlignmentFlag.AlignCenter, "BLANK PAGE")
                    painter.end()
                    result_pixmap = QPixmap(pixmap.size())
                    painter = QPainter(result_pixmap)
                    painter.drawPixmap(0, 0, pixmap)
                    painter.drawPixmap(0, 0, overlay)
                    painter.end()
                    pixmap = result_pixmap

                if self.debug_mode:
                    debug_overlay = QPixmap(pixmap.size())
                    debug_overlay.fill(QColor(0, 0, 0, 0))
                    if blank_result["is_blank"] is True:
                        border_color = QColor(255, 80, 80)
                    elif blank_result["is_blank"] is False:
                        border_color = QColor(80, 255, 80)
                    else:
                        border_color = QColor(255, 255, 80)
                    painter = QPainter(debug_overlay)
                    painter.setPen(QPen(border_color, 3))
                    painter.drawRect(2, 2, debug_overlay.width() - 4, debug_overlay.height() - 4)
                    painter.fillRect(10, 10, 220, 110, QColor(0, 0, 0, 180))
                    painter.setFont(QFont("Consolas", 9))
                    painter.setPen(QColor(255, 255, 255))
                    metrics = blank_result.get("metrics", {})
                    y = 30
                    painter.drawText(20, y, f"WHITE: {metrics.get('white_ratio', 0)*100:.1f}%"); y += 18
                    painter.drawText(20, y, f"CONTENT: {metrics.get('content_area', 0)*100:.2f}%"); y += 18
                    painter.drawText(20, y, f"EDGES: {metrics.get('edge_ratio', 0)*100:.3f}%"); y += 18
                    painter.drawText(20, y, f"STD_DEV: {metrics.get('std_dev', 0):.1f}"); y += 18
                    cls = "BLANK" if blank_result["is_blank"] is True else ("CONTENT" if blank_result["is_blank"] is False else "REVIEW")
                    painter.drawText(20, y, f"CLASS: {cls} ({blank_result['confidence']}%)")
                    painter.end()
                    result_pixmap = QPixmap(pixmap.size())
                    painter = QPainter(result_pixmap)
                    painter.drawPixmap(0, 0, pixmap)
                    painter.drawPixmap(0, 0, debug_overlay)
                    painter.end()
                    pixmap = result_pixmap

                lbl = QLabel()
                lbl.setPixmap(pixmap)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("background-color: #1e1f35; border: 1px solid #3a3b55; border-radius: 4px; padding: 4px;")
                self.preview_container_layout.addWidget(lbl)
                self.page_labels.append(lbl)

            total_pages = len(self.current_pdf_doc)
            if blank_page_indices:
                blank_str = ", ".join(str(i + 1) for i in blank_page_indices)
                self.page_label.setText(f"{total_pages} page(s) | {len(blank_page_indices)} blank: {blank_str} | Zoom: {self.zoom_level}%  (Ctrl+Scroll to zoom)")
            else:
                self.page_label.setText(f"{total_pages} page(s) | Zoom: {self.zoom_level}%  (Ctrl+Scroll to zoom)")
        except Exception as e:
            self.preview_placeholder.setText(f"Error rendering: {e}")
            self.preview_placeholder.setStyleSheet("color: #ff5555; font-size: 12pt;")

    def _toggle_debug_mode(self, checked):
        self.debug_mode = checked
        self.override_blank_btn.setEnabled(checked)
        self.override_content_btn.setEnabled(checked)
        if self.current_pdf_doc:
            self.render_preview()

    def _override_page(self, is_blank):
        if self.active_index < 0 or not self.current_pdf_doc:
            return
        if self.active_list == "pending":
            filename = self.pending_docs[self.active_index].get("original_filename", "")
        else:
            filename = self.passed_docs[self.active_index].get("original_filename", "")
        key = f"{filename}_{self.current_page}"
        self.debug_overrides[key] = is_blank
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
            self._save_passed_updates()

            self.refresh_review()

        elif self.active_list == "passed":
            doc = self.passed_docs[self.active_index]
            doc["detected_date"] = dt
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
