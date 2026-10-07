"""Organize Pages UI building blocks.

Task 2 delivers :class:`PagePane`, the lazy-thumbnail page grid used by both
sides of the organizer. Task 3 builds ``OrganizeDialog`` from two panes.

Drag payload (``text/plain`` mime): ``"pane:<pane_id>:<positions>"`` where
``positions`` are comma-separated sorted indices, e.g. ``"pane:a:0,2"``.
Both ends must go through :func:`build_drag_payload` / :func:`parse_drag_payload`.
"""

import fitz
from PyQt6.QtCore import QMimeData, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from organize_model import PageRef, apply_move
from .styles import THEME_DARK, get_palette_dict

DRAG_PREFIX = "pane"
CELL_INSET = 3
THUMB_WIDTH = 140
THUMB_GAP = 8

_APP = None


def _ensure_qapp():
    """Return the process-wide QApplication, creating one if there is none.

    A ``QApplication([])`` built as a throwaway temporary (the test-suite
    pattern ``QApplication.instance() or QApplication([])``) is garbage
    collected as soon as the statement ends, and constructing any QWidget
    afterwards aborts the process. Keep a module-level reference to the
    instance we create so panes can always be built.
    """
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def build_drag_payload(pane_id: str, positions) -> str:
    unique = sorted({int(p) for p in positions})
    return "%s:%s:%s" % (DRAG_PREFIX, pane_id, ",".join(str(p) for p in unique))


def parse_drag_payload(text: str):
    if not text or not text.startswith(DRAG_PREFIX + ":"):
        return None
    parts = text.split(":", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    raw = parts[2].strip()
    positions = []
    if raw:
        try:
            positions = sorted({int(chunk) for chunk in raw.split(",") if chunk.strip()})
        except ValueError:
            return None
    return parts[1], positions


class ClickableThumb(QLabel):
    """Single page thumbnail: press selects, drag moves, pane owns the logic."""

    def __init__(self, pane, position, ref, parent=None):
        _ensure_qapp()
        super().__init__(parent)
        self.pane = pane
        self._position = position
        self.ref = ref
        self._rendered = False
        self._placeholder = QPixmap()
        self._press_pos = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self.pane._thumb_pressed(self._position, event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._press_pos is not None:
            delta = event.position() - self._press_pos
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._press_pos = None
                self.pane._start_drag(self)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        if event.button() == Qt.MouseButton.LeftButton:
            self.pane._thumb_released(self._position)
        super().mouseReleaseEvent(event)


class PagePane(QWidget):
    """One side of the organizer: an ordered list of :class:`PageRef` thumbs.

    ``refs`` is the current arrangement; ``set_refs`` replaces and re-renders it
    (and clears the selection). Internal same-pane drops are applied here via
    ``apply_move`` and announced with ``pages_changed``; cross-pane drops are
    delegated to the settable ``on_cross_drop(src_pane_id, positions,
    insert_at)`` callback so the dialog can coordinate both panes.
    """

    pages_changed = pyqtSignal()

    def __init__(self, sources: dict[str, fitz.Document], refs: list[PageRef],
                 pane_id: str, title: str, config: dict, parent=None):
        _ensure_qapp()
        super().__init__(parent)
        self.sources = dict(sources or {})
        self.refs = list(refs or [])
        self.pane_id = pane_id
        self.title = title
        self.config = dict(config or {})
        self.on_cross_drop = None
        self.thumbs = []
        self._selected = set()
        self._anchor = None
        self._pending_collapse = None
        self._drop_index = None
        self._rebuilding = False
        self._cols = 0
        self._palette = get_palette_dict(self.config.get("theme", THEME_DARK))
        self.thumb_width = max(60, int(self.config.get("thumb_width", THUMB_WIDTH)))
        self.render_dpi = max(25, int(self.config.get("render_dpi", 150)))
        self.setAcceptDrops(True)
        self._build()

    # ---- construction ----

    def _build(self):
        p = self._palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self._title_label = QLabel(self.title)
        self._title_label.setStyleSheet(
            f"color: {p['text_main']}; font-weight: bold; font-size: 10pt;"
        )
        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 8pt;")
        header.addWidget(self._title_label, 1)
        header.addWidget(self._count_label)
        layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {p['bg_preview_scroll']}; "
            f"border: 1px solid {p['border']}; border-radius: 6px; }}"
        )
        self._container = QWidget()
        self._container.setStyleSheet(f"background-color: {p['bg_preview_scroll']};")
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(THUMB_GAP)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        self._empty_label = QLabel("No pages")
        self._empty_label.setStyleSheet(f"color: {p['text_placeholder']}; font-size: 10pt;")
        self._empty_label.hide()

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(80)
        self._scroll_timer.timeout.connect(self._on_scroll_timeout)
        self._scroll.verticalScrollBar().valueChanged.connect(self._schedule_render)

        self._rebuild_grid()

    # ---- public interface ----

    def set_refs(self, refs: list[PageRef]) -> None:
        self.refs = list(refs or [])
        self._selected = set()
        self._anchor = None
        self._pending_collapse = None
        self._drop_index = None
        self._rebuild_grid()

    def selected_positions(self) -> list[int]:
        return sorted(p for p in self._selected if 0 <= p < len(self.refs))

    def clear_selection(self):
        self._selected = set()
        self._anchor = None
        self._pending_collapse = None
        self._update_thumb_styles()

    def handle_drop_payload(self, text, insert_at) -> bool:
        parsed = parse_drag_payload(text)
        if parsed is None:
            return False
        src_pane_id, positions = parsed
        if not positions:
            return False
        if src_pane_id == self.pane_id:
            return self.handle_internal_drop(positions, insert_at)
        if self.on_cross_drop is None:
            return False
        self.on_cross_drop(src_pane_id, positions, insert_at)
        return True

    def handle_internal_drop(self, positions, insert_at) -> bool:
        positions = [p for p in sorted(set(int(p) for p in positions))
                     if 0 <= p < len(self.refs)]
        if not positions:
            return False
        moved = [self.refs[p] for p in positions]
        result = apply_move(self.refs, self.refs, positions, insert_at)
        if result is None:
            return False
        result, _ = result
        self.set_refs(result)
        self._selected = {i for i, ref in enumerate(self.refs)
                          if any(ref is m for m in moved)}
        self._anchor = min(self._selected) if self._selected else None
        self._update_thumb_styles()
        self.pages_changed.emit()
        return True

    # ---- grid construction ----

    def _rebuild_grid(self):
        if self._rebuilding:
            return
        self._rebuilding = True
        try:
            for thumb in self.thumbs:
                self._grid.removeWidget(thumb)
                thumb.hide()
                thumb.deleteLater()
            self.thumbs = []
            self._set_empty_state(not self.refs)
            self._cols = self._column_count()
            for position, ref in enumerate(self.refs):
                thumb = ClickableThumb(self, position, ref)
                thumb.setObjectName("pageThumb")
                thumb.setFixedSize(self.thumb_width, self._thumb_height(ref))
                thumb.setToolTip(self._tooltip(ref))
                placeholder = self._make_placeholder(ref)
                thumb._placeholder = placeholder
                thumb.setPixmap(placeholder)
                self._add_caption(thumb, ref)
                self._grid.addWidget(thumb, position // self._cols,
                                     position % self._cols,
                                     Qt.AlignmentFlag.AlignCenter)
                self.thumbs.append(thumb)
            self._update_count_label()
            self._update_thumb_styles()
            self._schedule_render()
        finally:
            self._rebuilding = False

    def _set_empty_state(self, empty):
        if empty:
            if self._grid.indexOf(self._empty_label) == -1:
                self._grid.addWidget(self._empty_label, 0, 0, 1, 1,
                                     Qt.AlignmentFlag.AlignCenter)
                self._empty_label.show()
        elif self._grid.indexOf(self._empty_label) != -1:
            self._grid.removeWidget(self._empty_label)
            self._empty_label.hide()

    def _update_count_label(self):
        count = len(self.refs)
        self._count_label.setText(f"{count} page{'s' if count != 1 else ''}")

    def _add_caption(self, thumb, ref):
        caption = QLabel(str(ref.src_page_index + 1), thumb)
        caption.setObjectName("pageCaption")
        caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p = self._palette
        caption.setStyleSheet(
            f"QLabel#pageCaption {{ color: {p['text_main']}; "
            f"background-color: rgba(8, 10, 18, 170); padding: 0 5px; "
            f"border-radius: 3px; font-size: 8pt; }}"
        )
        caption.adjustSize()
        caption.move(CELL_INSET + 3, thumb.height() - caption.height() - CELL_INSET - 3)
        caption.show()
        caption.raise_()

    def _column_count(self):
        viewport_width = self._scroll.viewport().width() if hasattr(self, "_scroll") else 0
        if viewport_width <= 0:
            viewport_width = 420
        inner = viewport_width - 16
        cols = (inner + THUMB_GAP) // (self.thumb_width + THUMB_GAP)
        return max(1, cols)

    def _thumb_height(self, ref):
        ratio = 1.3
        doc = self.sources.get(ref.source_doc_id)
        try:
            if doc is not None:
                rect = doc[ref.src_page_index].rect
                if rect.width > 0:
                    ratio = rect.height / rect.width
        except Exception:
            pass
        return max(40, int(round(self.thumb_width * ratio)))

    def _tooltip(self, ref):
        text = f"{ref.source_doc_id} \u00b7 page {ref.src_page_index + 1}"
        if ref.is_blank:
            text += " \u00b7 blank"
        if ref.is_docsep:
            text += " \u00b7 docsep"
        return text

    # ---- placeholders and rasterization ----

    def _content_box(self, ref_height=None):
        width = max(1, self.thumb_width - 2 * CELL_INSET)
        height = max(1, (ref_height or self.thumb_width) - 2 * CELL_INSET)
        return width, height

    def _make_placeholder(self, ref):
        width, height = self._content_box(self._thumb_height(ref))
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(self._palette["bg_input"]))
        return pixmap

    def _make_page_pixmap(self, ref, width, height):
        page = self.sources[ref.source_doc_id][ref.src_page_index]
        pix = page.get_pixmap(dpi=self.render_dpi)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                       QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > width or pixmap.height() > height:
            pixmap = pixmap.scaled(QSize(width, height),
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        return pixmap

    def _render_thumb(self, thumb):
        width, height = self._content_box(thumb.height())
        try:
            pixmap = self._make_page_pixmap(thumb.ref, width, height)
        except Exception:
            return
        thumb.setPixmap(pixmap)
        thumb._rendered = True

    def _schedule_render(self):
        if self.thumbs:
            self._scroll_timer.start()

    def _on_scroll_timeout(self):
        self._render_visible()
        self._release_far_pixmaps()

    def _visible_band(self, slack=0):
        viewport = self._scroll.viewport()
        if viewport is None:
            return None
        view_height = viewport.height()
        scroll_top = self._scroll.verticalScrollBar().value()
        margin = max(view_height, 400)
        return scroll_top - margin - slack, scroll_top + view_height + margin + slack

    def _render_visible(self):
        band = self._visible_band()
        if band is None:
            return
        low, high = band
        for thumb in self.thumbs:
            if thumb._rendered:
                continue
            top = thumb.geometry().top()
            bottom = top + thumb.height()
            if bottom < low or top > high:
                continue
            self._render_thumb(thumb)

    def _release_far_pixmaps(self):
        viewport = self._scroll.viewport()
        if viewport is None:
            return
        band = self._visible_band(slack=viewport.height())
        if band is None:
            return
        low, high = band
        for thumb in self.thumbs:
            if not thumb._rendered:
                continue
            top = thumb.geometry().top()
            bottom = top + thumb.height()
            if bottom < low or top > high:
                thumb.setPixmap(thumb._placeholder)
                thumb._rendered = False

    # ---- selection ----

    def _thumb_pressed(self, position, modifiers):
        if not (0 <= position < len(self.refs)):
            return
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if shift and self._anchor is not None:
            low, high = sorted((self._anchor, position))
            self._selected = set(range(low, high + 1))
            self._pending_collapse = None
        elif ctrl:
            if position in self._selected:
                self._selected.discard(position)
            else:
                self._selected.add(position)
                self._anchor = position
            self._pending_collapse = None
        elif position in self._selected and len(self._selected) > 1:
            self._pending_collapse = position
        else:
            self._selected = {position}
            self._anchor = position
            self._pending_collapse = None
        self._update_thumb_styles()

    def _thumb_released(self, position):
        if self._pending_collapse is None:
            return
        if self._pending_collapse == position:
            self._selected = {position}
            self._anchor = position
            self._update_thumb_styles()
        self._pending_collapse = None

    def _update_thumb_styles(self):
        p = self._palette
        hot = None
        if self._drop_index is not None and self.thumbs:
            hot = min(self._drop_index, len(self.thumbs) - 1)
        for index, thumb in enumerate(self.thumbs):
            ref = thumb.ref
            if index == hot:
                color, width = p["accent_primary"], 3
            elif ref.is_blank:
                color, width = p["status_bad"], 3
            elif ref.is_docsep:
                color, width = p["status_warn"], 3
            elif index in self._selected:
                color, width = p["accent_primary"], 3
            else:
                color, width = p["border"], 1
            thumb.setStyleSheet(
                f"QLabel#pageThumb {{ background-color: {p['bg_surface']}; "
                f"border: {width}px solid {color}; border-radius: 4px; }}"
            )

    # ---- drag and drop ----

    def _start_drag(self, thumb):
        positions = self.selected_positions()
        if not positions:
            self._selected = {thumb._position}
            self._anchor = thumb._position
            self._update_thumb_styles()
            positions = list(self._selected)
        self._pending_collapse = None
        mime = QMimeData()
        mime.setText(build_drag_payload(self.pane_id, positions))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = thumb.pixmap()
        if pixmap is not None and not pixmap.isNull():
            drag.setPixmap(pixmap)
        drag.exec(QDrag.DropAction.MoveAction)

    def _insert_index_at(self, pos: QPoint):
        for index, thumb in enumerate(self.thumbs):
            local = thumb.mapFrom(self, pos)
            if 0 <= local.x() < thumb.width() and 0 <= local.y() < thumb.height():
                return index + 1 if local.x() * 2 > thumb.width() else index
        return len(self.refs)

    def dragEnterEvent(self, event):
        if parse_drag_payload(event.mimeData().text()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if parse_drag_payload(event.mimeData().text()) is None:
            event.ignore()
            return
        index = self._insert_index_at(event.position().toPoint())
        if index != self._drop_index:
            self._drop_index = index
            self._update_thumb_styles()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        if self._drop_index is not None:
            self._drop_index = None
            self._update_thumb_styles()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        index = self._insert_index_at(event.position().toPoint())
        handled = self.handle_drop_payload(event.mimeData().text(), index)
        self._drop_index = None
        self._update_thumb_styles()
        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---- layout ----

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._rebuilding or not hasattr(self, "_grid"):
            return
        if self._column_count() != self._cols:
            self._rebuild_grid()


class OrganizeDialog(QDialog):
    """Two-pane organizer shell: Save commits, Undo Save reverts the commit.

    ``commit_fn(refs_a, refs_b)`` applies a save and returns an ``undo()``
    callable; Save stores it, Undo Save calls it. Closing (Cancel/Esc) never
    reverts an already-completed save. Cross-pane drops are coordinated here
    because only this dialog can see both panes.
    """

    def __init__(self, sources: dict[str, fitz.Document], refs_a: list[PageRef],
                 refs_b: list[PageRef], title_a: str, title_b: str, commit_fn,
                 config: dict, parent=None):
        _ensure_qapp()
        super().__init__(parent)
        self.setWindowTitle("Organize Pages")
        self.setMinimumSize(880, 560)
        self._commit_fn = commit_fn
        self.config = dict(config or {})
        self._initial_a = list(refs_a)
        self._initial_b = list(refs_b)
        self._undo_fn = None
        self._saved = False

        p = get_palette_dict(self.config.get("theme", THEME_DARK))
        self.setStyleSheet(
            f"QDialog {{ background-color: {p['bg_window']}; color: {p['text_main']}; }}"
            f"QPushButton {{ background-color: {p['bg_input']}; color: {p['text_main']};"
            f" border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 16px;"
            f" font-size: 9pt; }}"
            f"QPushButton:hover {{ border: 1px solid {p['border_focus']}; }}"
            f"QPushButton:disabled {{ background-color: {p['bg_tab']};"
            f" color: {p['text_placeholder']}; border: 1px solid {p['border']}; }}"
        )

        self.pane_a = PagePane(sources, list(refs_a), "a", title_a, self.config)
        self.pane_b = PagePane(sources, list(refs_b), "b", title_b, self.config)
        self.pane_a.on_cross_drop = self._cross_to_a
        self.pane_b.on_cross_drop = self._cross_to_b

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.pane_a)
        splitter.addWidget(self.pane_b)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._on_save)
        self.undo_btn = QPushButton("Undo Save")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._on_undo)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.undo_btn)
        buttons.addStretch()
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_shortcut.activated.connect(self._on_undo)
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.activated.connect(self.reject)

    # ---- cross-pane moves ----

    def _cross_to_b(self, src_id, positions, insert_at):
        result = apply_move(self.pane_a.refs, self.pane_b.refs, positions, insert_at)
        if result is None:
            return
        a_refs, b_refs = result
        self.pane_a.set_refs(a_refs)
        self.pane_b.set_refs(b_refs)

    def _cross_to_a(self, src_id, positions, insert_at):
        result = apply_move(self.pane_b.refs, self.pane_a.refs, positions, insert_at)
        if result is None:
            return
        b_refs, a_refs = result
        self.pane_a.set_refs(a_refs)
        self.pane_b.set_refs(b_refs)

    # ---- save / undo ----

    def _on_save(self):
        if self._saved:
            return
        self._undo_fn = self._commit_fn(self.pane_a.refs, self.pane_b.refs)
        self._saved = True
        self.save_btn.setEnabled(False)
        self.undo_btn.setEnabled(True)
        self.cancel_btn.setText("Close")

    def _on_undo(self):
        if self._undo_fn is not None:
            self._undo_fn()
        self._undo_fn = None
        self.pane_a.set_refs(self._initial_a)
        self.pane_b.set_refs(self._initial_b)
        self._saved = False
        self.save_btn.setEnabled(True)
        self.undo_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel")
