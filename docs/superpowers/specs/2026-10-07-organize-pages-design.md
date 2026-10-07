# Organize Pages — Cross-Document Page Drag

Date: 2026-10-07
Status: Approved (design)

## Purpose

Let a reviewer move pages between two PDFs. The motivating example: a page that
belongs in `1.pdf` is actually sitting in `2.pdf`. The user opens the two
documents side by side, drags pages between them, and saves the two reorganized
PDFs — all without risking the originals until they explicitly save, and with a
single in-dialog undo.

## Scope

- Applies to all three Review tabs: `pending`, `auto_confirmed`, `reviewed`.
- Triggered from the Review tab document context menu ("Organize Pages...").
- Operates on exactly **two** documents at a time.
- Local desktop application (PyQt6). There is no server; all "safety" is local
  and in-memory.

## Non-Goals (YAGNI)

- No server, permission checks, or file-version locking.
- No disk backup folder, no temp files, no log file.
- No renaming of documents; filenames are unchanged.
- No multi-level undo; a single in-dialog undo step only.
- No selection of a second PDF from outside the current tab.
- No changes to `pipeline.py` or the finalize/rename pipeline.

## Trigger & Flow

1. User right-clicks a document in the Review tab list.
2. A new **"Organize Pages..."** action appears in the context menu:
   - `_show_document_menu` (pending / auto_confirmed), and
   - `_show_reviewed_menu` (reviewed).
   - Shown for a single selected document. If exactly two documents are already
     selected, both are used directly and no picker is shown.
   - If the tab holds fewer than two documents, the action is not offered
     (there is nothing to pair with).
3. Choosing Organize opens a small **picker dialog** listing the *other*
   documents in the **same tab** (excluding the first). Selecting one opens the
   Organize window.
4. The first document becomes **Pane A**; the picked document becomes **Pane B**.

## UI — `ui/organize_dialog.py`

A self-contained `QDialog` (modal) built for this feature only. It does not
reuse or modify the Review tab's preview/sort internals (chosen to isolate risk
and keep the Review tab's state untouched).

- `QSplitter` (horizontal) with two equal panes.
- Each pane: a per-document header (filename + live page count) over a
  scrollable grid of page thumbnails.
- Thumbnails **lazy-render** as the viewport scrolls: a cheap placeholder is
  placed first and the real pixmap is generated when the page becomes visible.
  Each pane holds its own mapping from slot widget to `PageRef`.
- **Selection:** click selects one page; Ctrl/Shift extend a multi-selection
  within a pane.
- **Drag & drop:**
  - Drag a page (or a multi-selection) into the other pane to move it.
  - Drag within the same pane to reorder a page or a block.
  - Dragging the **last remaining page** out of a pane is blocked; the drop is
    refused and the page stays.
- Footer buttons: **Save**, **Undo Save (Ctrl+Z)**, **Cancel / Esc**.
- Header shows the two filenames so the user always knows which is which.

## Data Model

`PageRef` is the single source of truth for the working state:

- `source_doc_id` — identifies which source PDF the page comes from ("A" or "B").
- `src_page_index` — page index within that source PDF (0-based).
- `is_blank` — whether the page was marked blank in its source document.
- `is_docsep` — whether the page was marked as a DOCSEP page in its source.

Each pane holds an ordered `list[PageRef]`. Initial state:
- Pane A = one `PageRef` per page of document A, in order, flags from A's
  `blank_pages` / `docsep_pages`.
- Pane B likewise for document B.

All moves and reorders mutate only these two lists. No file on disk is touched
until Save. Each source `fitz` document is opened once and kept in memory for
thumbnailing; both panes rasterize from these opened sources.

### Marker remapping ("markers travel with the page")

The blank/DOCSEP flags are carried on each `PageRef`. On Save, each document's
`blank_pages` and `docsep_pages` are rebuilt from the pages that document now
holds (its `PageRef` list), independent of where the page originally came from.

## Save Behavior

### Pending / Auto-Confirmed (in-memory)

- Build new PDF bytes for each of the two documents from its `PageRef` list.
- Store the result in that document's `_pending_pdf_bytes`.
- Rebuild `blank_pages` / `docsep_pages` as described above.
- Leave both documents in the tab, marked as modified.
- **No disk write.**

### Reviewed (real files)

- Capture both originals' bytes into memory (this is the undo source).
- Build new PDF bytes for each document from its `PageRef` list.
- Write each new version **directly over its own original path**
  (truncate + write). No temp file, no backup folder, no log.
- Filenames are unchanged.
- Update the in-memory reviewed document records if the tab is refreshed.

## Undo

- **In-dialog, single step.** The pre-Save state of both documents is held in
  memory (bytes for Reviewed; prior doc state/`_pending_pdf_bytes` for
  Pending/Auto-Confirmed).
- `Ctrl+Z` or the **Undo Save** button restores both documents to that state.
- **Cancel / Esc** discards all pending changes; nothing on disk changes.
- After the dialog closes, the save is final — no further undo from the main
  window.

## Error Handling

- If a Reviewed source file has disappeared or cannot be read, show an error
  and make no changes.
- If building the new PDF bytes fails, report the error and leave the
  originals untouched.
- For the Reviewed direct write: build the new bytes **fully in memory first**;
  only start overwriting a file once its new bytes are ready, so a build failure
  never leaves a partially written file.
- No-op Save (no pages moved) simply closes without writing.

## Components & Boundaries

- `ui/organize_dialog.py` — the dialog, picker, `PageRef` model, thumbnail
  rendering, drag/drop, Save/Undo. Owns all feature state.
- `ui/review_tab.py` — minimal integration:
  - two context-menu entries,
  - the same-tab picker,
  - a helper to obtain a document's readable source (bytes or file path),
  - a helper to apply the dialog's result back into the tab's document list and
    trigger the existing save (`_save_flagged_updates` /
    `_save_auto_confirmed_updates` / reviewed rescan).
- A small pure helper for "build PDF bytes from an ordered list of
  `(source pdf handle, page index)` pairs" (unit-testable without Qt).

## Testing

- Unit tests (`test_organize.py`):
  - `PageRef` list operations: move single page, move multiple, reorder,
    block-last-page.
  - Build-PDF-from-refs against generated temp PDFs; verify page count and
    content identity.
  - Marker remap: blank/DOCSEP flags follow moved pages; rebuilt lists correct.
  - Reviewed path: direct overwrite then Undo restores exact original bytes.
  - Pending/Auto path: commit sets `_pending_pdf_bytes` and rebuilt markers; no
    disk file created.
- Manual smoke test: right-click a reviewed doc → Organize → pick second →
  drag a page across → Save → confirm contents; Ctrl+Z restores; Cancel makes no
  change.

## Open Questions

None. All design decisions were confirmed during brainstorming.