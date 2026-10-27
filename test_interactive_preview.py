import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint
from ui.interactive_preview import InteractivePageLabel

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_interactive_page_label_candidate_badge(qapp):
    lbl = InteractivePageLabel(page_num=0)
    selected = []
    lbl.company_selected.connect(selected.append)
    candidates = [
        {"text": "PIONEER INSURANCE", "bbox": [50, 50, 200, 70], "score": 90, "badge": 1, "is_high_conf": True}
    ]
    lbl.set_candidates(candidates, word_boxes=[], page_size=(612, 792))
    lbl.select_candidate_by_badge(1)
    assert selected == ["PIONEER INSURANCE"]
