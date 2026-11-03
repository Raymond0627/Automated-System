import pytest
from pathlib import Path
import fitz
from PyQt6.QtWidgets import QApplication
from ui.review_tab import ReviewTab
from ocr_box_cache import compute_file_hash, clear_disk_cache, _FILE_HASH_CACHE, get_page_word_boxes
from date_extractor import extract_document_date

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_file_hash_cache_in_memory(tmp_path):
    clear_disk_cache()
    sample = tmp_path / "test_hash.pdf"
    sample.write_text("Hello World for Hash Cache", encoding="utf-8")
    
    h1 = compute_file_hash(str(sample))
    assert str(sample.resolve()) in _FILE_HASH_CACHE or str(sample) in _FILE_HASH_CACHE
    
    # Second call uses in-memory cached value
    h2 = compute_file_hash(str(sample))
    assert h1 == h2
    
    clear_disk_cache()
    assert len(_FILE_HASH_CACHE) == 0

def test_get_page_word_boxes_allow_ocr_flag(tmp_path):
    pdf_path = tmp_path / "test_digital.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "SAMPLE COMPANY LTD")
    doc.save(str(pdf_path))
    doc.close()

    boxes = get_page_word_boxes(str(pdf_path), page_num=0, use_cache=True, allow_ocr=False)
    assert len(boxes) >= 3
    assert any("SAMPLE" in b["text"] for b in boxes)

def test_review_tab_pixmap_lru_cache(qapp, tmp_path):
    pdf_path = tmp_path / "test_pixmap.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 100), "Page 1 Content")
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 100), "Page 2 Content")
    doc.save(str(pdf_path))
    doc.close()

    tab = ReviewTab(config={"theme": "Dark"})
    tab.load_document({"original_path": str(pdf_path), "original_filename": "test_pixmap.pdf"})
    
    # First render populates cache
    pm1 = tab._make_page_pixmap(0, 400)
    assert (0, tab.zoom_level, 400) in tab._pixmap_cache
    
    # Second render returns exact cached instance without re-rendering
    pm2 = tab._make_page_pixmap(0, 400)
    assert pm1 is pm2

    # Clear cache works
    tab._clear_pixmap_cache()
    assert len(tab._pixmap_cache) == 0

def test_date_extractor_digital_fast_path(tmp_path):
    pdf_path = tmp_path / "digital_doc.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The company has issued a payment date on 2023-05-15 with ample text here.")
    doc.save(str(pdf_path))
    doc.close()

    # Must process fast and not mark as blank
    res = extract_document_date(str(pdf_path), page_index=0, skip_ocr=True)
    assert 0 not in (res.blank_pages or [])
    assert res.all_blank is False
