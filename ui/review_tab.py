import os
import json
import fitz
from pathlib import Path
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QScrollArea, QListWidget, QListWidgetItem, QTreeWidget,
    QTreeWidgetItem, QStackedWidget, QSplitter,
    QGroupBox, QDialog, QTextEdit, QMessageBox, QFileDialog,
    QMenu, QInputDialog, QFrame, QLineEdit, QCompleter
)
from PyQt6.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import (
    QFont, QPixmap, QImage, QAction, QShortcut, QKeySequence,
    QPainter, QPen, QBrush, QColor, QCursor
)

import sys
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR
from pipeline import (
    PipelineConfig, parse_folder_structure, load_flagged_index
)
from enhance import enhance_page
from company_extractor import normalize_company_name, learn_company_name, load_known_companies, save_known_companies

from .widgets import DocCardWidget, PassedDocCardWidget


def _norm_path(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""


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


class CropOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._crop_rect = None
        self._handle_size = 10
        self._dragging = None
        self._drag_start = None
        self._min_size = 40

    def set_crop_rect(self, rect):
        self._crop_rect = QRectF(rect)
        self.update()

    def get_crop_rect(self):
        return QRectF(self._crop_rect) if self._crop_rect else None

    def _handles(self):
        r = self._crop_rect
        if not r:
            return []
        hs = self._handle_size / 2
        cx, cy = r.center().x(), r.center().y()
        return [
            ("tl", QRectF(r.left() - hs, r.top() - hs, self._handle_size, self._handle_size)),
            ("tr", QRectF(r.right() - hs, r.top() - hs, self._handle_size, self._handle_size)),
            ("bl", QRectF(r.left() - hs, r.bottom() - hs, self._handle_size, self._handle_size)),
            ("br", QRectF(r.right() - hs, r.bottom() - hs, self._handle_size, self._handle_size)),
            ("t", QRectF(cx - hs, r.top() - hs, self._handle_size, self._handle_size)),
            ("b", QRectF(cx - hs, r.bottom() - hs, self._handle_size, self._handle_size)),
            ("l", QRectF(r.left() - hs, cy - hs, self._handle_size, self._handle_size)),
            ("r", QRectF(r.right() - hs, cy - hs, self._handle_size, self._handle_size)),
        ]

    def paintEvent(self, event):
        if not self._crop_rect:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._crop_rect
        w, h = self.width(), self.height()

        p.setBrush(QColor(0, 0, 0, 120))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(0, 0, w, r.top()))
        p.drawRect(QRectF(0, r.bottom(), w, h - r.bottom()))
        p.drawRect(QRectF(0, r.top(), r.left(), r.height()))
        p.drawRect(QRectF(r.right(), r.top(), w - r.right(), r.height()))

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 1.5, Qt.PenStyle.DashLine))
        p.drawRect(r)

        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(0, 0, 0), 1))
        for _, hr in self._handles():
            p.drawRect(hr)
        p.end()

    def _hit_handle(self, pos):
        for name, hr in self._handles():
            if hr.adjusted(-2, -2, 2, 2).contains(pos):
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._crop_rect:
            return
        pos = event.position() if hasattr(event, 'position') else event.pos()
        handle = self._hit_handle(pos)
        if handle:
            self._dragging = handle
            self._drag_start = pos
        elif self._crop_rect.contains(pos):
            self._dragging = "move"
            self._drag_start = pos

    def mouseMoveEvent(self, event):
        if not self._dragging or not self._crop_rect:
            return
        pos = event.position() if hasattr(event, 'position') else event.pos()
        dx = pos.x() - self._drag_start.x()
        dy = pos.y() - self._drag_start.y()
        self._drag_start = pos
        r = QRectF(self._crop_rect)

        if self._dragging == "move":
            r.translate(dx, dy)
        elif self._dragging == "tl":
            r.setTopLeft(r.topLeft() + QPointF(dx, dy))
        elif self._dragging == "tr":
            r.setTopRight(r.topRight() + QPointF(dx, dy))
        elif self._dragging == "bl":
            r.setBottomLeft(r.bottomLeft() + QPointF(dx, dy))
        elif self._dragging == "br":
            r.setBottomRight(r.bottomRight() + QPointF(dx, dy))
        elif self._dragging == "t":
            r.setTop(r.top() + dy)
        elif self._dragging == "b":
            r.setBottom(r.bottom() + dy)
        elif self._dragging == "l":
            r.setLeft(r.left() + dx)
        elif self._dragging == "r":
            r.setRight(r.right() + dx)

        if r.width() >= self._min_size and r.height() >= self._min_size:
            self._crop_rect = r
        self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging and self._crop_rect:
            bounds = QRectF(0, 0, self.width(), self.height())
            self._crop_rect = self._crop_rect.intersected(bounds)
            if self._crop_rect.width() < self._min_size or self._crop_rect.height() < self._min_size:
                self._crop_rect = bounds
        self._dragging = None
        self._drag_start = None
        self.update()



class ReviewedScanWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, output_root: str):
        super().__init__()
        self.output_root = output_root

    def run(self):
        from pathlib import Path
        root = Path(self.output_root)
        reviewed = []
        if not root.exists():
            self.finished.emit(reviewed)
            return
        for div_dir in root.iterdir():
            if not div_dir.is_dir() or div_dir.name.startswith("."):
                continue
            try:
                for company_dir in div_dir.iterdir():
                    if not company_dir.is_dir():
                        continue
                    try:
                        for pdf_file in company_dir.glob("*.pdf"):
                            reviewed.append({
                                "original_path": str(pdf_file),
                                "original_filename": pdf_file.name,
                                "division_code": div_dir.name,
                                "company_name": company_dir.name,
                                "detected_date": pdf_file.name[:8] if len(pdf_file.name) >= 8 else "",
                                "confidence": 100,
                                "method": "finalized",
                                "blank_pages": [],
                                "docsep_pages": [],
                            })
                    except PermissionError:
                        continue
            except PermissionError:
                continue
        try:
            for pdf_file in root.iterdir():
                if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                    reviewed.append({
                        "original_path": str(pdf_file),
                        "original_filename": pdf_file.name,
                        "division_code": "",
                        "company_name": "All Documents",
                        "detected_date": pdf_file.name[:8] if len(pdf_file.name) >= 8 else "",
                        "confidence": 100,
                        "method": "finalized",
                        "blank_pages": [],
                        "docsep_pages": [],
                    })
        except PermissionError:
            pass
        self.finished.emit(reviewed)


class FinalizeAllWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int, list)

    def __init__(self, docs: list, config, parent=None):
        super().__init__(parent)
        self.docs = docs
        self.config = config

    def run(self):
        from pipeline import finalize_single_document
        failures = []
        done = 0
        total = len(self.docs)
        for doc in self.docs:
            result = finalize_single_document(doc, self.config)
            if result.get("success"):
                done += 1
            else:
                failures.append((doc.get("original_path", ""), doc.get("original_filename", ""), result.get("error", "Unknown")))
            self.progress.emit(done, total)
        self.finished.emit(done, failures)


class ReviewTab(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.all_flagged_docs = []
        self.pending_docs = []
        self.auto_confirmed_docs = []
        self.reviewed_docs = []
        self.active_tab = "pending"
        self.active_index = -1
        self.current_pdf_doc = None
        self.doc_card_widgets = []
        self.reviewed_card_widgets = []
        self.reviewed_index_to_item = {}
        self._reviewed_scan_worker = None
        self._reviewed_scan_token = 0
        self._confirm_all_worker = None
        self.ocr_dialog = None
        self.reviewed_passed = set()
        self._toast = None
        self._toast_timer = None
        self.zoom_level = 100
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self.render_preview)
        self.page_labels = []
        self.document_modified = False
        self._crop_mode = False
        self._crop_page_num = -1
        self._crop_overlay = None
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

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.setContentsMargins(0, 4, 0, 4)
        self.tab_pending_btn = QPushButton("Pending (0)")
        self.tab_pending_btn.setCheckable(True)
        self.tab_pending_btn.setChecked(True)
        self.tab_pending_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.tab_pending_btn.setFixedHeight(26)
        self.tab_pending_btn.clicked.connect(lambda: self.switch_tab("pending"))
        filter_row.addWidget(self.tab_pending_btn)
        self.tab_auto_confirmed_btn = QPushButton("Auto-Confirmed (0)")
        self.tab_auto_confirmed_btn.setCheckable(True)
        self.tab_auto_confirmed_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.tab_auto_confirmed_btn.setFixedHeight(26)
        self.tab_auto_confirmed_btn.clicked.connect(lambda: self.switch_tab("auto_confirmed"))
        filter_row.addWidget(self.tab_auto_confirmed_btn)
        self.tab_reviewed_btn = QPushButton("Reviewed (0)")
        self.tab_reviewed_btn.setCheckable(True)
        self.tab_reviewed_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.tab_reviewed_btn.setFixedHeight(26)
        self.tab_reviewed_btn.clicked.connect(lambda: self.switch_tab("reviewed"))
        filter_row.addWidget(self.tab_reviewed_btn)
        left_layout.addLayout(filter_row)

        self.doc_list = QListWidget()
        self.doc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.doc_list.currentRowChanged.connect(self._on_doc_selected)

        self.reviewed_tree = QTreeWidget()
        self.reviewed_tree.setHeaderHidden(True)
        self.reviewed_tree.setRootIsDecorated(True)
        self.reviewed_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.reviewed_tree.itemClicked.connect(self._on_reviewed_item_clicked)
        self.reviewed_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1a1e2e;
                border: 1px solid #2d2e45;
                border-radius: 6px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QTreeWidget::item {
                padding: 2px 4px;
            }
            QTreeWidget::item:selected {
                background-color: #1a2a3a;
                color: #4fc3f7;
            }
            QTreeWidget::item:hover {
                background-color: #1e2535;
            }
        """)

        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.doc_list)
        self.list_stack.addWidget(self.reviewed_tree)
        left_layout.addWidget(self.list_stack, 1)

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
        self.save_crop_btn = QPushButton("Save")
        self.save_crop_btn.setFixedHeight(26)
        self.save_crop_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.save_crop_btn.clicked.connect(self.apply_crop)
        self.save_crop_btn.setVisible(False)
        bottom.addWidget(self.save_crop_btn)
        bottom.addWidget(self.add_roster_btn)
        bottom.addWidget(self.ocr_btn)
        bottom.addStretch()
        self.confirm_all_btn = QPushButton("CONFIRM ALL")
        self.confirm_all_btn.setObjectName("accent")
        self.confirm_all_btn.setFixedHeight(26)
        self.confirm_all_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.confirm_all_btn.clicked.connect(self.confirm_all_auto)
        bottom.addWidget(self.confirm_all_btn)
        main_layout.addLayout(bottom)

        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self._undo)
        self.enter_shortcut_return = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self.enter_shortcut_return.activated.connect(self._on_enter_key)
        self.enter_shortcut_enter = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        self.enter_shortcut_enter.activated.connect(self._on_enter_key)

    def refresh_completer(self):
        companies = load_known_companies()
        self._company_completer.model().setStringList(companies)

    def refresh_review(self):
        _prev_flagged = list(self.all_flagged_docs) if self.all_flagged_docs else []
        _prev_auto = list(self.auto_confirmed_docs) if self.auto_confirmed_docs else []

        _old_pending_bytes = {}
        for pd in _prev_auto:
            pb = pd.get("_pending_pdf_bytes")
            if pb:
                _old_pending_bytes[_norm_path(pd.get("original_path", ""))] = pb

        self.doc_list.clear()
        self.doc_card_widgets = []
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

        auto_file = Path(self.config["flagged_root"]) / "auto_confirmed_index.json"
        if auto_file.exists():
            try:
                with open(auto_file, "r", encoding="utf-8") as f:
                    self.auto_confirmed_docs = json.load(f)
                if not self.auto_confirmed_docs:
                    self.auto_confirmed_docs = _prev_auto
            except Exception:
                self.auto_confirmed_docs = _prev_auto
        else:
            self.auto_confirmed_docs = _prev_auto

        for pd in self.auto_confirmed_docs:
            key = _norm_path(pd.get("original_path", ""))
            if key in _old_pending_bytes and "_pending_pdf_bytes" not in pd:
                pd["_pending_pdf_bytes"] = _old_pending_bytes[key]

        if self.active_tab == "reviewed":
            self._start_reviewed_scan()
        else:
            self.refresh_doc_list()
            if self.active_tab == "pending" and self.pending_docs:
                self.doc_list.setCurrentRow(0)

    def switch_tab(self, tab_name: str):
        self.active_tab = tab_name
        self.active_index = -1
        self.current_pdf_doc = None
        self.document_modified = False
        self.undo_stack.clear()
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []
        self.preview_placeholder.setText("Select a document to preview")
        self.preview_placeholder.setStyleSheet("color: #555570; font-size: 12pt;")
        self.preview_placeholder.show()
        self.tab_pending_btn.setChecked(tab_name == "pending")
        self.tab_auto_confirmed_btn.setChecked(tab_name == "auto_confirmed")
        self.tab_reviewed_btn.setChecked(tab_name == "reviewed")
        self.confirm_all_btn.setVisible(tab_name == "auto_confirmed")
        self.confirm_all_btn.setEnabled(tab_name == "auto_confirmed" and bool(self.auto_confirmed_docs))
        if tab_name == "reviewed":
            self.list_stack.setCurrentWidget(self.reviewed_tree)
            self._start_reviewed_scan()
        else:
            self.list_stack.setCurrentWidget(self.doc_list)
            self.refresh_doc_list()

    def refresh_doc_list(self, select_row=None):
        if self.active_tab == "reviewed":
            return
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        self.doc_card_widgets = []
        if self.active_tab == "pending":
            docs = self.pending_docs
            for i, doc in enumerate(docs):
                card = DocCardWidget(doc, i)
                card.clicked.connect(self._on_card_clicked)
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 62))
                self.doc_list.addItem(item)
                self.doc_list.setItemWidget(item, card)
                self.doc_card_widgets.append(card)
        elif self.active_tab == "auto_confirmed":
            docs = self.auto_confirmed_docs
            for i, doc in enumerate(docs):
                card = PassedDocCardWidget(doc, i)
                card.clicked.connect(self._on_card_clicked)
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 62))
                self.doc_list.addItem(item)
                self.doc_list.setItemWidget(item, card)
                self.doc_card_widgets.append(card)
        self.tab_pending_btn.setText(f"Pending ({len(self.pending_docs)})")
        self.tab_auto_confirmed_btn.setText(f"Auto-Confirmed ({len(self.auto_confirmed_docs)})")
        self.tab_reviewed_btn.setText(f"Reviewed ({len(self.reviewed_docs)})")
        self.count_label.setText(f"{len(self.pending_docs)} pending | {len(self.auto_confirmed_docs)} auto | {len(self.reviewed_docs)} reviewed")
        self.doc_nav_label.setText(f"{len(docs)} docs")
        self.confirm_all_btn.setVisible(self.active_tab == "auto_confirmed")
        self.confirm_all_btn.setEnabled(self.active_tab == "auto_confirmed" and bool(docs))
        self.doc_list.blockSignals(False)
        if docs:
            row = select_row if select_row is not None else 0
            self.doc_list.setCurrentRow(row)

    def _load_reviewed_from_output(self):
        output_root = Path(self.config.get("output_root", ""))
        if not output_root.exists():
            self.reviewed_docs = []
            return
        reviewed = []
        for div_dir in sorted(output_root.iterdir()):
            if not div_dir.is_dir() or div_dir.name.startswith("."):
                continue
            for company_dir in sorted(div_dir.iterdir()):
                if not company_dir.is_dir():
                    continue
                for pdf_file in company_dir.glob("*.pdf"):
                    reviewed.append({
                        "original_path": str(pdf_file),
                        "original_filename": pdf_file.name,
                        "division_code": div_dir.name,
                        "company_name": company_dir.name,
                        "detected_date": pdf_file.name[:8] if len(pdf_file.name) >= 8 else "",
                        "confidence": 100,
                        "method": "finalized",
                        "blank_pages": [],
                        "docsep_pages": [],
                    })
        for pdf_file in sorted(output_root.iterdir()):
            if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                reviewed.append({
                    "original_path": str(pdf_file),
                    "original_filename": pdf_file.name,
                    "division_code": "",
                    "company_name": "All Documents",
                    "detected_date": pdf_file.name[:8] if len(pdf_file.name) >= 8 else "",
                    "confidence": 100,
                    "method": "finalized",
                    "blank_pages": [],
                    "docsep_pages": [],
                })
        self.reviewed_docs = reviewed

    def _start_reviewed_scan(self):
        self.reviewed_tree.blockSignals(True)
        self.reviewed_tree.clear()
        self.reviewed_tree.blockSignals(False)
        self.reviewed_index_to_item = {}
        loading_item = QTreeWidgetItem(["Scanning output folder..."])
        loading_item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.reviewed_tree.addTopLevelItem(loading_item)
        self._reviewed_scan_token += 1
        token = self._reviewed_scan_token
        self._reviewed_scan_worker = ReviewedScanWorker(self.config.get("output_root", ""))
        self._reviewed_scan_worker.finished.connect(lambda docs, t=token: self._on_reviewed_scan_done(docs, t))
        self._reviewed_scan_worker.start()

    def _on_reviewed_scan_done(self, reviewed: list, token: int):
        if token != self._reviewed_scan_token:
            return
        reviewed.sort(key=lambda d: d.get("original_filename", ""))
        self.reviewed_docs = reviewed
        if self.active_tab == "reviewed":
            self._populate_reviewed_tree(reviewed)

    def _populate_reviewed_tree(self, docs: list):
        self.reviewed_tree.blockSignals(True)
        self.reviewed_tree.clear()
        self.reviewed_index_to_item = {}

        grouped = {}
        for i, doc in enumerate(docs):
            div = doc.get("division_code", "")
            comp = doc.get("company_name", "")
            key = (div, comp)
            grouped.setdefault(key, []).append((i, doc))

        for key in sorted(grouped.keys()):
            div, comp = key
            label = f"{div} / {comp}" if comp else (div or "Unknown")
            folder_item = QTreeWidgetItem([label])
            folder_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            folder_item.setToolTip(0, label)
            self.reviewed_tree.addTopLevelItem(folder_item)
            for i, doc in grouped[key]:
                file_item = QTreeWidgetItem([doc.get("original_filename", "Unknown")])
                file_item.setData(0, Qt.ItemDataRole.UserRole, i)
                file_item.setToolTip(0, doc.get("original_path", ""))
                folder_item.addChild(file_item)
                self.reviewed_index_to_item[i] = file_item

        self.reviewed_tree.resizeColumnToContents(0)
        self.reviewed_tree.expandAll()
        self.reviewed_tree.blockSignals(False)

        self.tab_reviewed_btn.setText(f"Reviewed ({len(self.reviewed_docs)})")
        self.tab_pending_btn.setText(f"Pending ({len(self.pending_docs)})")
        self.tab_auto_confirmed_btn.setText(f"Auto-Confirmed ({len(self.auto_confirmed_docs)})")
        self.count_label.setText(f"{len(self.pending_docs)} pending | {len(self.auto_confirmed_docs)} auto | {len(self.reviewed_docs)} reviewed")
        self.doc_nav_label.setText(f"{len(self.reviewed_docs)} docs")

    def _on_reviewed_item_clicked(self, item, column):
        if item.childCount() > 0:
            item.setExpanded(not item.isExpanded())
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self.reviewed_docs):
            return
        self.active_index = idx
        self.load_document(self.reviewed_docs[idx])

    def _nav_reviewed(self, delta: int):
        docs = self.reviewed_docs
        if not docs:
            return
        new_idx = self.active_index + delta
        if new_idx < 0 or new_idx >= len(docs):
            return
        self.active_index = new_idx
        item = self.reviewed_index_to_item.get(new_idx)
        if item:
            self.reviewed_tree.setCurrentItem(item)
            self.reviewed_tree.scrollToItem(item)
        self.load_document(docs[new_idx])

    def _on_card_clicked(self, idx: int):
        self.doc_list.setCurrentRow(idx)

    def _on_doc_selected(self, row: int):
        if row < 0:
            return
        docs = self._get_current_docs()
        if row >= len(docs):
            return
        self.active_index = row
        for i, card in enumerate(self.doc_card_widgets):
            card.set_selected(i == row)
        if self.active_tab == "pending":
            self.load_document(self.pending_docs[row])
        elif self.active_tab == "auto_confirmed":
            self.load_document(self.auto_confirmed_docs[row])
        elif self.active_tab == "reviewed":
            self.load_document(self.reviewed_docs[row])

    def _save_auto_confirmed_updates(self):
        auto_file = Path(self.config["flagged_root"]) / "auto_confirmed_index.json"
        auto_file.parent.mkdir(parents=True, exist_ok=True)
        clean = [{k: v for k, v in d.items() if k != "_pending_pdf_bytes"} for d in self.auto_confirmed_docs]
        with open(auto_file, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)

    def clear_all(self):
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        self.doc_list.blockSignals(False)
        self.doc_card_widgets = []
        self.reviewed_tree.clear()
        self.reviewed_index_to_item = {}
        self.all_flagged_docs = []
        self.pending_docs = []
        self.auto_confirmed_docs = []
        self.reviewed_docs = []
        self.active_index = -1
        self.current_pdf_doc = None
        self.undo_stack.clear()
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []
        self.preview_placeholder.setText("Select a document to preview")
        self.preview_placeholder.setStyleSheet("color: #555570; font-size: 12pt;")
        self.tab_pending_btn.setText("Pending (0)")
        self.tab_auto_confirmed_btn.setText("Auto-Confirmed (0)")
        self.tab_reviewed_btn.setText("Reviewed (0)")
        self.count_label.setText("No documents")
        self.doc_nav_label.setText("No docs")
        self.page_label.setText("No document loaded")
        self.confirm_all_btn.setVisible(False)
        self.confirm_all_btn.setEnabled(False)
        self.confirm_all_btn.setText("CONFIRM ALL")

    def add_doc(self, doc_type: str, data: dict):
        if doc_type == "pending":
            self.all_flagged_docs.append(data)
            self.pending_docs.append(data)
        elif doc_type == "passed":
            self.auto_confirmed_docs.append(data)
        self.tab_pending_btn.setText(f"Pending ({len(self.pending_docs)})")
        self.tab_auto_confirmed_btn.setText(f"Auto-Confirmed ({len(self.auto_confirmed_docs)})")
        self.count_label.setText(f"{len(self.pending_docs)} pending | {len(self.auto_confirmed_docs)} auto | {len(self.reviewed_docs)} reviewed")

    def load_document(self, doc: dict):
        self.exit_crop_mode()
        if self.current_pdf_doc:
            try:
                self.current_pdf_doc.close()
            except Exception:
                pass
            self.current_pdf_doc = None

        self.current_page = 0
        self.document_modified = False
        self.undo_stack.clear()

        total = len(self._get_current_docs())
        docs = self._get_current_docs()
        self.doc_nav_label.setText(f"{self.active_tab.title()} {self.active_index + 1} of {total}")

        loaded = False

        pending_bytes = doc.get("_pending_pdf_bytes")
        if pending_bytes:
            try:
                self.current_pdf_doc = fitz.open("pdf", pending_bytes)
                self.total_pages = len(self.current_pdf_doc)
                loaded = True
                self.document_modified = True
            except Exception:
                pass

        if not loaded:
            mod_path = doc.get("modified_path", "")
            pdf_path = mod_path if (mod_path and os.path.exists(mod_path)) else doc.get("original_path", "")
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

        if self.active_tab in ("auto_confirmed", "reviewed"):
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
        doc = self._get_current_doc()
        return doc.get("blank_pages", []) if doc else []

    def _get_docsep_pages(self):
        doc = self._get_current_doc()
        return doc.get("docsep_pages", []) if doc else []

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
                w = item.widget()
                if w and w is not self.preview_placeholder:
                    w.hide()
                    w.deleteLater()

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
        crop_act = QAction("Crop Page", self)
        crop_act.triggered.connect(lambda: self.enter_crop_mode(page_num))
        menu.addAction(crop_act)
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

    def enter_crop_mode(self, page_num):
        if not self.current_pdf_doc or self._crop_mode:
            return
        if page_num < 0 or page_num >= len(self.page_labels):
            return
        self._push_undo()
        self._crop_mode = True
        self._crop_page_num = page_num
        lbl = self.page_labels[page_num]
        self._crop_label = lbl
        overlay = CropOverlay(lbl)
        overlay.setGeometry(0, 0, lbl.width(), lbl.height())
        overlay.set_crop_rect(QRectF(0, 0, lbl.width(), lbl.height()))
        overlay.show()
        self._crop_overlay = overlay
        self.save_crop_btn.setVisible(True)
        self.confirm_btn.setEnabled(False)

    def apply_crop(self):
        if not self._crop_mode or not self._crop_overlay or not self.current_pdf_doc:
            return
        crop_rect = self._crop_overlay.get_crop_rect()
        if not crop_rect:
            self.exit_crop_mode()
            return
        lbl = self._crop_label
        page_num = self._crop_page_num
        page = self.current_pdf_doc[page_num]
        page_w_pts = page.rect.width
        page_h_pts = page.rect.height
        if lbl.width() <= 0 or lbl.height() <= 0:
            self.exit_crop_mode()
            return
        sx = page_w_pts / lbl.width()
        sy = page_h_pts / lbl.height()
        pdf_rect = fitz.Rect(
            crop_rect.left() * sx,
            crop_rect.top() * sy,
            crop_rect.right() * sx,
            crop_rect.bottom() * sy,
        )
        full = page.rect
        if (abs(pdf_rect.x0 - full.x0) < 1 and abs(pdf_rect.y0 - full.y0) < 1
                and abs(pdf_rect.x1 - full.x1) < 1 and abs(pdf_rect.y1 - full.y1) < 1):
            self.exit_crop_mode()
            return
        page.set_cropbox(pdf_rect)
        self.document_modified = True
        self.exit_crop_mode()
        self.render_preview()


    def exit_crop_mode(self):
        if self._crop_overlay:
            self._crop_overlay.setParent(None)
            self._crop_overlay.deleteLater()
            self._crop_overlay = None
        self._crop_mode = False
        self._crop_page_num = -1
        self._crop_label = None
        self.save_crop_btn.setVisible(False)
        self.confirm_btn.setEnabled(True)

    def _get_current_doc(self):
        docs = self._get_current_docs()
        if self.active_index >= 0 and self.active_index < len(docs):
            return docs[self.active_index]
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
        self.exit_crop_mode()
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
        if self.active_tab == "reviewed":
            self._nav_reviewed(-1)
        elif self.active_index > 0:
            self.doc_list.setCurrentRow(self.active_index - 1)

    def next_document(self):
        if self.active_tab == "reviewed":
            self._nav_reviewed(1)
        else:
            docs = self._get_current_docs()
            if self.active_index < len(docs) - 1:
                self.doc_list.setCurrentRow(self.active_index + 1)

    def _get_current_docs(self):
        if self.active_tab == "pending":
            return self.pending_docs
        elif self.active_tab == "auto_confirmed":
            return self.auto_confirmed_docs
        elif self.active_tab == "reviewed":
            return self.reviewed_docs
        return []

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
        if self.active_index < 0:
            return
        dt = self._get_date_from_widgets()
        company_text = self._get_company_from_widgets()
        from pipeline import finalize_single_document
        config = self._pipeline_config()

        if self.active_tab == "pending":
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
            if self.document_modified and self.current_pdf_doc:
                doc["_pending_pdf_bytes"] = self.current_pdf_doc.tobytes(garbage=4, deflate=True)
            result = finalize_single_document(doc, config)
            if result.get("success"):
                self.pending_docs.pop(self.active_index)
                self._save_flagged_updates()
                self._show_toast(f"Saved: {result.get('final_filename', '')}\n→ {os.path.dirname(result.get('output_path', ''))}")
                self.current_pdf_doc = None
                self.active_index = -1
                for lbl in self.page_labels:
                    lbl.deleteLater()
                self.page_labels = []
                self.refresh_doc_list()
            else:
                QMessageBox.warning(self, "Finalize Failed", f"Could not save: {result.get('error', 'Unknown')}")

        elif self.active_tab == "auto_confirmed":
            doc = self.auto_confirmed_docs[self.active_index]
            original_company = doc.get("company_name", "")
            if company_text and company_text != original_company:
                confirmed_company = normalize_company_name(company_text)
            else:
                confirmed_company = original_company
            doc["detected_date"] = dt
            doc["company_name"] = confirmed_company
            doc["reviewed"] = True
            if self.document_modified and self.current_pdf_doc:
                doc["_pending_pdf_bytes"] = self.current_pdf_doc.tobytes(garbage=4, deflate=True)
            result = finalize_single_document(doc, config)
            if result.get("success"):
                self.auto_confirmed_docs.pop(self.active_index)
                self._save_auto_confirmed_updates()
                self._show_toast(f"Saved: {result.get('final_filename', '')}\n→ {os.path.dirname(result.get('output_path', ''))}")
                self.current_pdf_doc = None
                self.active_index = -1
                for lbl in self.page_labels:
                    lbl.deleteLater()
                self.page_labels = []
                self.refresh_doc_list()
            else:
                QMessageBox.warning(self, "Finalize Failed", f"Could not save: {result.get('error', 'Unknown')}")

    def toggle_ocr_panel(self):
        if self.active_index < 0:
            return
        docs = self._get_current_docs()
        doc = docs[self.active_index]
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
        clean = [{k: v for k, v in d.items() if k != "_pending_pdf_bytes"} for d in self.all_flagged_docs]
        with open(flagged_file, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)

    def _pipeline_config(self):
        from pipeline import PipelineConfig
        config = PipelineConfig(
            input_root=self.config.get("input_root", ""),
            output_root=self.config.get("output_root", ""),
            flagged_root=self.config.get("flagged_root", ""),
            confidence_threshold=self.config.get("confidence_threshold", 70),
            page_index=self.config.get("page_index", 0),
            earliest_year=self.config.get("earliest_year", 1990),
            ocr_engine=self.config.get("ocr_engine", "tesseract"),
        )
        config.enable_qc = self.config.get("enable_qc", None)
        config.enable_docsep_removal = self.config.get("enable_docsep_removal", True)
        config.enable_blank_removal = self.config.get("enable_blank_removal", True)
        config.qc_blank_threshold = self.config.get("qc_blank_threshold", 1.5)
        config.qc_rotation_threshold = self.config.get("qc_rotation_threshold", 65)
        config.qc_min_text_threshold = self.config.get("qc_min_text_threshold", 5)
        config.qc_oversized_margin = self.config.get("qc_oversized_margin", 0.3)
        config.qc_blur_threshold = self.config.get("qc_blur_threshold", 100)
        config.min_file_size_kb = self.config.get("min_file_size_kb", 10)
        config.output_layout = self.config.get("output_layout", "company")
        return config

    def _on_enter_key(self):
        focus = self.focusWidget()
        if focus in (self.company_input, self.month_combo, self.year_spin) or isinstance(focus, QPushButton):
            return
        if self.active_tab in ("pending", "auto_confirmed"):
            self._finalize_current_and_advance()

    def _finalize_current_and_advance(self):
        if self.active_tab not in ("pending", "auto_confirmed"):
            return
        if self.active_index < 0:
            QMessageBox.information(self, "Info", "Select a document first")
            return
        from pipeline import finalize_single_document
        docs = self.pending_docs if self.active_tab == "pending" else self.auto_confirmed_docs
        if self.active_index >= len(docs):
            return
        config = self._pipeline_config()
        doc = docs[self.active_index]

        if self.document_modified and self.current_pdf_doc:
            doc["_pending_pdf_bytes"] = self.current_pdf_doc.tobytes(garbage=4, deflate=True)

        result = finalize_single_document(doc, config)
        if not result.get("success"):
            QMessageBox.warning(self, "Finalize Failed", f"Could not save: {result.get('error', 'Unknown')}")
            return

        if self.active_tab == "pending":
            doc["reviewed"] = True
            self.pending_docs.pop(self.active_index)
            self._save_flagged_updates()
        else:
            self.auto_confirmed_docs.pop(self.active_index)
            self._save_auto_confirmed_updates()

        self._show_toast(f"Saved: {result.get('final_filename', '')}\n→ {os.path.dirname(result.get('output_path', ''))}")

        self.current_pdf_doc = None
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []
        next_row = min(self.active_index, len(self.pending_docs if self.active_tab == "pending" else self.auto_confirmed_docs) - 1)
        self.active_index = next_row
        self.refresh_doc_list(select_row=next_row if next_row >= 0 else None)

    def confirm_all_auto(self):
        if self.active_tab != "auto_confirmed" or not self.auto_confirmed_docs:
            return
        import copy
        if self.document_modified and self.current_pdf_doc and 0 <= self.active_index < len(self.auto_confirmed_docs):
            self.auto_confirmed_docs[self.active_index]["_pending_pdf_bytes"] = self.current_pdf_doc.tobytes(garbage=4, deflate=True)
        self._set_confirm_all_running(True)
        snapshot = copy.deepcopy(self.auto_confirmed_docs)
        self._confirm_all_worker = FinalizeAllWorker(snapshot, self._pipeline_config())
        self._confirm_all_worker.progress.connect(self._on_confirm_all_progress)
        self._confirm_all_worker.finished.connect(self._on_confirm_all_finished)
        self._confirm_all_worker.start()

    def _set_confirm_all_running(self, running: bool):
        self.confirm_all_btn.setEnabled(not running)
        self.confirm_all_btn.setText("Confirming..." if running else "CONFIRM ALL")

    def _on_confirm_all_progress(self, done: int, total: int):
        if total > 0:
            self.confirm_all_btn.setText(f"Confirming... {done}/{total}")

    def _on_confirm_all_finished(self, done: int, failures: list):
        self._set_confirm_all_running(False)
        self.current_pdf_doc = None
        for lbl in self.page_labels:
            lbl.deleteLater()
        self.page_labels = []
        self.active_index = -1
        if failures:
            failed_paths = {f[0] for f in failures}
            self.auto_confirmed_docs = [d for d in self.auto_confirmed_docs if d.get("original_path", "") in failed_paths]
            self._save_auto_confirmed_updates()
            lines = [f"{name}: {err}" for _, name, err in failures[:20]]
            extra = "" if len(failures) <= 20 else f"\n... and {len(failures) - 20} more"
            QMessageBox.warning(self, "Confirm All", f"Finalized {done}.\n{len(failures)} failed:\n" + "\n".join(lines) + extra)
        else:
            self.auto_confirmed_docs = []
            self._save_auto_confirmed_updates()
            QMessageBox.information(self, "Confirm All", f"All {done} documents finalized and moved to the output folder.")
        self.refresh_doc_list()

    def _show_toast(self, message: str):
        if self._toast is None:
            self._toast = QLabel(self)
            self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._toast.setWordWrap(True)
            self._toast.setStyleSheet(
                "background-color: #20223a; color: #7dff9b; border: 1px solid #2fbf6b;"
                " border-radius: 6px; padding: 8px 14px; font-size: 10pt; font-weight: bold;"
            )
        self._toast.setText(message)
        self._toast.adjustSize()
        max_w = max(240, min(420, self.width() - 32))
        if self._toast.width() > max_w:
            self._toast.setFixedWidth(max_w)
            self._toast.adjustSize()
        self._toast.adjustSize()
        self._toast.move(self.width() - self._toast.width() - 16, self.height() - self._toast.height() - 16)
        self._toast.raise_()
        self._toast.show()
        if self._toast_timer is None:
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._toast.hide)
        self._toast_timer.start(2000)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._toast is not None and self._toast.isVisible():
            self._toast.move(self.width() - self._toast.width() - 16, self.height() - self._toast.height() - 16)
        if self.current_pdf_doc:
            QTimer.singleShot(100, self.render_preview)

    def closeEvent(self, event):
        _log = open(Path(__file__).parent.parent / "finalize_debug.log", "a", encoding="utf-8")
        _log.write(f"=== ReviewTab closeEvent ===\n")
        _log.close()
        super().closeEvent(event)
