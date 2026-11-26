"""Page edits must refresh one label, not rebuild the whole preview."""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

import fitz
from PyQt6.QtWidgets import QApplication, QLabel

from ui.review_tab import ReviewTab

_APP = None


def _app():
    global _APP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _pump(seconds=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        _app().processEvents()
        time.sleep(0.01)


def _config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path),
        "output_root": str(tmp_path / "out"),
        "flagged_root": "",
        "division_code": "003",
        "confidence_threshold": 85,
        "company_confidence_threshold": 90,
        "page_index": 0,
        "earliest_year": 1950,
        "ocr_engine": "tesseract",
        "theme": "Light Mode",
        "enable_qc": False,
        "enable_docsep_removal": False,
        "enable_blank_removal": False,
        "qc_rotation_enabled": False,
        "rename_enabled": False,
        "document_date_enabled": False,
        "audit_enabled": False,
        "render_dpi": 100,
        "max_workers": 1,
        "batch_size": 50,
        "review_window_high": 50,
        "review_window_low": 10,
        "output_layout": "flat",
    }


def _review_with_pdf(tmp_path: Path, pages: int = 4):
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((50, 80), f"TEST PAGE {index + 1}", fontsize=14)
        for line in range(8):
            page.insert_text((50, 130 + line * 30), f"Line {line + 1} body text", fontsize=10)
    doc.save(str(pdf))
    doc.close()

    tab = ReviewTab(_config(tmp_path))
    entry = {
        "original_path": str(pdf),
        "original_filename": pdf.name,
        "division_code": "003",
        "company_name": "TEST",
        "confidence": 100,
        "blank_pages": [],
        "docsep_pages": [],
    }
    tab.auto_confirmed_docs = [entry]
    tab.switch_tab("auto_confirmed")
    tab.active_index = 0
    tab.load_document(entry)
    tab.render_preview()
    _pump(0.3)
    tab._render_visible_pages()
    _pump(0.1)
    return tab, entry


def test_rotate_refreshes_one_label_and_uses_lightweight_undo(tmp_path, monkeypatch):
    _app()
    tab, _ = _review_with_pdf(tmp_path)
    before_ids = [id(lbl) for lbl in tab.page_labels]
    monkeypatch.setattr(tab, "render_preview", lambda *a, **k: (_ for _ in ()).throw(AssertionError("full preview rebuilt")))

    tab._rotate_page(0, 90)
    _pump(0.2)

    assert tab.current_pdf_doc[0].rotation == 90
    assert len(tab.page_labels) == len(before_ids)
    assert id(tab.page_labels[0]) != before_ids[0]
    assert [id(lbl) for lbl in tab.page_labels[1:]] == before_ids[1:]
    assert tab.undo_stack[-1]["kind"] == "rotation"
    assert "pdf" not in tab.undo_stack[-1]

    tab._undo()
    _pump(0.2)
    assert tab.current_pdf_doc[0].rotation == 0


def test_mark_blank_refreshes_badge_without_clearing_cache(tmp_path, monkeypatch):
    _app()
    tab, entry = _review_with_pdf(tmp_path)
    before_ids = [id(lbl) for lbl in tab.page_labels]
    cache_keys = set(tab._pixmap_cache.keys())
    monkeypatch.setattr(tab, "render_preview", lambda *a, **k: (_ for _ in ()).throw(AssertionError("full preview rebuilt")))

    tab._mark_blank(0)
    _pump(0.2)

    assert entry["blank_pages"] == [0]
    assert id(tab.page_labels[0]) != before_ids[0]
    assert [id(lbl) for lbl in tab.page_labels[1:]] == before_ids[1:]
    assert set(tab._pixmap_cache.keys()) == cache_keys
    badges = [lbl.text() for lbl in tab.page_labels[0].findChildren(QLabel)]
    assert "BLANK" in badges
    assert "1 blank" in tab.page_label.text()
    assert tab.undo_stack[-1]["kind"] == "marks"

    tab._undo()
    _pump(0.2)
    assert entry["blank_pages"] == []
    badges = [lbl.text() for lbl in tab.page_labels[0].findChildren(QLabel)]
    assert "BLANK" not in badges
