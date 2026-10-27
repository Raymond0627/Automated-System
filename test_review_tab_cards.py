import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_refresh_doc_list_instantiates_cards(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path)})
    tab.pending_docs = [{
        "original_path": str(tmp_path / "dummy.pdf"),
        "original_filename": "dummy.pdf",
        "company_name": "Test Co",
        "detected_date": "2024-01-01",
        "qc_status": "PASS",
        "auto_confirmed": False,
    }]
    # Calling refresh_doc_list must instantiate DocCardWidget without NameError
    tab.refresh_doc_list(select_row=None)
    assert tab.doc_list.count() == 1
    assert len(tab.doc_card_widgets) == 1
