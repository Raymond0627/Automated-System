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
