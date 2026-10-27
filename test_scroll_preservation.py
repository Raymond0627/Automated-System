import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSize
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_refresh_doc_list_preserves_scroll_and_selection(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path)})
    tab.resize(1000, 600)
    tab.show()
    qapp.processEvents()

    # Create 30 items
    docs = [{
        "original_path": str(tmp_path / f"doc_{i}.pdf"),
        "original_filename": f"doc_{i}.pdf",
        "company_name": f"Company {i}",
        "detected_date": "2024-01-01",
        "qc_status": "PASS",
        "auto_confirmed": False,
    } for i in range(30)]
    tab.pending_docs = docs
    tab.all_flagged_docs = list(docs)
    
    tab.refresh_doc_list(select_row=0)
    qapp.processEvents()
    assert tab.doc_list.count() == 30

    # Select row 20 and scroll down
    tab.active_index = 20
    tab.selected_indices = [20]
    tab.doc_list.setCurrentRow(20)
    tab.doc_list.verticalScrollBar().setValue(50)
    val = tab.doc_list.verticalScrollBar().value()
    
    # Refresh without explicit select_row (e.g. after organize save or background update)
    tab.refresh_doc_list()
    qapp.processEvents()

    # Must preserve row 20 and scroll position
    assert tab.doc_list.currentRow() == 20
    assert tab.active_index == 20
    assert tab.doc_list.verticalScrollBar().value() == val
