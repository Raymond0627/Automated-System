import sys
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).parent))

from test_organize import _pdf, _review_tab


def _patch_confirm(monkeypatch):
    """Auto-confirm the modal merge confirmation dialog."""
    from PyQt6.QtWidgets import QMessageBox
    import ui.review_tab as rt_mod
    monkeypatch.setattr(
        rt_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_merge_documents_pending_appends_target_keeps_and_removes_second(tmp_path, monkeypatch):
    _patch_confirm(monkeypatch)
    rt = _review_tab(tmp_path)
    a = _pdf(1)
    b = _pdf(1)
    doc_a = {"original_path": str(tmp_path / "a.pdf"), "original_filename": "a.pdf",
             "division_code": "155", "company_name": "Acme",
             "blank_pages": [], "docsep_pages": [],
             "_pending_pdf_bytes": a.tobytes(garbage=4, deflate=True)}
    doc_b = {"original_path": str(tmp_path / "b.pdf"), "original_filename": "b.pdf",
             "division_code": "155", "company_name": "Acme",
             "blank_pages": [0], "docsep_pages": [],
             "_pending_pdf_bytes": b.tobytes(garbage=4, deflate=True)}
    rt.active_tab = "pending"
    rt.pending_docs = [doc_a, doc_b]
    rt.all_flagged_docs = [doc_a, doc_b]

    rt._merge_documents([doc_a, doc_b])

    merged = fitz.open("pdf", doc_a["_pending_pdf_bytes"])
    assert merged.page_count == 2
    merged.close()
    assert doc_b not in rt.pending_docs
    assert len(rt._doc_undo_stack) == 1

    rt._undo_document_deletion()
    assert doc_b in rt.pending_docs


def test_merge_reviewed_appends_keeps_first_name_and_deletes_second(tmp_path, monkeypatch):
    _patch_confirm(monkeypatch)
    rt = _review_tab(tmp_path)
    a = _pdf(1)
    b = _pdf(1)
    pa = tmp_path / "1.pdf"
    pb = tmp_path / "2.pdf"
    a.save(str(pa))
    b.save(str(pb))
    doc_a = {"original_path": str(pa), "original_filename": "1.pdf",
             "division_code": "155", "company_name": "Acme",
             "blank_pages": [], "docsep_pages": []}
    doc_b = {"original_path": str(pb), "original_filename": "2.pdf",
             "division_code": "155", "company_name": "Acme",
             "blank_pages": [], "docsep_pages": []}
    rt.active_tab = "reviewed"
    rt.reviewed_docs = [doc_a, doc_b]

    rt._merge_reviewed([doc_a, doc_b])

    out = fitz.open(str(pa))
    assert out.page_count == 2
    out.close()
    assert pa.exists()
    assert not pb.exists()
    assert doc_a["original_filename"] == "1.pdf"
