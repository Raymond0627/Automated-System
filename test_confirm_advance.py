import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_confirm_date_advances_smoothly_without_losing_track(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path), "output_root": str(tmp_path)})
    tab.resize(1000, 600)
    tab.show()
    qapp.processEvents()

    # Create dummy pending docs
    docs = [{
        "original_path": str(tmp_path / f"doc_{i}.pdf"),
        "original_filename": f"doc_{i}.pdf",
        "company_name": f"Company {i}",
        "detected_date": "2024-01-01",
        "qc_status": "PASS",
        "auto_confirmed": False,
    } for i in range(10)]
    tab.pending_docs = list(docs)
    tab.all_flagged_docs = list(docs)

    tab.refresh_doc_list(select_row=3)
    qapp.processEvents()
    assert tab.active_index == 3
    assert tab.doc_list.currentRow() == 3

    # Mock finalize so we don't need real PDF bytes
    def fake_finalize(doc, cfg):
        return {"success": True, "final_filename": doc["original_filename"], "output_path": doc["original_path"]}
    
    import pipeline
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pipeline, "finalize_single_document", fake_finalize)

    # Calling confirm_date on document 3
    tab.confirm_date()
    qapp.processEvents()

    # After doc 3 is confirmed, the next document moves into index 3
    # User must NOT be reset to -1 or row 0!
    assert tab.active_index == 3
    assert tab.doc_list.currentRow() == 3
    assert len(tab.pending_docs) == 9
