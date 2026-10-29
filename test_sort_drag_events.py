import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QMouseEvent
from ui.interactive_preview import InteractivePageLabel

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

class MockSortHandler:
    def __init__(self):
        self.dragged_slot = None
        self.released_slot = None
        self.moves = []

    def on_sort_drag(self, slot):
        self.dragged_slot = slot

    def on_drag_move(self, slot, global_pos):
        self.moves.append((slot, global_pos))

    def on_sort_release(self, slot):
        self.released_slot = slot

def test_interactive_page_label_drag_events(qapp):
    handler = MockSortHandler()
    lbl = InteractivePageLabel(page_num=0)
    lbl._sort_mode = True
    lbl._sort_slot = 2
    lbl._sort_handler = handler
    lbl.resize(200, 300)

    # 1. Mouse press
    press_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(50, 50).toPointF(),
        QPoint(150, 150).toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    lbl.mousePressEvent(press_event)
    assert lbl._press_screen == QPoint(150, 150)

    # 2. Mouse move beyond threshold (150+20 = 170)
    move_event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPoint(70, 70).toPointF(),
        QPoint(170, 170).toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    lbl.mouseMoveEvent(move_event)
    assert handler.dragged_slot == 2
    assert lbl._drag_active is True

    # 3. Mouse release
    release_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPoint(70, 70).toPointF(),
        QPoint(170, 170).toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    lbl.mouseReleaseEvent(release_event)
    assert handler.released_slot == 2
    assert lbl._drag_active is False
