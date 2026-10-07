# Organize Pages (Cross-Document Page Drag) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user open two documents from the same Review tab side by side and drag pages between them (and reorder within a pane), with an in-memory-only working state and a single in-dialog Undo, then Save.

**Architecture:** A pure data module (`organize_model.py`) owns the page-reference model and PDF assembly. A self-contained modal `OrganizeDialog` (`ui/organize_dialog.py`) renders two lazy-thumbnail panes and edits only in-memory page-reference lists. `ReviewTab` gains two context-menu entries, a same-tab picker, and a controller method that opens the dialog and applies the result differently per tab (in-memory bytes for pending/auto-confirmed; direct file overwrite for reviewed).

**Tech Stack:** Python 3.14, PyQt6, PyMuPDF (`fitz`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-organize-pages-design.md`

## Global Constraints

- Local desktop PyQt6 app — **no server**, no permissions/version/lock checks.
- **No temporary files, no backup folder, no log file** for the reviewed save.
- Reviewed save **overwrites each original file directly** (truncate + write); filenames are unchanged.
- Pending/auto-confirmed save is **in-memory only** (`_pending_pdf_bytes`); no disk write.
- Moving the **last remaining page** out of a pane is **blocked**.
- blank/DOCSEP markers **travel with each page** and are rebuilt per document on Save.
- Undo is **single-step and in-dialog only**; once the dialog closes the save is final.
- Follow existing `ReviewTab` patterns and imports (PyQt6, `fitz`, `_null`/dict-based doc records).

## Review Focus

- Moving the last page out of a pane — must be **blocked**, source pane never emptied.
- blank/DOCSEP flags after a cross-document move — must travel with the page and be **re-indexed** to the destination document's new page numbers.
- Reviewed overwrite when a source file is missing or unreadable — must error **without partially writing** either file.
- Cancel/Esc with no Save — must leave **both files exactly as before**.
- Same-pane reorder drag — must not drop or duplicate pages (index adjustment after removal).

---

### Task 1: Pure page-arrangement model (`organize_model.py`)

**Files:**
- Create: `organize_model.py`
- Test: `test_organize.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PageRef` dataclass with fields `source_id: str`, `src_page_index: int`, `is_blank: bool`, `is_docsep: bool`.
  - `page_refs_for_source(source_id: str, page_count: int, blank_pages: list[int], docsep_pages: List[int]) -> list[PageRef]`
  - `markers_for(refs: list[PageRef]) -> tuple[list[int], list[int]]` returning `(blank_pages, docsep_pages)`.
  - `extract(refs: list[PageRef], positions: list[int]) -> tuple[list[PageRef], list[PageRef]]` returning `(remaining, moved)`.
  - `insert(refs: list[PageRef], items: list[PageRef], at: int) -> list[PageRef]`.
  - `apply_move(src_refs, dst_refs, positions, insert_at) -> tuple[list[PageRef], list[PageRef]] | None` (`None` = blocked because source would empty).
  - `build_pdf_bytes(sources: dict[str, fitz.Document], refs: list[PageRef]) -> bytes`.

- [ ] **Step 1: Write the failing tests**

Create `test_organize.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_organize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'organize_model'`.

- [ ] **Step 3: Implement `organize_model.py`**

```python
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import fitz


@dataclass
class PageRef:
    source_doc_id: str
    src_page_index: int
    is_blank: bool = False
    is_docsep: bool = False


def page_refs_for_source(source_doc_id, page_count, blank_pages=None, docsep_pages=None):
    blank_pages = set(blank_pages or [])
    docsep_pages = set(docsep_pages or [])
    return [
        PageRef(source_doc_id, i, i in blank_pages, i in docsep_pages)
        for i in range(page_count)
    ]


def markers_for(refs):
    blank_pages = [i for i, r in enumerate(refs) if r.is_blank]
    docsep_pages = [i for i, r in enumerate(refs) if r.is_docsep]
    return blank_pages, docsep_pages


def extract(refs, positions):
    pos = set(positions)
    remaining = [r for i, r in enumerate(refs) if i not in pos]
    moved = [refs[i] for i in sorted(pos) if 0 <= i < len(refs)]
    return remaining, moved


def insert(refs, items, at):
    at = max(0, min(at, len(refs)))
    return list(refs[:at]) + list(items) + list(refs[at:])


def apply_move(src_refs, dst_refs, positions, insert_at):
    same_pane = src_refs is dst_refs
    remaining, moved = extract(src_refs, positions)
    if not remaining:
        return None
    if same_pane:
        removed_before = sum(1 for p in set(positions) if p < insert_at)
        adjusted = insert_at - removed_before
        result = insert(remaining, moved, adjusted)
        return result, result
    return remaining, insert(dst_refs, moved, insert_at)


def build_pdf_bytes(sources, refs):
    out = fitz.open()
    try:
        for ref in refs:
            src = sources[ref.source_doc_id]
            out.insert_pdf(src, from_page=ref.src_page_index, to_page=ref.src_page_index)
        return out.tobytes(garbage=4, deflate=True)
    finally:
        out.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_organize.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add organize_model.py test_organize.py
git commit -m "feat: add organize page-reference model and PDF assembly"
```

---

### Task 2: `PagePane` thumbnail widget

**Files:**
- Create: `ui/organize_dialog.py`
- Test: `test_organize.py` (append)

**Interfaces:**
- Consumes: `organize_model.PageRef`, `organize_model.apply_move`, `organize_model.build_pdf_bytes` (Task 1); `ui.styles.get_palette_dict`, `THEME_DARK`.
- Produces:
  - `class PagePane(QWidget)` with:
    - `__init__(self, sources: dict[str, fitz.Document], refs: list[PageRef], title: str, config: dict, parent=None)`
    - attribute `refs: list[PageRef]` (current arrangement)
    - method `set_refs(self, refs: list[PageRef]) -> None` (re-render)
    - signal `pages_changed = pyqtSignal()`
    - method `selected_positions(self) -> list[int]`
    - lazy thumbnail rendering as the user scrolls.

- [ ] **Step 1: Write the failing test** (append to `test_organize.py`)

```python
def test_page_pane_refs_roundtrip():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.organize_dialog import PagePane

    pane = PagePane({"a": _pdf(3)}, page_refs_for_source("a", 3), "Doc A", {"theme": "Dark Mode"})
    assert len(pane.refs) == 3
    pane.set_refs(page_refs_for_source("a", 2))
    assert len(pane.refs) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_organize.py::test_page_pane_refs_roundtrip -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.organize_dialog'`.

- [ ] **Step 3: Implement `PagePane` in `ui/organize_dialog.py`**

Build a `QWidget` containing a `QLabel` title + `QScrollArea`. The scroll area holds a `QGridLayout` of `ClickableThumb` `QLabel` subclasses. Each thumb stores `_position` and the `PageRef`. Render placeholders immediately, then on scroll (`QScrollArea.verticalScrollBar().valueChanged` → `QTimer.singleShot(0, self._render_visible)`) rasterize visible pages with `page.get_pixmap(...)`. Selection: Ctrl/Shift toggles multi-select; `selected_positions()` returns sorted highlighted indices. Drag: on press over a thumb, start `QDrag` with `text/plain` = `"pane:<id>:<positions>"`; accept drops between panes via a module-level callback the dialog wires up. `set_refs` re-renders. Keep rasterization identical in spirit to `ReviewTab._make_page_pixmap` (DPI from config `render_dpi`, width-bounded).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_organize.py::test_page_pane_refs_roundtrip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/organize_dialog.py test_organize.py
git commit -m "feat: add PagePane lazy-thumbnail widget"
```

---

### Task 3: `OrganizeDialog` shell with Save / Undo / Cancel

**Files:**
- Modify: `ui/organize_dialog.py`
- Test: `test_organize.py` (append)

**Interfaces:**
- Consumes: `PagePane` (Task 2); `organize_model.apply_move`, `markers_for`, `build_pdf_bytes`.
- Produces:
  - `class OrganizeDialog(QDialog)` with:
    - `__init__(self, sources: dict[str, fitz.Document], refs_a: list[PageRef], refs_b: list[PageRef], title_a: str, title_b: str, commit_fn, config: dict, parent=None)`
    - `commit_fn(refs_a: list[PageRef], refs_b: list[PageRef]) -> callable` — applies the save and returns an `undo()` callable.
    - Save button → calls `commit_fn`, stores the returned undo callable, enables Undo Save.
    - `Undo Save` (and Ctrl+Z) → calls stored undo, resets both panes to initial refs.
    - Esc / Cancel closes without reverting an already-completed Save.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_organize_dialog_save_then_undo():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.organize_dialog import OrganizeDialog

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
    dlg._on_save()
    assert calls["commit"] == 1
    assert dlg._saved is True
    assert dlg._undo_fn is not None
    dlg._on_undo()
    assert calls["undo"] == 1
    assert dlg._saved is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_organize.py::test_organize_dialog_save_then_undo -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.organize_dialog'`.

- [ ] **Step 3: Implement `OrganizeDialog`**

`QDialog` with a horizontal `QSplitter` holding `self.pane_a` and `self.pane_b` (`PagePane`), a bottom button row (`Save`, `Undo Save`, `Cancel`), and `Ctrl+Z`/`Esc` shortcuts. Wire cross-pane drops: dropping pane A's selection onto pane B calls `apply_move(pane_a.refs, pane_b.refs, positions, insert_at)`; if it returns `None`, ignore (blocked). Re-render both panes after a move. `_on_save` calls `self._commit_fn(self.pane_a.refs, self.pane_b.refs)`, stores the returned callable in `self._undo_fn`, sets `self._saved = True`, disables Save, enables Undo. `_on_undo` calls `self._undo_fn()`, resets both panes to the initial refs snapshot, `self._saved = False`, swaps button enablement. `reject()` (Cancel/Esc) closes; it does not revert an already-completed save.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_organize.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add ui/organize_dialog.py test_organize.py
git commit -m "feat: add OrganizeDialog with save/undo/cancel"
```

---

### Task 4: ReviewTab wiring — context menus, same-tab picker, `_open_organizer`

**Files:**
- Modify: `ui/review_tab.py` (context menus at ~2709 and ~1012; new methods near `_merge_selected_documents`)
- Test: `test_organize.py` (append)

**Interfaces:**
- Consumes: `organize_model.page_refs_for_source`, `markers_for`, `build_pdf_bytes`; `OrganizeDialog`.
- Produces (on `ReviewTab`):
  - `_organize_source_doc(self, doc: dict) -> tuple[fitz.Document, str]` — opens the doc (prefers `_pending_pdf_bytes`, then `original_path`, then `flagged_copy_path`) and returns `(fitz_document, kind)` where `kind` is `"bytes"` or `"path"`.
  - `_apply_organize(self, tab: str, doc_a: dict, doc_b: dict, sources: dict, refs_a: list[PageRef], refs_b: list[PageRef]) -> Callable[[], None]` — builds both new PDFs, applies per-tab, returns an undo callable.
  - `_open_organizer(self, doc_a: dict, doc_b: dict) -> None` — builds refs, shows the dialog.

- [ ] **Step 1: Write the failing tests** (append to `test_organize.py`)

```python
def test_apply_organize_pending_is_in_memory(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from pathlib import Path
    from ui.review_tab import ReviewTab
    a = _pdf(2); b = _pdf(2)
    pa = tmp_path / "a.pdf"; pb = tmp_path / "b.pdf"
    a.save(str(pa)); b.save(str(pb))
    doc_a = {"original_path": str(pa), "original_filename": "a.pdf",
             "division_code": "155", "company_name": "Acme",
             "detected_date": "2024-05-01", "blank_pages": [], "docsep_pages": []}
    doc_b = {"original_path": str(pb), "original_filename": "b.pdf",
             "division_code": "155", "company_name": "Acme",
             "detected_date": "2024-05-01", "blank_pages": [0], "docsep_pages": []}
    rt = ReviewTab({"input_root": str(tmp_path), "output_root": str(tmp_path),
                    "flagged_root": "", "theme": "Dark Mode"})
    src_a = fitz.open(str(pa)); src_b = fitz.open(str(pb))
    refs_a = page_refs_for_source("a", 2)
    refs_b = page_refs_for_source("b", 2, blank_pages=[0])
    # move a's page 0 into b at position 0
    from organize_model import apply_move
    refs_a2, refs_b2 = apply_move(refs_a, refs_b, [0], 0)
    undo = rt._apply_organize("pending", doc_a, doc_b, {"a": src_a, "b": src_b}, refs_a2, refs_b2)
    assert "_pending_pdf_bytes" in doc_a and "_pending_pdf_bytes" in doc_b
    assert fitz.open("pdf", doc_a["_pending_pdf_bytes"]).page_count == 1
    assert fitz.open("pdf", doc_b["_pending_pdf_bytes"]).page_count == 3
    # blank marker traveled with b page 0 which is now index 1
    assert doc_b["blank_pages"] == [1]
    undo()
    assert "_pending_pdf_bytes" not in doc_a
    src_a.close(); src_b.close()


def test_apply_organize_reviewed_overwrites_and_undo_restores(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.review_tab import ReviewTab
    a = _pdf(2); b = _pdf(2)
    pa = tmp_path / "1.pdf"; pb = tmp_path / "2.pdf"
    a.save(str(pa)); b.save(str(pb))
    orig_a = pa.read_bytes(); orig_b = pb.read_bytes()
    doc_a = {"original_path": str(pa), "original_filename": "1.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    doc_b = {"original_path": str(pb), "original_filename": "2.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    rt = ReviewTab({"input_root": str(tmp_path), "output_root": str(tmp_path),
                    "flagged_root": "", "theme": "Dark Mode"})
    src_a = fitz.open(str(pa)); src_b = fitz.open(str(pb))
    refs_a = page_refs_for_source("a", 2); refs_b = page_refs_for_source("b", 2)
    from organize_model import apply_move
    refs_a2, refs_b2 = apply_move(refs_a, refs_b, [0], 0)
    undo = rt._apply_organize("reviewed", doc_a, doc_b, {"a": src_a, "b": src_b}, refs_a2, refs_b2)
    assert fitz.open(str(pa)).page_count == 1
    assert fitz.open(str(pb)).page_count == 3
    undo()
    assert pa.read_bytes() == orig_a
    assert pb.read_bytes() == orig_b
    src_a.close(); src_b.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test_organize.py -k apply_organize -v`
Expected: FAIL — `ReviewTab` has no `_apply_organize`.

- [ ] **Step 3: Implement wiring in `ui/review_tab.py`**

Add imports: `from organize_model import (PageRef, page_refs_for_source, apply_move, markers_for, build_pdf_bytes)` and lazily import `OrganizeDialog` inside `_open_organizer`.

`_organize_source_doc`: open via `_pending_pdf_bytes` (`fitz.open("pdf", bytes)` → kind "bytes"), else `original_path`, else `flagged_copy_path` (`fitz.open(path)` → kind "path").

`_apply_organize`:
- Build `new_a_bytes = build_pdf_bytes(sources, refs_a)`, `blank_a, docsep_a = markers_for(refs_a)`, likewise B.
- **pending/auto_confirmed:** snapshot the two docs' previous `_pending_pdf_bytes` (if any) and marker lists; set new values; mark `modified`; call `_save_flagged_updates()` (pending) or `_save_auto_confirmed_updates()` (auto). Undo restores the snapshot. (No `_pending_pdf_bytes` key existed originally → undo removes it.)
- **reviewed:** verify both `original_path`s exist and are readable **before any write**; raise if not. Read `orig_a = Path(path_a).read_bytes()` (same for B); build both new byte strings first; then write `new_a_bytes` to `path_a` and `new_b_bytes` to `path_b` via `open(path, "wb").write(...)` (truncate+write, no temp); update each doc dict's `blank_pages`/`docsep_pages`; call `_start_reviewed_scan()`. Undo re-writes `orig_a`/`orig_b` and restores the marker lists.
- Return the undo callable.

`_open_organizer(doc_a, doc_b)`:
- Get `(src_a, _) = self._organize_source_doc(doc_a)`, same for B (if either fails → `QMessageBox.warning` and return).
- `refs_a = page_refs_for_source("a", len(src_a), doc_a.get("blank_pages"), doc_a.get("docsep_pages"))`; likewise B.
- Create the dialog with `commit_fn=lambda ra, rb: self._apply_organize(self.active_tab, doc_a, doc_b, {"a": src_a, "b": src_b}, ra, rb)`; on close, close both `fitz` documents.

Context menus: in `_show_document_menu`, add an **"Organize Pages…"** action for the single-selection branch (`lambda: self._organize_from_selection(idx)`), and when `count == 2` add `"Organize These 2…"` calling `_organize_selected_pair()`. Do the same in `_show_reviewed_menu`. Add a same-tab picker `_pick_second_doc(self, docs, exclude_idx) -> dict | None` using `QInputDialog.getItem` (or a small `QDialog` list) over the other docs' `original_filename`s; `_open_organizer` is invoked with the two chosen docs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test_organize.py -k apply_organize -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest test_organize.py test_resume_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Add the missing-file guard test** (append to `test_organize.py`)

```python
def test_apply_organize_reviewed_missing_file_raises(tmp_path):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.review_tab import ReviewTab
    pa = tmp_path / "gone.pdf"
    doc_a = {"original_path": str(pa), "original_filename": "1.pdf",
             "division_code": "155", "company_name": "Acme", "blank_pages": [], "docsep_pages": []}
    doc_b = dict(doc_a, original_path=str(tmp_path / "b.pdf"))
    rt = ReviewTab({"input_root": str(tmp_path), "output_root": str(tmp_path),
                    "flagged_root": "", "theme": "Dark Mode"})
    import pytest
    with pytest.raises(Exception):
        rt._apply_organize("reviewed", doc_a, doc_b, {}, [], [])
```

Run: `python -m pytest test_organize.py -k missing_file -v`
Expected: PASS (the reviewed path raises before any write when a source path is absent).

- [ ] **Step 6: Commit**

```bash
git add ui/review_tab.py test_organize.py
git commit -m "feat: wire Organize Pages into ReviewTab context menus"
```

---

### Task 5: Same-tab picker dialog and end-to-end manual smoke test

**Files:**
- Modify: `ui/review_tab.py` (picker dialog)
- Manual verification

**Interfaces:**
- Consumes: `_open_organizer` (Task 4).
- Produces: a `_OrganizeSecondPicker(QDialog)` with a `QListWidget` of same-tab documents (label = `original_filename`, `UserRole` = doc index) and OK/Cancel.

- [ ] **Step 1: Implement `_OrganizeSecondPicker`** in `ui/review_tab.py` (module-level class near the other dialogs). `_open_organizer` is invoked from the context menu with the first doc; it opens the picker filtered to the other docs in the current tab; the chosen doc becomes Pane B.

- [ ] **Step 2: Manual smoke test (Reviewed tab)**

1. Launch the app, process a batch so the Reviewed tab has two files.
2. Right-click a file → **Organize Pages…** → pick the second file.
3. Confirm two panes load thumbnails lazily while scrolling.
4. Drag one page from pane A to pane B; verify counts change and thumbnails re-render.
5. Try to drag A's only remaining page → verify it is refused.
6. Click **Save**; verify on disk that file A got shorter and file B got the page, filenames unchanged.
7. Click **Undo Save**; verify both files are byte-identical to their originals.
8. Reopen, drag, click **Save**, then **Cancel/Esc**; verify the save persists and closing does not revert it.

- [ ] **Step 3: Manual smoke test (Pending/auto-confirmed)**

Repeat with a Pending doc pair; confirm nothing is written to disk and the two docs still appear (modified) in the tab.

- [ ] **Step 4: Commit**

```bash
git add ui/review_tab.py
git commit -m "feat: add same-tab document picker for Organize"
```

---

## Self-Review

- **Spec coverage:** trigger menus + same-tab picker (Task 4/5); modal two-pane dialog (Tasks 2–3); in-memory page-reference model (Task 1); markers travel (Task 1 + Task 4); per-tab save (Task 4); in-dialog single-step undo (Task 3/4); block last page (Task 1); no temp/backup/log (Task 4 reviewed path); filenames unchanged (Task 4). All covered.
- **Review Focus tests:** last-page block (Task 1 `test_apply_move_blocks_emptying_source`), marker remap (Task 1 `test_markers_for_rebuilds_indices` + Task 4 `test_apply_organize_pending_is_in_memory`), reviewed missing-file guard (Task 4 `test_apply_organize_reviewed_missing_file_raises`) and undo-restores-originals (Task 4 `test_apply_organize_reviewed_overwrites_and_undo_restores`), cancel-untouched (Task 5 manual step 8), same-pane reorder (Task 1 `test_apply_move_same_pane_reorder`).
- **Type consistency:** `PageRef` fields, `apply_move`/`extract`/`insert`/`markers_for`/`build_pdf_bytes` names and signatures are used consistently across tasks.
- **Proportion:** implementation bodies are described by signature and behavior; full code appears only for the pure model (Task 1) and tests.