import pytest
from pathlib import Path
from ocr_box_cache import get_page_word_boxes, compute_file_hash

def test_compute_file_hash_returns_string(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("sample content", encoding="utf-8")
    h = compute_file_hash(str(f))
    assert isinstance(h, str) and len(h) > 0

def test_get_page_word_boxes_digital(tmp_path):
    import fitz
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "PHILIPPINE BRITISH ASSURANCE COMPANY INC")
    doc.save(str(pdf_path))
    doc.close()

    boxes = get_page_word_boxes(str(pdf_path), page_num=0, use_cache=False)
    assert len(boxes) >= 4
    assert any("BRITISH" in b["text"] for b in boxes)
    assert "bbox" in boxes[0] and len(boxes[0]["bbox"]) == 4
    assert boxes[0]["is_digital"] is True
