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
