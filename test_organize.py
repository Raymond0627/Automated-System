import sys
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).parent))

from organize_model import (
    PageRef, page_refs_for_source, markers_for, extract, insert, apply_move,
    build_pdf_bytes,
)


def _pdf(n_pages):
    d = fitz.open()
    for i in range(n_pages):
        d.new_page().insert_text((72, 72), f"page {i}")
    return d


def test_page_refs_for_source_marks_blank_and_docsep():
    refs = page_refs_for_source("a", 4, blank_pages=[1], docsep_pages=[3])
    assert [(r.src_page_index, r.is_blank, r.is_docsep) for r in refs] == [
        (0, False, False), (1, True, False), (2, False, False), (3, False, True)
    ]


def test_page_ref_field_name_is_source_doc_id():
    ref = PageRef(source_doc_id="a", src_page_index=0)
    assert ref.source_doc_id == "a"
    refs = page_refs_for_source(source_doc_id="a", page_count=1)
    assert refs[0].source_doc_id == "a"


def test_markers_for_rebuilds_indices():
    refs = [PageRef("a", 0, False, False),
            PageRef("a", 1, True, False),
            PageRef("b", 0, False, True)]
    blank, docsep = markers_for(refs)
    assert blank == [1]
    assert docsep == [2]


def test_extract_preserves_order_and_removes():
    refs = [PageRef("a", i, False, False) for i in range(4)]
    remaining, moved = extract(refs, [1, 3])
    assert [r.src_page_index for r in remaining] == [0, 2]
    assert [r.src_page_index for r in moved] == [1, 3]


def test_insert_clamps_position():
    refs = [PageRef("a", i, False, False) for i in range(2)]
    out = insert(refs, [PageRef("b", 0, False, False)], 99)
    assert [r.src_page_index for r in out] == [0, 1, 0]


def test_apply_move_cross_pane_moves_pages():
    a = [PageRef("a", i, False, False) for i in range(3)]
    b = [PageRef("b", i, False, False) for i in range(2)]
    a_new, b_new = apply_move(a, b, [0], 1)
    assert [r.src_page_index for r in a_new] == [1, 2]
    assert [r.src_page_index for r in b_new] == [0, 0, 1]  # b0, moved a0, b1


def test_apply_move_blocks_emptying_source():
    a = [PageRef("a", 0, False, False)]
    b = [PageRef("b", i, False, False) for i in range(2)]
    assert apply_move(a, b, [0], 0) is None


def test_apply_move_same_pane_reorder():
    a = [PageRef("a", i, False, False) for i in range(3)]
    out, _ = apply_move(a, a, [0], 3)
    assert [r.src_page_index for r in out] == [1, 2, 0]


def test_build_pdf_bytes_single_source():
    a = _pdf(2)
    data = build_pdf_bytes({"a": a}, [PageRef("a", 0, False, False)])
    out = fitz.open("pdf", data)
    assert len(out) == 1
    out.close()


def test_build_pdf_bytes_multi_source_order():
    a = _pdf(2)
    b = _pdf(2)
    data = build_pdf_bytes(
        {"a": a, "b": b},
        [PageRef("a", 1, False, False), PageRef("b", 0, False, False),
         PageRef("a", 0, False, False)],
    )
    out = fitz.open("pdf", data)
    assert len(out) == 3
    out.close()


def _pane(n_pages=3, pane_id="a", title="Doc A"):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.organize_dialog import PagePane
    return PagePane(
        {"a": _pdf(n_pages)}, page_refs_for_source("a", n_pages),
        pane_id, title, {"theme": "Dark Mode"},
    )


def test_page_pane_refs_roundtrip():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.organize_dialog import PagePane

    pane = PagePane({"a": _pdf(3)}, page_refs_for_source("a", 3), "a",
                    "Doc A", {"theme": "Dark Mode"})
    assert len(pane.refs) == 3
    pane.set_refs(page_refs_for_source("a", 2))
    assert len(pane.refs) == 2


def test_page_pane_drag_payload_roundtrip():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.organize_dialog import build_drag_payload, parse_drag_payload

    assert build_drag_payload("a", [2, 0]) == "pane:a:0,2"
    assert parse_drag_payload("pane:a:0,2") == ("a", [0, 2])
    assert parse_drag_payload("pane:b:1,0") == ("b", [0, 1])
    assert parse_drag_payload("") is None
    assert parse_drag_payload("not a payload") is None
    assert parse_drag_payload("pane::1") is None
    assert parse_drag_payload("pane:a:1,x") is None


def test_page_pane_internal_drop_reorders_and_emits():
    pane = _pane(3)
    fired = []
    pane.pages_changed.connect(lambda: fired.append(True))

    assert pane.handle_drop_payload("pane:a:0", 3) is True
    assert [r.src_page_index for r in pane.refs] == [1, 2, 0]
    assert fired == [True]

    assert pane.handle_drop_payload("pane:a:0,1,2", 0) is False
    assert [r.src_page_index for r in pane.refs] == [1, 2, 0]


def test_page_pane_cross_drop_delegates_to_callback():
    pane = _pane(3)
    calls = []
    pane.on_cross_drop = lambda src_id, positions, insert_at: calls.append(
        (src_id, positions, insert_at)
    )
    fired = []
    pane.pages_changed.connect(lambda: fired.append(True))

    assert pane.handle_drop_payload("pane:b:1,0", 2) is True
    assert calls == [("b", [0, 1], 2)]
    assert fired == []
    assert [r.src_page_index for r in pane.refs] == [0, 1, 2]

    pane.on_cross_drop = None
    assert pane.handle_drop_payload("pane:b:1", 0) is False
    assert len(calls) == 1


def test_page_pane_selection_positions_sorted():
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    pane = _pane(3)

    def press(thumb, modifiers=Qt.KeyboardModifier.NoModifier):
        thumb.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(4, 4), QPointF(4, 4),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers,
        ))

    press(pane.thumbs[0])
    assert pane.selected_positions() == [0]
    press(pane.thumbs[2], Qt.KeyboardModifier.ControlModifier)
    assert pane.selected_positions() == [0, 2]
    press(pane.thumbs[2])
    assert pane.selected_positions() == [0, 2]
    press(pane.thumbs[1])
    assert pane.selected_positions() == [1]


def test_page_pane_drop_event_routes_by_payload():
    from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
    from PyQt6.QtGui import QDropEvent

    pane = _pane(3)
    # drop positions are geometric: show the pane so the grid assigns real
    # thumb rectangles (a never-shown pane lays everything out at 0,0)
    pane.show()
    pane.layout().activate()
    from PyQt6.QtWidgets import QApplication
    QApplication.instance().processEvents()
    calls = []
    pane.on_cross_drop = lambda src_id, positions, insert_at: calls.append(
        (src_id, positions, insert_at)
    )

    def drop(text, thumb, local_x):
        mime = QMimeData()
        mime.setText(text)
        pos = QPointF(thumb.mapTo(pane, QPoint(local_x, 4)))
        event = QDropEvent(pos, Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        pane.dropEvent(event)
        return event

    # cross-pane payload: delegates to the callback, left half of thumb 1
    # inserts at index 1, and nothing about this pane changes
    event = drop("pane:b:0", pane.thumbs[1], 3)
    assert calls == [("b", [0], 1)]
    assert event.isAccepted()
    assert [r.src_page_index for r in pane.refs] == [0, 1, 2]

    # same-pane payload: applied here, announced with pages_changed
    fired = []
    pane.pages_changed.connect(lambda: fired.append(True))
    event = drop("pane:a:2", pane.thumbs[0], 3)
    assert [r.src_page_index for r in pane.refs] == [2, 0, 1]
    assert fired == [True]
    assert event.isAccepted()

    # unrelated mime data is refused
    mime = QMimeData()
    mime.setText("text/plain")
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pane.dropEvent(event)
    assert not event.isAccepted()
    assert calls == [("b", [0], 1)]
    pane.hide()


def test_organize_dialog_save_then_undo():
    from ui.organize_dialog import OrganizeDialog, _ensure_qapp
    _ensure_qapp()

    calls = {"commit": 0, "undo": 0}

    def commit(refs_a, refs_b):
        calls["commit"] += 1
        return lambda: calls.__setitem__("undo", calls["undo"] + 1)

    dlg = OrganizeDialog(
        sources={"a": _pdf(2), "b": _pdf(2)},
        refs_a=page_refs_for_source("a", 2),
        refs_b=page_refs_for_source("b", 2),
        title_a="A", title_b="B",
        commit_fn=commit,
        config={"theme": "Dark Mode"},
    )
    ra, rb = apply_move(dlg.pane_a.refs, dlg.pane_b.refs, [0], 0)
    dlg.pane_a.set_refs(ra)
    dlg.pane_b.set_refs(rb)
    dlg._on_save()
    assert calls["commit"] == 1
    assert dlg._saved is True
    assert dlg._undo_fn is not None
    dlg._on_undo()
    assert calls["undo"] == 1
    assert dlg._saved is False


def test_organize_dialog_cross_drop_moves_pages_in_drop_direction():
    from ui.organize_dialog import OrganizeDialog, _ensure_qapp
    _ensure_qapp()

    dlg = OrganizeDialog(
        sources={"a": _pdf(3), "b": _pdf(2)},
        refs_a=page_refs_for_source("a", 3),
        refs_b=page_refs_for_source("b", 2),
        title_a="A", title_b="B",
        commit_fn=lambda ra, rb: (lambda: None),
        config={"theme": "Dark Mode"},
    )

    # A's page 0 dropped on pane B at index 1 -> leaves A, lands in B
    assert dlg.pane_b.handle_drop_payload("pane:a:0", 1) is True
    assert [(r.source_doc_id, r.src_page_index) for r in dlg.pane_a.refs] == [
        ("a", 1), ("a", 2)]
    assert [(r.source_doc_id, r.src_page_index) for r in dlg.pane_b.refs] == [
        ("b", 0), ("a", 0), ("b", 1)]

    # B's page 0 dropped on pane A at index 0 -> leaves B, lands in A
    assert dlg.pane_a.handle_drop_payload("pane:b:0", 0) is True
    assert [(r.source_doc_id, r.src_page_index) for r in dlg.pane_a.refs] == [
        ("b", 0), ("a", 1), ("a", 2)]
    assert [(r.source_doc_id, r.src_page_index) for r in dlg.pane_b.refs] == [
        ("a", 0), ("b", 1)]

    # a move that would empty the source pane is blocked: both panes unchanged
    before_a = list(dlg.pane_a.refs)
    before_b = list(dlg.pane_b.refs)
    dlg.pane_b.handle_drop_payload("pane:a:0,1,2", 0)
    assert dlg.pane_a.refs == before_a
    assert dlg.pane_b.refs == before_b


def _review_tab(tmp_path):
    from ui.organize_dialog import _ensure_qapp
    _ensure_qapp()
    from ui.review_tab import ReviewTab
    # output_root points at a directory that does not exist so the delayed
    # ReviewedScanWorker (QTimer at 150ms) never starts a thread.
    return ReviewTab({"input_root": str(tmp_path), "output_root": str(tmp_path / "_out"),
                      "flagged_root": "", "theme": "Dark Mode"})


def test_apply_organize_pending_is_in_memory(tmp_path):
    a = _pdf(2); b = _pdf(2)
    pa = tmp_path / "a.pdf"; pb = tmp_path / "b.pdf"
    a.save(str(pa)); b.save(str(pb))
    doc_a = {"original_path": str(pa), "original_filename": "a.pdf",
             "division_code": "155", "company_name": "Acme",
             "detected_date": "2024-05-01", "blank_pages": [], "docsep_pages": []}
    doc_b = {"original_path": str(pb), "original_filename": "b.pdf",
             "division_code": "155", "company_name": "Acme",
             "detected_date": "2024-05-01", "blank_pages": [0], "docsep_pages": []}
    rt = _review_tab(tmp_path)
    orig_pa = pa.read_bytes(); orig_pb = pb.read_bytes()
    src_a = fitz.open(str(pa)); src_b = fitz.open(str(pb))
    refs_a = page_refs_for_source("a", 2)
    refs_b = page_refs_for_source("b", 2, blank_pages=[0])
    # move a's page 0 into b at position 0
    refs_a2, refs_b2 = apply_move(refs_a, refs_b, [0], 0)
    undo = rt._apply_organize("pending", doc_a, doc_b, {"a": src_a, "b": src_b}, refs_a2, refs_b2)
    assert "_pending_pdf_bytes" in doc_a and "_pending_pdf_bytes" in doc_b
    assert fitz.open("pdf", doc_a["_pending_pdf_bytes"]).page_count == 1
    assert fitz.open("pdf", doc_b["_pending_pdf_bytes"]).page_count == 3
    # blank marker traveled with b page 0 which is now index 1
    assert doc_b["blank_pages"] == [1]
    undo()
    assert "_pending_pdf_bytes" not in doc_a
    assert "_pending_pdf_bytes" not in doc_b
    assert doc_a["blank_pages"] == []
    assert doc_b["blank_pages"] == [0]
    # the on-disk originals are untouched: organize is in-memory for pending
    assert pa.read_bytes() == orig_pa
    assert pb.read_bytes() == orig_pb
    src_a.close(); src_b.close()


def test_apply_organize_reviewed_overwrites_and_undo_restores(tmp_path):
    a = _pdf(2); b = _pdf(2)
    pa = tmp_path / "1.pdf"; pb = tmp_path / "2.pdf"
    a.save(str(pa)); b.save(str(pb))
    orig_a = pa.read_bytes(); orig_b = pb.read_bytes()
    doc_a = {"original_path": str(pa), "original_filename": "1.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    doc_b = {"original_path": str(pb), "original_filename": "2.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    rt = _review_tab(tmp_path)
    src_a = fitz.open(str(pa)); src_b = fitz.open(str(pb))
    refs_a = page_refs_for_source("a", 2); refs_b = page_refs_for_source("b", 2)
    refs_a2, refs_b2 = apply_move(refs_a, refs_b, [0], 0)
    undo = rt._apply_organize("reviewed", doc_a, doc_b, {"a": src_a, "b": src_b}, refs_a2, refs_b2)
    assert fitz.open(str(pa)).page_count == 1
    assert fitz.open(str(pb)).page_count == 3
    assert pa.read_bytes() != orig_a and pb.read_bytes() != orig_b
    undo()
    assert pa.read_bytes() == orig_a
    assert pb.read_bytes() == orig_b
    src_a.close(); src_b.close()


def test_apply_organize_reviewed_missing_file_raises(tmp_path):
    import pytest
    doc_a = {"original_path": str(tmp_path / "gone.pdf"), "original_filename": "1.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    doc_b = dict(doc_a, original_path=str(tmp_path / "b.pdf"))
    rt = _review_tab(tmp_path)
    with pytest.raises(FileNotFoundError):
        rt._apply_organize("reviewed", doc_a, doc_b, {}, [], [])
    assert not (tmp_path / "b.pdf").exists()


def test_apply_organize_reviewed_mid_write_restores_first_file(tmp_path):
    from ui.review_tab import ReviewTab
    a = _pdf(2); b = _pdf(2)
    pa = tmp_path / '1.pdf'; pb = tmp_path / '2.pdf'
    a.save(str(pa)); b.save(str(pb))
    orig_a = pa.read_bytes(); orig_b = pb.read_bytes()
    doc_a = {'original_path': str(pa), 'original_filename': '1.pdf',
             'division_code': '155', 'company_name': 'Acme', 'blank_pages': [], 'docsep_pages': []}
    doc_b = {'original_path': str(pb), 'original_filename': '2.pdf',
             'division_code': '155', 'company_name': 'Acme', 'blank_pages': [], 'docsep_pages': []}
    rt = _review_tab(tmp_path)
    src_a = fitz.open(str(pa)); src_b = fitz.open(str(pb))
    refs_a = page_refs_for_source('a', 2); refs_b = page_refs_for_source('b', 2)
    refs_a2, refs_b2 = apply_move(refs_a, refs_b, [0], 0)
    import builtins
    orig_open = builtins.open
    writes = []
    class FailingWrite:
        def __init__(self, *args, **kwargs):
            self._f = orig_open(*args, **kwargs)
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return self._f.__exit__(exc_type, exc, tb)
        def write(self, data):
            writes.append(True)
            if len(writes) == 2: raise IOError('disk full')
            return self._f.write(data)
    builtins.open = FailingWrite
    try:
        import pytest
        with pytest.raises(IOError):
            rt._apply_organize('reviewed', doc_a, doc_b, {'a': src_a, 'b': src_b}, refs_a2, refs_b2)
    finally:
        builtins.open = orig_open
    assert pa.read_bytes() == orig_a
    src_a.close(); src_b.close()

def test_dialog_no_op_save_does_not_call_commit(tmp_path):
    from ui.organize_dialog import OrganizeDialog, _ensure_qapp
    _ensure_qapp()
    calls = []
    dlg = OrganizeDialog(
        sources={'a': _pdf(2), 'b': _pdf(2)},
        refs_a=page_refs_for_source('a', 2),
        refs_b=page_refs_for_source('b', 2),
        title_a='A', title_b='B',
        commit_fn=lambda ra, rb: calls.append((list(ra), list(rb))) or (lambda: None),
        config={'theme': 'Dark Mode'},
    )
    dlg.save_btn.click()
    assert len(calls) == 0
