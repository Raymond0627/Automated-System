from typing import Any, Dict, List, Optional, Tuple
from PyQt6.QtWidgets import QLabel
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF

from candidate_scorer import find_line_at_point


class ClickableLabel(QLabel):
    """Base clickable page label with sort and drag events."""
    clicked = pyqtSignal(int)

    def __init__(self, page_num: int, parent=None):
        super().__init__(parent)
        self._page_num = page_num
        self._sort_slot = -1
        self._sort_mode = False
        self._sort_handler = None
        self._press_screen = None
        self._drag_active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._sort_mode:
                self._press_screen = event.globalPosition().toPoint()
                self._drag_active = False
                event.accept()
                return
            self.clicked.emit(self._page_num)
        super().mousePressEvent(event)


class InteractivePageLabel(ClickableLabel):
    """Interactive preview label that renders company candidate overlays and supports click selection."""
    company_selected = pyqtSignal(str)

    def __init__(self, page_num: int, parent=None):
        super().__init__(page_num, parent)
        self._candidates: List[Dict[str, Any]] = []
        self._word_boxes: List[Dict[str, Any]] = []
        self._page_size: Tuple[float, float] = (612.0, 792.0)
        self.setMouseTracking(True)

    def set_candidates(
        self,
        candidates: List[Dict[str, Any]],
        word_boxes: List[Dict[str, Any]],
        page_size: Tuple[float, float] = (612.0, 792.0)
    ):
        self._candidates = candidates or []
        self._word_boxes = word_boxes or []
        self._page_size = (max(page_size[0], 1.0), max(page_size[1], 1.0))
        self.update()

    def select_candidate_by_badge(self, badge_num: int) -> bool:
        for c in self._candidates:
            if c.get("badge") == badge_num:
                self.company_selected.emit(c.get("text", ""))
                return True
        return False

    def _scale_bbox_to_widget(self, bbox: List[float]) -> QRectF:
        x0, y0, x1, y1 = bbox
        pw, ph = self._page_size
        ww = self.width()
        wh = self.height()
        sx = ww / pw
        sy = wh / ph
        return QRectF(x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy)

    def _widget_point_to_page(self, pt: QPoint) -> Tuple[float, float]:
        pw, ph = self._page_size
        ww = max(self.width(), 1)
        wh = max(self.height(), 1)
        sx = pw / ww
        sy = ph / wh
        return (pt.x() * sx, pt.y() * sy)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._sort_mode:
            pos = event.position().toPoint()
            # 1. Check if clicked inside any candidate bounding box
            for c in self._candidates:
                rect = self._scale_bbox_to_widget(c["bbox"])
                if rect.contains(event.position()):
                    self.company_selected.emit(c.get("text", ""))
                    super().mousePressEvent(event)
                    return

            # 2. Check if clicked on any unhighlighted line
            page_pt = self._widget_point_to_page(pos)
            clicked_line = find_line_at_point(page_pt, self._word_boxes)
            if clicked_line:
                self.company_selected.emit(clicked_line)
                super().mousePressEvent(event)
                return

        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Transparent overlays: Keep document rendering 100% natural and clean
        # without drawing color fills or overlapping boxes that obscure text.
