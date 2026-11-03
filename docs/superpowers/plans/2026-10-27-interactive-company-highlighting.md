# Interactive OCR Company Selection and Highlighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users in the Review tab to see detected company names highlighted as clickable bounding boxes on PDF preview pages (with confidence color badges 1-4), click any highlighted or unhighlighted line to instantly populate the company input, and run background OCR with disk caching and hotkeys.

**Architecture:** 
- A dedicated background OCR caching and box detector module (`ocr_box_cache.py`) that checks PyMuPDF digital text first, runs prioritized Tesseract OCR on scanned pages in a `QThreadPool` worker, crops to header regions, and caches word boxes on disk.
- A candidate ranking engine (`candidate_scorer.py`) that merges bounding box words into candidate lines/phrases, scores them by vertical position, font box height, uppercase/corporate keywords, and roster match, and assigns ranking (1-4).
- An interactive preview widget (`InteractivePageLabel` in `ui/widgets.py` or `ui/review_tab.py`) that renders overlay badges/rectangles, handles single-click mapping to words/lines, emits company selection, and supports keyboard hotkeys (1-4, Enter, Ctrl+Z).

**Tech Stack:** Python 3.14+, PyMuPDF (`fitz`), PyQt6, `pytesseract`, `rapidfuzz`, `pytest`, `pytest-qt`.

**Spec:** In-chat approved architecture covering:
1. Cheap OCR (PyMuPDF words first, background QThreadPool, zone crop top 35-40%, disk cache by file hash + page)
2. Smarter Candidates (font height from bbox, position weighting, roster fuzzy match, high confidence auto-fill)
3. Speedy workflow (1-4 hotkeys, Enter to accept, Tab to advance, undo history, learn on click/confirm)
4. Responsive UI (cached pixmap scaling, responsive overlay paintEvent, coordinate click hit testing)

## Global Constraints
- Do not break existing date extraction, manual QC, or company resolution fallback.
- Python 3.14 compatible.
- All file paths use `paths.DATA_DIR` for cache files.
- OCR cache directory: `paths.DATA_DIR / "ocr_cache"`.

## Review Focus
- Empty/blank pages should return empty candidates without crashing.
- Documents without embedded text and without Tesseract available should degrade gracefully to plain clicks.
- Zooming preview or changing window size must accurately scale click coordinates to PDF box coordinates.
- Multi-page documents: page 1 confirmed company persists as default for subsequent pages.
- Clicking an unhighlighted line with punctuation or noise strips excess whitespace and punctuation cleanly.

---

### Task 1: OCR Word Box Caching and Extraction (`ocr_box_cache.py`)

**Files:**
- Create: `ocr_box_cache.py`
- Test: `test_ocr_box_cache.py`

**Interfaces:**
- Produces:
  - `get_page_word_boxes(doc_path: str, page_num: int, use_cache: bool = True) -> List[Dict[str, Any]]`
    Returns list of dicts: `[{"text": str, "bbox": [x0, y0, x1, y1], "conf": float, "is_digital": bool}]`
  - `clear_disk_cache()`
  - `compute_file_hash(file_path: str) -> str`

- [ ] **Step 1: Write the failing test**

```python
# test_ocr_box_cache.py
import pytest
from pathlib import Path
from ocr_box_cache import get_page_word_boxes, compute_file_hash

def test_compute_file_hash_returns_string(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("sample content")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_ocr_box_cache.py -v`
Expected: FAIL with ModuleNotFoundError: No module named 'ocr_box_cache'

- [ ] **Step 3: Implement `ocr_box_cache.py`**

Implement `compute_file_hash`, disk cache check/save in `DATA_DIR / "ocr_cache" / f"{h}_{page_num}.json"`, PyMuPDF `page.get_text("words")` extraction first, and Tesseract `image_to_data` fallback when words are empty.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test_ocr_box_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ocr_box_cache.py test_ocr_box_cache.py
git commit -m "feat: add ocr word box extraction and caching module"
```

---

### Task 2: Candidate Ranking & Line Aggregator (`candidate_scorer.py`)

**Files:**
- Create: `candidate_scorer.py`
- Test: `test_candidate_scorer.py`

**Interfaces:**
- Consumes: Word boxes from `ocr_box_cache.py`
- Produces:
  - `extract_company_candidates(word_boxes: List[Dict[str, Any]], page_rect: tuple, roster: List[str] = None) -> List[Dict[str, Any]]`
    Returns top candidates sorted by score:
    `[{"text": "PHILIPPINE BRITISH ASSURANCE CORP", "bbox": [x0, y0, x1, y1], "score": 92.5, "badge": 1, "is_high_conf": True}]`
  - `find_line_at_point(point: tuple, word_boxes: List[Dict[str, Any]]) -> Optional[str]`

- [ ] **Step 1: Write the failing test**

```python
# test_candidate_scorer.py
import pytest
from candidate_scorer import extract_company_candidates, find_line_at_point

def test_extract_company_candidates_scores_header_keywords():
    words = [
        {"text": "PHILIPPINE", "bbox": [50, 60, 150, 75], "conf": 95},
        {"text": "BRITISH", "bbox": [155, 60, 220, 75], "conf": 95},
        {"text": "ASSURANCE", "bbox": [225, 60, 320, 75], "conf": 95},
        {"text": "CORP", "bbox": [325, 60, 370, 75], "conf": 95},
        {"text": "Case", "bbox": [50, 300, 90, 312], "conf": 90},
        {"text": "No.", "bbox": [95, 300, 115, 312], "conf": 90},
    ]
    candidates = extract_company_candidates(words, page_rect=(0, 0, 612, 792), roster=["PHILIPPINE BRITISH ASSURANCE CORP"])
    assert len(candidates) > 0
    top = candidates[0]
    assert "ASSURANCE" in top["text"]
    assert top["badge"] == 1
    assert top["is_high_conf"] is True

def test_find_line_at_point():
    words = [
        {"text": "HELLO", "bbox": [10, 10, 50, 25], "conf": 90},
        {"text": "WORLD", "bbox": [55, 10, 100, 25], "conf": 90},
    ]
    line = find_line_at_point((30, 15), words)
    assert line == "HELLO WORLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_candidate_scorer.py -v`
Expected: FAIL with ModuleNotFoundError: No module named 'candidate_scorer'

- [ ] **Step 3: Implement `candidate_scorer.py`**

Group adjacent words on identical/similar vertical lines into phrase chunks. Score lines by vertical position ($y < 0.40$ of page height), font size (box height), presence of corporate keywords (`company_extractor.STRONG_KEYWORDS_RE`), and fuzzy match against roster entries (`company_extractor.load_known_companies`). Provide line hit-test helper.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test_candidate_scorer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add candidate_scorer.py test_candidate_scorer.py
git commit -m "feat: add company candidate scoring and hit testing"
```

---

### Task 3: Interactive Page Label Overlay Widget (`ui/interactive_preview.py`)

**Files:**
- Create: `ui/interactive_preview.py`
- Test: `test_interactive_preview.py`

**Interfaces:**
- Produces:
  - `class InteractivePageLabel(ClickableLabel)`
    - Signals:
      - `company_selected(str)`
    - Methods:
      - `set_candidates(candidates: List[dict], word_boxes: List[dict], page_size: tuple)`
      - `select_candidate_by_badge(badge_num: int)`
      - `paintEvent(event)`: draws bounding box outlines, badge tags (1 to 4), and colors (green for auto-fill/rank 1, cyan/yellow for alternates).
      - `mousePressEvent(event)`: hit-tests candidate bboxes or falls back to `find_line_at_point` and emits `company_selected`.

- [ ] **Step 1: Write the failing test**

```python
# test_interactive_preview.py
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPoint
from ui.interactive_preview import InteractivePageLabel

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_interactive_page_label_candidate_badge(qapp):
    lbl = InteractivePageLabel(page_num=0)
    selected = []
    lbl.company_selected.connect(selected.append)
    candidates = [
        {"text": "PIONEER INSURANCE", "bbox": [50, 50, 200, 70], "score": 90, "badge": 1, "is_high_conf": True}
    ]
    lbl.set_candidates(candidates, word_boxes=[], page_size=(612, 792))
    lbl.select_candidate_by_badge(1)
    assert selected == ["PIONEER INSURANCE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_interactive_preview.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `InteractivePageLabel` in `ui/interactive_preview.py`**

Subclass `ClickableLabel`. Implement `set_candidates`, bounding box scaling against current `lbl.width()` and `lbl.height()`, custom `paintEvent` with `QPainter`, badge numbers, and click detection mapping cursor position to PDF rects.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test_interactive_preview.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/interactive_preview.py test_interactive_preview.py
git commit -m "feat: add interactive page label with candidate overlay and click selection"
```

---

### Task 4: Integration into ReviewTab & Keyboard Shortcuts (`ui/review_tab.py`)

**Files:**
- Modify: `ui/review_tab.py`
- Test: `test_review_tab_candidate_flow.py`

**Interfaces:**
- Wire `InteractivePageLabel` into `ReviewTab._create_page_label`.
- Run background worker (`QThreadPool`) to fetch OCR boxes & candidates upon document selection.
- Connect `company_selected(str)` to `self.company_input.setText(...)`.
- Add hotkeys: keys `1` to `4` select candidate badges, `Enter` confirms, `Ctrl+Z` undos company text.

- [ ] **Step 1: Write the failing test**

```python
# test_review_tab_candidate_flow.py
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_review_tab_undo_and_shortcuts(qapp, tmp_path):
    # Verify undo stack and candidate selection on review tab
    tab = ReviewTab(config={"theme": "Dark"})
    tab.company_input.setText("INITIAL CO")
    tab._set_company_with_undo("NEW CO")
    assert tab.company_input.text() == "NEW CO"
    tab._undo_company()
    assert tab.company_input.text() == "INITIAL CO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test_review_tab_candidate_flow.py -v`
Expected: FAIL

- [ ] **Step 3: Implement wiring in `ui/review_tab.py`**

Replace `ClickableLabel` creation with `InteractivePageLabel`. Implement `_set_company_with_undo` and `_undo_company`. Connect key events (1-4, Enter, Ctrl+Z). Start background task on `_load_document_preview` to populate page candidates.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test_review_tab_candidate_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/review_tab.py test_review_tab_candidate_flow.py
git commit -m "feat: integrate interactive candidates and hotkeys into review tab"
```

---

### Task 5: End-to-End Regression & Verification

**Files:**
- Test: `test_company_resolver.py`, `test_ocr_box_cache.py`, `test_candidate_scorer.py`, `test_interactive_preview.py`, `test_review_tab_candidate_flow.py`

- [ ] **Step 1: Run all test suites**

Run: `pytest test_company_resolver.py test_ocr_box_cache.py test_candidate_scorer.py test_interactive_preview.py test_review_tab_candidate_flow.py -v`
Expected: All tests PASS.

- [ ] **Step 2: Run application syntax and import sanity check**

Run: `python -m py_compile ocr_box_cache.py candidate_scorer.py ui/interactive_preview.py ui/review_tab.py`
Expected: Exit code 0.

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore: complete verification of interactive ocr company highlighting feature"
```
