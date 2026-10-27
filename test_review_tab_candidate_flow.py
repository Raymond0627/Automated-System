import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_review_tab_undo_and_shortcuts(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark"})
    tab.company_input.setText("INITIAL CO")
    tab._set_company_with_undo("NEW CO")
    assert tab.company_input.text() == "NEW CO"
    tab._undo_company()
    assert tab.company_input.text() == "INITIAL CO"
