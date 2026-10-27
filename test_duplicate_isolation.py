"""Tests for duplicate isolation (original vs duplicate must never share state)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from doc_identity import clone_document, doc_key, ensure_doc_id


def _doc(name="f.pdf", path="C:/x/f.pdf", **extra):
    d = {"original_path": path, "original_filename": name}
    d.update(extra)
    return d


def test_clone_isolates_mutable_fields():
    original = _doc(
        doc_id="orig",
        blank_pages=[1, 2],
        docsep_pages=[0],
        qc={"status": "passed"},
        _pending_pdf_bytes=b"ORIG",
    )

    dup = clone_document(original)

    assert dup["doc_id"] != original["doc_id"], "duplicate needs its own identity"
    assert dup["original_path"] == original["original_path"], "must still point at the source"

    dup["blank_pages"].append(9)
    dup["docsep_pages"].append(7)
    dup["qc"]["status"] = "failed"

    assert original["blank_pages"] == [1, 2]
    assert original["docsep_pages"] == [0]
    assert original["qc"]["status"] == "passed"


def test_clone_keeps_shared_bytes_reference():
    original = _doc(_pending_pdf_bytes=b"PDFDATA")
    dup = clone_document(original)
    assert dup["_pending_pdf_bytes"] == b"PDFDATA"
    assert dup["_pending_pdf_bytes"] is original["_pending_pdf_bytes"]


def test_ensure_doc_id_unique_for_same_path_duplicates():
    a = _doc(name="f.pdf", path="C:/x/f.pdf")
    b = _doc(name="f(1).pdf", path="C:/x/f.pdf")

    ida = ensure_doc_id(a)
    idb = ensure_doc_id(b)

    assert ida and idb
    assert ida != idb, "duplicates share a path, so the id must include the filename"
    # stable across calls / fresh dicts (legacy index backfill)
    assert ensure_doc_id(_doc(name="f.pdf", path="C:/x/f.pdf")) == ida
    assert ensure_doc_id(a) == ida


def test_doc_key_prefers_doc_id():
    assert doc_key({"doc_id": "abc", "original_path": "C:/x/f.pdf"}) == "abc"
    assert doc_key(_doc()) == doc_key(_doc()), "fallback key must be deterministic"
    # Documents sharing a path but with different names must not collide.
    assert doc_key(_doc(name="f.pdf")) != doc_key(_doc(name="f(1).pdf"))


def test_session_snapshot_does_not_cross_apply_bytes():
    import session

    class FakePdf:
        def tobytes(self, **kwargs):
            return b"ACTIVE-EDIT"

    class RT:
        pass

    rt = RT()
    rt.config = {}
    rt.active_tab = "pending"
    rt.active_index = 1
    rt.document_modified = True
    rt.current_pdf_doc = FakePdf()
    rt.all_flagged_docs = [
        _doc(name="f.pdf", doc_id="orig", _pending_pdf_bytes=b"ORIG"),
        _doc(name="f(1).pdf", doc_id="dup", _pending_pdf_bytes=b"DUP"),
    ]
    rt.auto_confirmed_docs = []
    rt.view_mode = "variable"
    rt.zoom_level = 100

    def _get_current_doc():
        return rt.all_flagged_docs[rt.active_index]

    rt._get_current_doc = _get_current_doc

    snap = session.serialize(rt)
    by_id = {d["doc_id"]: session._unb64(d.get("_pending_pdf_bytes")) for d in snap["all_flagged_docs"]}

    assert by_id["dup"] == b"ACTIVE-EDIT"
    assert by_id["orig"] != b"ACTIVE-EDIT", "editing the duplicate must not touch the original"


def test_duplicate_document_at_isolates_edits(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    from ui.review_tab import ReviewTab

    class FakeTab:
        def __init__(self):
            self.active_tab = "pending"
            self.active_index = 0
            self.docs = [_doc(
                name="f.pdf",
                path=str(tmp_path / "f.pdf"),
                doc_id="orig",
                blank_pages=[1],
                docsep_pages=[0],
                _pending_pdf_bytes=b"ORIG",
            )]
            self.all_flagged_docs = self.docs
            self._doc_undo_stack = []
            self._doc_undo_max = 10

        def _get_current_docs(self):
            return self.docs

        def _save_flagged_updates(self):
            pass

        def refresh_doc_list(self, select_row=None):
            pass

        def _session_changed(self):
            pass

    fake = FakeTab()
    dup = ReviewTab._duplicate_document_at(fake, 0)

    assert dup is not None
    assert dup["doc_id"] != fake.docs[0]["doc_id"]
    assert dup["original_path"] == fake.docs[0]["original_path"], "logical duplicate keeps the source"

    # Simulate a page delete + blank mark on the duplicate.
    dup["blank_pages"].append(5)
    dup["docsep_pages"].append(3)
    dup["_pending_pdf_bytes"] = b"DUPLICATE-EDIT"

    assert fake.docs[0]["blank_pages"] == [1]
    assert fake.docs[0]["docsep_pages"] == [0]
    assert fake.docs[0]["_pending_pdf_bytes"] == b"ORIG"
