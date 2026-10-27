"""Compare an input folder against an output folder and list what is left.

Output PDFs are renamed, so filenames cannot be compared directly.  This tool
uses, in order:

1. the saved app session (exact pending / reviewed paths),
2. ``rename_manifest.csv`` written next to the output (exact SHA-256 mapping),
3. page-text fingerprints (handles renamed, page-trimmed, cropped or rotated
   copies of the same document),
4. page-image fingerprints for scans with no text layer (handles renamed,
   page-trimmed copies even when QC re-saved the PDF).

It never changes the input or output folders.  Staging and session repair are
opt-in flags.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from collections import Counter, defaultdict

import manifest as manifest_store

SKIP_DIRS = {"passed", "failed", "processed", "flagged", "logs", "data", "output"}
FINAL_NAME_RE = re.compile(r"^(?:\d{6})?\d{4}_[^_]+_.+\.pdf$", re.IGNORECASE)

STATUS_DONE_SESSION = "DONE_SESSION"
STATUS_DONE_MANIFEST = "DONE_MANIFEST"
STATUS_DONE_TEXT_MATCH = "DONE_TEXT_MATCH"
STATUS_DONE_IMAGE_MATCH = "DONE_IMAGE_MATCH"
STATUS_SHARED_OUTPUT = "SHARED_OUTPUT"
STATUS_REMAINING_PENDING = "REMAINING_PENDING"
STATUS_REMAINING_AUTO = "REMAINING_AUTO"
STATUS_REMAINING_RAW_COPY = "REMAINING_RAW_COPY"
STATUS_REMAINING_NO_OUTPUT = "REMAINING_NO_OUTPUT"
STATUS_REMAINING_MANIFEST_MISSING_OUTPUT = "REMAINING_MANIFEST_MISSING_OUTPUT"
STATUS_BORDERLINE = "BORDERLINE"
STATUS_UNREADABLE = "UNREADABLE"

REMAINING_STATUSES = {
    STATUS_REMAINING_PENDING,
    STATUS_REMAINING_AUTO,
    STATUS_REMAINING_RAW_COPY,
    STATUS_REMAINING_NO_OUTPUT,
    STATUS_REMAINING_MANIFEST_MISSING_OUTPUT,
    STATUS_BORDERLINE,
    STATUS_UNREADABLE,
}

# Page-image fingerprint settings.  Scanned PDFs often have no text layer, and
# QC edits remove pages without changing the remaining page images, so a
# perceptual hash of each rendered page identifies renamed output documents.
IMAGE_DPI = 50
IMAGE_SIZE = 16
IMAGE_MAX_DISTANCE = 10
IMAGE_BLANK_INK = 0.006
IMAGE_COVERAGE_THRESHOLD = 0.5
IMAGE_AMBIGUITY_MARGIN = 0.01


def _norm(path) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(str(path)))


def is_finalized_name(name: str) -> bool:
    return bool(FINAL_NAME_RE.match(Path(name).name))


@dataclass
class Row:
    status: str
    input_path: str
    rel_path: str
    output_match: str = ""
    score: float = 0.0
    reason: str = ""
    sha256: str = ""
    matched_output_pages: set = field(default_factory=set)


@dataclass
class OutputRow:
    status: str
    path: str
    rel_path: str
    name: str
    sha256: str = ""
    matched_inputs: list = field(default_factory=list)
    reason: str = ""


@dataclass
class Report:
    input_root: str
    output_root: str
    rows: list
    outputs: list
    session_path: str = ""

    @property
    def remaining(self):
        return sorted(
            (row.input_path for row in self.rows if row.status in REMAINING_STATUSES),
            key=lambda p: os.path.normcase(p),
        )

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)

    @property
    def done_count(self) -> int:
        return sum(1 for row in self.rows if row.status.startswith("DONE_") or row.status == STATUS_SHARED_OUTPUT)


def plan_for_pipeline(report: Report):
    """Return ``(plan, existing_paths)`` for a fresh selective pipeline run.

    ``plan`` is every input path and ``existing_paths`` is the subset already
    finalized, matching what ``PipelineThread(plan=..., existing_paths=...)``
    expects for a resume.
    """
    plan = [row.input_path for row in report.rows]
    existing = {
        row.input_path
        for row in report.rows
        if row.status not in REMAINING_STATUSES
    }
    return plan, existing


def _walk_pdfs(root):
    root = Path(root)
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith(".") and name.lower() not in SKIP_DIRS
        ]
        for name in filenames:
            if name.lower().endswith(".pdf"):
                yield Path(dirpath) / name


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _file_sha256(path) -> str:
    digest = hashlib.sha256()
    try:
        with open(str(path), "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _page_tokens(path):
    """Return a frozenset of meaningful words for each page.

    Raises on unreadable/encrypted PDFs so the caller can report it.
    """
    import fitz

    doc = fitz.open(str(path))
    try:
        pages = []
        for page in doc:
            text = page.get_text("text") or ""
            tokens = re.findall(r"[a-z0-9]{4,}", text.lower())
            pages.append(frozenset(tokens))
        return pages
    finally:
        doc.close()


def _text_ok(pages) -> bool:
    return any(len(page) >= 3 for page in pages)


def _jaccard(a, b) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if not intersection:
        return 0.0
    return intersection / len(a | b)


def _score_pages(input_pages, output_pages, page_threshold: float):
    """Return (document_score, coverage, comparable_page_count)."""
    comparable = [page for page in input_pages if len(page) >= 3]
    if not comparable or not output_pages:
        return 0.0, 0.0, 0

    best_scores = []
    for page in comparable:
        best = max((_jaccard(page, other) for other in output_pages if other), default=0.0)
        best_scores.append(best)

    coverage = sum(1 for score in best_scores if score >= page_threshold) / len(best_scores)
    keep = max(1, int(math.ceil(len(best_scores) * 0.9)))
    document_score = sum(sorted(best_scores)[-keep:]) / keep
    return document_score, coverage, len(best_scores)


def _image_fingerprint(page):
    """Return (dHash, ink_ratio) for one rendered PDF page.

    The perceptual hash ignores small resampling differences, so a page still
    matches after QC re-saves the document.  ``ink_ratio`` lets blank backs be
    ignored: they are common to many documents and cannot identify one.
    """
    import fitz
    import numpy as np
    from PIL import Image

    pix = page.get_pixmap(dpi=IMAGE_DPI, colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    ink = float((gray < 200).mean())
    small = Image.fromarray(gray).resize((IMAGE_SIZE + 1, IMAGE_SIZE))
    values = np.asarray(small, dtype=np.int16)
    bits = (values[:, 1:] > values[:, :-1]).flatten()
    fingerprint = 0
    for bit in bits:
        fingerprint = (fingerprint << 1) | int(bit)
    return fingerprint, ink


def _image_pages(path):
    """Return page image fingerprints for a PDF; raises if it is unreadable."""
    import fitz

    doc = fitz.open(str(path))
    try:
        return [_image_fingerprint(page) for page in doc]
    finally:
        doc.close()


def _score_image_pages(input_pages, output_pages):
    """Return (score, coverage, matched_page_count) for two page-image lists."""
    input_hashes = [h for h, ink in input_pages if ink >= IMAGE_BLANK_INK]
    output_hashes = [h for h, ink in output_pages if ink >= IMAGE_BLANK_INK]
    if not input_hashes or not output_hashes:
        return 0.0, 0.0, 0

    distances = [
        min((a ^ b).bit_count() for b in output_hashes)
        for a in input_hashes
    ]
    matched = sum(1 for distance in distances if distance <= IMAGE_MAX_DISTANCE)
    coverage = matched / len(distances)
    score = sum(1.0 - distance / (IMAGE_SIZE * IMAGE_SIZE) for distance in distances) / len(distances)
    return score, coverage, matched


def _matched_page_indices(input_pages, output_pages, page_threshold):
    indices = set()
    for page in input_pages:
        if len(page) < 3:
            continue
        best_score, best_index = 0.0, None
        for index, other in enumerate(output_pages):
            if not other:
                continue
            score = _jaccard(page, other)
            if score > best_score:
                best_score, best_index = score, index
        if best_index is not None and best_score >= page_threshold:
            indices.add(best_index)
    return indices


def _matched_image_indices(input_pages, output_pages):
    input_hashes = [h for h, ink in input_pages if ink >= IMAGE_BLANK_INK]
    output_hashes = [
        (h, index)
        for index, (h, ink) in enumerate(output_pages)
        if ink >= IMAGE_BLANK_INK
    ]
    if not input_hashes or not output_hashes:
        return set()

    indices = set()
    for fingerprint in input_hashes:
        distance, index = min(
            ((fingerprint ^ h).bit_count(), index) for h, index in output_hashes
        )
        if distance <= IMAGE_MAX_DISTANCE:
            indices.add(index)
    return indices


def _load_output_images(outputs, indices):
    for index in indices:
        out = outputs[index]
        if out.image_pages is not None:
            continue
        try:
            out.image_pages = _image_pages(out.path)
        except Exception as exc:
            out.image_pages = []
            out.error = out.error or f"unreadable: {exc}"


def _best_image_match(input_pages, outputs, indices):
    """Return ("match"|"ambiguous", best, second) or None.

    ``best`` is (coverage, score, matched_pages, output_index).
    """
    scored = []
    for index in indices:
        out = outputs[index]
        if not out.image_pages:
            continue
        score, coverage, matched = _score_image_pages(input_pages, out.image_pages)
        if matched and coverage >= IMAGE_COVERAGE_THRESHOLD:
            scored.append((coverage, score, matched, index))
    if not scored:
        return None

    scored.sort(reverse=True)
    best = scored[0]
    if len(scored) > 1:
        second = scored[1]
        if best[0] == second[0] and abs(best[1] - second[1]) <= IMAGE_AMBIGUITY_MARGIN:
            return "ambiguous", best, second
    return "match", best, None


@dataclass
class _OutputDoc:
    path: Path
    rel_path: str
    name: str
    sha256: str
    is_final: bool
    pages: list
    text_ok: bool
    image_pages: list = None
    error: str = ""


@dataclass
class _InputDoc:
    path: Path
    rel_path: str
    sha256: str
    pages: list = None
    text_ok: bool = True
    image_pages: list = None
    error: str = ""


def _session_sets(snapshot):
    pending, auto, reviewed = set(), set(), set()
    if not snapshot:
        return pending, auto, reviewed, set()

    for doc in snapshot.get("all_flagged_docs") or []:
        key = _norm(doc.get("original_path"))
        if not key:
            continue
        (reviewed if doc.get("reviewed") else pending).add(key)

    for doc in snapshot.get("auto_confirmed_docs") or []:
        key = _norm(doc.get("original_path"))
        if not key:
            continue
        (reviewed if doc.get("reviewed") else auto).add(key)

    scan = snapshot.get("scan") or {}
    plan = [_norm(path) for path in (scan.get("plan") or []) if path]
    processed = {_norm(path) for path in (scan.get("processed") or []) if path}
    scan_remaining = {path for path in plan if path not in processed}
    return pending, auto, reviewed, scan_remaining


def _manifest_index(entries, output_hashes=None, output_names=None):
    """Return manifest lookups.

    A manifest row still counts when its recorded output was renamed or moved:
    the output SHA-256 or final filename can identify it in the current output
    tree.  Hashes are stored as lists so duplicate input copies can be counted.
    """
    output_hashes = output_hashes or set()
    output_names = output_names or set()
    valid_hash = defaultdict(list)
    valid_path = {}
    missing_hash = defaultdict(list)
    missing_path = {}

    for entry in entries or []:
        output_path = entry.get("output_path", "")
        output_sha = entry.get("output_sha256", "")
        final_name = entry.get("final_filename", "")
        digest = entry.get("original_sha256", "")
        key = _norm(entry.get("original_path"))
        exists = (
            (bool(output_path) and Path(output_path).exists())
            or (bool(output_sha) and output_sha in output_hashes)
            or (bool(final_name) and final_name in output_names)
        )
        if digest:
            (valid_hash if exists else missing_hash)[digest].append(entry)
        if key:
            (valid_path if exists else missing_path)[key] = entry
    return valid_hash, valid_path, missing_hash, missing_path


def _candidate_indices(input_pages, final_indices, df, postings):
    """Narrow comparisons using rare-word postings, falling back to all finals."""
    if len(final_indices) <= 40:
        return set(final_indices)

    candidates = set()
    for page in input_pages:
        if len(page) < 3:
            continue
        rare = sorted(page, key=lambda token: df.get(token, 10 ** 9))[:8]
        for token in rare:
            candidates.update(postings.get(token, ()))
    if not candidates:
        return set(final_indices)
    return candidates


def build_report(
    input_root,
    output_root,
    session_snapshot=None,
    session_path: str = "",
    manifest_entries=None,
    threshold: float = 0.85,
    page_threshold: float = 0.75,
    coverage_threshold: float = 0.8,
) -> Report:
    input_root = Path(input_root)
    output_root = Path(output_root)

    if manifest_entries is None:
        manifest_entries = manifest_store.load_manifest(output_root)

    inputs = []
    for path in _walk_pdfs(input_root):
        inputs.append(_InputDoc(path=path, rel_path=_relative(path, input_root), sha256=_file_sha256(path)))

    input_hashes = {doc.sha256 for doc in inputs if doc.sha256}
    input_hash_counts = Counter(doc.sha256 for doc in inputs if doc.sha256)

    outputs = []
    for path in _walk_pdfs(output_root):
        digest = _file_sha256(path)
        final = is_finalized_name(path.name)
        pages, text_ok, error = [], False, ""
        if final:
            try:
                pages = _page_tokens(path)
                text_ok = _text_ok(pages)
                if not text_ok:
                    error = "no usable text layer"
            except Exception as exc:
                error = f"unreadable: {exc}"
        outputs.append(
            _OutputDoc(
                path=path,
                rel_path=_relative(path, output_root),
                name=path.name,
                sha256=digest,
                is_final=final,
                pages=pages,
                text_ok=text_ok,
                error=error,
            )
        )

    pending_paths, auto_paths, reviewed_paths, scan_remaining = _session_sets(session_snapshot)
    output_hashes = {out.sha256 for out in outputs if out.sha256}
    output_names = {out.name for out in outputs}
    manifest_valid, manifest_valid_path, manifest_missing_hash, manifest_missing_path = _manifest_index(
        manifest_entries, output_hashes=output_hashes, output_names=output_names
    )
    raw_output_hashes = {out.sha256 for out in outputs if out.sha256 and not out.is_final}

    final_indices = [i for i, out in enumerate(outputs) if out.is_final and out.text_ok]
    image_indices = [i for i, out in enumerate(outputs) if out.is_final]
    df, postings = {}, {}
    for index in final_indices:
        tokens = set()
        for page in outputs[index].pages:
            tokens.update(page)
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    # Only keep postings for words that are not in most documents; a company
    # letterhead is common and would make every pair a candidate.
    max_df = max(20, len(final_indices) // 5)
    for index in final_indices:
        tokens = set()
        for page in outputs[index].pages:
            tokens.update(page)
        for token in tokens:
            if df.get(token, 0) <= max_df:
                postings.setdefault(token, set()).add(index)

    rows = []
    matches_by_output = {}

    for doc in inputs:
        key = _norm(doc.path)
        row = Row(status="", input_path=str(doc.path), rel_path=doc.rel_path, sha256=doc.sha256)

        if key in reviewed_paths:
            row.status, row.reason = STATUS_DONE_SESSION, "marked reviewed in saved session"
        else:
            entry = manifest_valid_path.get(key)
            if entry is None and doc.sha256:
                hash_entries = manifest_valid.get(doc.sha256, [])
                # Only trust a hash when the manifest accounts for every input
                # copy that shares it; otherwise one duplicate would hide the
                # other.
                if hash_entries and len(hash_entries) >= input_hash_counts[doc.sha256]:
                    entry = hash_entries[0]
            if entry is not None:
                row.status = STATUS_DONE_MANIFEST
                row.output_match = entry.get("final_filename") or entry.get("output_path", "")
                row.score = 1.0
                row.reason = "original SHA-256 is in rename_manifest.csv"
            elif key in pending_paths or key in scan_remaining:
                row.status = STATUS_REMAINING_PENDING
                row.reason = (
                    "in saved session pending list"
                    if key in pending_paths
                    else "in saved scan plan but not marked processed"
                )
            elif key in auto_paths:
                row.status = STATUS_REMAINING_AUTO
                row.reason = "auto-confirmed in session but not finalized"
            elif (doc.sha256 and doc.sha256 in manifest_missing_hash) or key in manifest_missing_path:
                row.status = STATUS_REMAINING_MANIFEST_MISSING_OUTPUT
                row.reason = "manifest entry exists but its output file is missing"
            else:
                try:
                    doc.pages = _page_tokens(doc.path)
                    doc.text_ok = _text_ok(doc.pages)
                    best_index, best_score, best_coverage = None, 0.0, 0.0
                    if doc.text_ok:
                        candidates = _candidate_indices(
                            doc.pages, final_indices, df, postings
                        )
                        for index in candidates:
                            out = outputs[index]
                            score, coverage, _ = _score_pages(doc.pages, out.pages, page_threshold)
                            if (coverage, score) > (best_coverage, best_score):
                                best_index, best_score, best_coverage = index, score, coverage

                    if (
                        best_index is not None
                        and best_coverage >= coverage_threshold
                        and best_score >= threshold
                    ):
                        row.status = STATUS_DONE_TEXT_MATCH
                        row.output_match = outputs[best_index].name
                        row.score = best_score
                        row.reason = f"page fingerprint match ({best_coverage:.0%} of pages)"
                        row.matched_output_pages = _matched_page_indices(
                            doc.pages, outputs[best_index].pages, page_threshold
                        )
                        matches_by_output.setdefault(str(outputs[best_index].path), []).append(row)
                    else:
                        try:
                            doc.image_pages = _image_pages(doc.path)
                        except Exception:
                            doc.image_pages = []
                        _load_output_images(outputs, image_indices)
                        image_result = _best_image_match(doc.image_pages, outputs, image_indices)

                        if image_result and image_result[0] == "match":
                            _, (coverage, score, matched, index), _ = image_result
                            row.status = STATUS_DONE_IMAGE_MATCH
                            row.output_match = outputs[index].name
                            row.score = score
                            row.reason = (
                                f"page image fingerprint match "
                                f"({coverage:.0%} of pages, {matched} page(s))"
                            )
                            row.matched_output_pages = _matched_image_indices(
                                doc.image_pages, outputs[index].image_pages
                            )
                            matches_by_output.setdefault(str(outputs[index].path), []).append(row)
                        elif image_result and image_result[0] == "ambiguous":
                            _, best, _ = image_result
                            row.status = STATUS_BORDERLINE
                            row.output_match = outputs[best[3]].name
                            row.score = best[1]
                            row.reason = "ambiguous page image match; verify before scanning"
                        elif doc.sha256 and doc.sha256 in raw_output_hashes:
                            row.status = STATUS_REMAINING_RAW_COPY
                            row.score = best_score
                            row.reason = "output contains only an unrenamed copy of this file"
                        elif best_score >= 0.60:
                            row.status = STATUS_BORDERLINE
                            row.output_match = outputs[best_index].name if best_index is not None else ""
                            row.score = best_score
                            row.reason = "weak match; verify before scanning"
                        elif not doc.text_ok and not doc.image_pages:
                            row.status = STATUS_UNREADABLE
                            row.reason = "no usable text layer or page images; cannot compare safely"
                        else:
                            row.status = STATUS_REMAINING_NO_OUTPUT
                            row.score = best_score
                            row.reason = "no finalized output found"
                except Exception as exc:
                    row.status = STATUS_UNREADABLE
                    row.reason = f"unreadable: {exc}"

        rows.append(row)

    for matched in matches_by_output.values():
        if len(matched) <= 1:
            continue

        # A genuine merge has inputs that cover different pages of the output.
        # An input that only re-matches pages another input already covers is a
        # duplicate/template page, not a second processed document.
        ordered = sorted(
            matched,
            key=lambda row: (row.score, len(row.matched_output_pages)),
            reverse=True,
        )
        kept, duplicates = [], []
        covered = set()
        for row in ordered:
            if not row.matched_output_pages or row.matched_output_pages - covered:
                kept.append(row)
                covered.update(row.matched_output_pages)
            else:
                duplicates.append(row)

        if len(kept) > 1:
            for row in kept:
                row.status = STATUS_SHARED_OUTPUT
                row.reason = (
                    f"{len(kept)} inputs map to different pages of the same "
                    "output; likely merged"
                )
        for row in duplicates:
            row.status = STATUS_BORDERLINE
            row.reason = (
                "matches the same pages as another input; likely a duplicate "
                "page or shared template"
            )

    output_rows = []
    for out in outputs:
        row = OutputRow(
            status="",
            path=str(out.path),
            rel_path=out.rel_path,
            name=out.name,
            sha256=out.sha256,
            reason=out.error,
        )
        matched = [
            m
            for m in matches_by_output.get(str(out.path), [])
            if m.status.startswith("DONE_") or m.status == STATUS_SHARED_OUTPUT
        ]
        row.matched_inputs = [m.input_path for m in matched]
        if out.is_final:
            if not matched:
                row.status = "ORPHAN_OUTPUT"
                row.reason = row.reason or "no input matched this finalized file"
            elif len(matched) > 1:
                row.status = "SHARED_OUTPUT"
            else:
                row.status = "MATCHED"
        else:
            if out.sha256 and out.sha256 in input_hashes:
                row.status = "RAW_COPY"
                row.reason = "byte-identical to an input PDF; not finalized"
            else:
                row.status = "ORPHAN_OUTPUT"
                row.reason = row.reason or "unrecognized file in output"
        output_rows.append(row)

    return Report(
        input_root=str(input_root),
        output_root=str(output_root),
        rows=rows,
        outputs=output_rows,
        session_path=session_path,
    )


def load_session_snapshot(path):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        return snapshot if isinstance(snapshot, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _atomic_write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(str(tmp), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def repair_session(
    session_path,
    remaining,
    all_input_paths=None,
    done_paths=None,
    mode: str = "reprocess",
):
    """Back up and patch a saved session so the app resumes only what is left.

    ``reprocess`` clears the old pending list and asks the app to rescan the
    remaining paths.  ``review`` keeps the pending docs in memory and marks the
    done paths as processed, so the app only restores them for review.
    """
    if mode not in ("reprocess", "review"):
        raise ValueError("mode must be 'reprocess' or 'review'")

    session_path = Path(session_path)
    snapshot = load_session_snapshot(session_path)
    if snapshot is None:
        raise ValueError(f"Not a readable session file: {session_path}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = session_path.with_name(f"{session_path.stem}.backup-{stamp}{session_path.suffix}")
    counter = 1
    while backup.exists():
        backup = session_path.with_name(
            f"{session_path.stem}.backup-{stamp}-{counter}{session_path.suffix}"
        )
        counter += 1
    shutil.copy2(session_path, backup)

    remaining_paths = [str(path) for path in remaining]
    if mode == "reprocess":
        snapshot["all_flagged_docs"] = []
        snapshot["auto_confirmed_docs"] = []
        snapshot["active_tab"] = "pending"
        snapshot["active_index"] = -1
        snapshot["reviewed_active_index"] = -1
        snapshot["scan"] = {"plan": remaining_paths, "processed": []}
    else:
        snapshot["scan"] = {
            "plan": [str(path) for path in (all_input_paths or remaining_paths)],
            "processed": [str(path) for path in (done_paths or [])],
        }

    snapshot["saved_at"] = datetime.now().isoformat()
    _atomic_write_json(session_path, snapshot)
    return backup


def stage_remaining(report, input_root, output_root, stage_dir, division_code: str = ""):
    """Create a staging input tree containing only the remaining PDFs."""
    input_root = Path(input_root)
    stage_dir = Path(stage_dir)
    batch_root = stage_dir / input_root.name
    batch_root.mkdir(parents=True, exist_ok=True)

    staged = []
    for source in report.remaining:
        source_path = Path(source)
        try:
            relative = source_path.relative_to(input_root)
        except ValueError:
            relative = Path(source_path.name)
        destination = batch_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        try:
            os.link(str(source_path), str(destination))
        except OSError:
            shutil.copy2(str(source_path), str(destination))
        staged.append(destination)

    instructions = [
        "Lumeed QScan - remaining documents",
        "=" * 40,
        "",
        f"Files staged: {len(staged)}",
        "",
        "Set these in the Dashboard:",
        f"  Input:  {batch_root}",
        f"  Output: {output_root}",
    ]
    if division_code:
        instructions.append(f"  Division code: {division_code}")
    instructions.extend(
        [
            "",
            "Run the pipeline, review as usual, then delete this staging folder.",
            "The input and output folders were not modified by this tool.",
            "",
            "Files:",
        ]
    )
    for path in staged:
        instructions.append(f"  {path.relative_to(batch_root)}")
    (stage_dir / "RUN_INSTRUCTIONS.txt").write_text("\n".join(instructions) + "\n", encoding="utf-8")
    return staged


def _row_payload(row):
    data = dict(row.__dict__)
    data["matched_output_pages"] = sorted(data.get("matched_output_pages") or [])
    return data


def write_reports(report: Report, report_dir):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    csv_path = report_dir / "remaining_pdfs.csv"
    with open(str(csv_path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "input_file", "rel_path", "output_match", "score", "reason"])
        for row in report.rows:
            writer.writerow([
                row.status,
                Path(row.input_path).name,
                row.rel_path,
                row.output_match,
                f"{row.score:.3f}" if row.score else "",
                row.reason,
            ])

    output_csv = report_dir / "output_files.csv"
    with open(str(output_csv), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "output_file", "rel_path", "matched_inputs", "reason"])
        for row in report.outputs:
            writer.writerow([
                row.status,
                row.name,
                row.rel_path,
                "; ".join(Path(p).name for p in row.matched_inputs),
                row.reason,
            ])

    txt_path = report_dir / "remaining_pdfs.txt"
    lines = [
        "Lumeed QScan - remaining documents report",
        "=" * 48,
        f"Input:  {report.input_root}",
        f"Output: {report.output_root}",
        f"Session: {report.session_path or '(not used)'}",
        "",
        f"Input PDFs: {len(report.rows)}",
        f"Done: {report.done_count}",
        f"Remaining: {len(report.remaining)}",
        f"Shared/merged: {report.count(STATUS_SHARED_OUTPUT)}",
        f"Orphan outputs: {sum(1 for o in report.outputs if o.status == 'ORPHAN_OUTPUT')}",
        "",
    ]

    sections = [
        ("REMAINING - scan these", REMAINING_STATUSES),
        ("SHARED / MERGED - done, verify", {STATUS_SHARED_OUTPUT}),
        ("DONE", {STATUS_DONE_SESSION, STATUS_DONE_MANIFEST, STATUS_DONE_TEXT_MATCH, STATUS_DONE_IMAGE_MATCH}),
    ]
    for title, statuses in sections:
        lines.append(f"--- {title} ---")
        selected = [row for row in report.rows if row.status in statuses]
        if not selected:
            lines.append("  (none)")
        for row in selected:
            suffix = f" -> {row.output_match}" if row.output_match else ""
            score = f" [{row.score:.3f}]" if row.score else ""
            lines.append(f"  {row.status:<34} {row.rel_path}{suffix}{score}  ({row.reason})")
        lines.append("")

    lines.append("--- ORPHAN OUTPUTS - no input matched ---")
    orphans = [out for out in report.outputs if out.status == "ORPHAN_OUTPUT"]
    if not orphans:
        lines.append("  (none)")
    for out in orphans:
        lines.append(f"  {out.rel_path}  ({out.reason})")
    lines.append("")

    lines.append("--- RAW COPIES - present in output but not finalized ---")
    raw = [out for out in report.outputs if out.status == "RAW_COPY"]
    if not raw:
        lines.append("  (none)")
    for out in raw:
        lines.append(f"  {out.rel_path}  ({out.reason})")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, txt_path


def _default_session_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "LumeedQScan" / "sessions" / "latest_session.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="List input PDFs that have no finalized counterpart in the output folder."
    )
    parser.add_argument("--input", help="Input folder (default: saved session dashboard)")
    parser.add_argument("--output", help="Output folder (default: saved session dashboard)")
    parser.add_argument("--session", default=None, help="Path to latest_session.json")
    parser.add_argument("--report-dir", default="resume_reports", help="Where to write reports")
    parser.add_argument("--stage", help="Create a staging input folder with only remaining PDFs")
    parser.add_argument(
        "--repair-session",
        choices=["none", "reprocess", "review"],
        default="none",
        help="Patch the saved session so the app resumes only remaining PDFs",
    )
    parser.add_argument("--division-code", default="", help="Division code shown in staging instructions")
    parser.add_argument("--threshold", type=float, default=0.85, help="Document match score (0-1)")
    parser.add_argument("--page-threshold", type=float, default=0.75, help="Page match score (0-1)")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parser.parse_args(argv)

    session_path = Path(args.session) if args.session else _default_session_path()
    snapshot = load_session_snapshot(session_path)
    dashboard = (snapshot or {}).get("dashboard") or {}

    input_root = args.input or dashboard.get("input_root")
    output_root = args.output or dashboard.get("output_root")
    if not input_root:
        parser.error("--input is required (no input_root in the saved session)")
    if not output_root:
        parser.error("--output is required (no output_root in the saved session)")

    report = build_report(
        input_root,
        output_root,
        session_snapshot=snapshot,
        session_path=str(session_path) if session_path.exists() else "",
        threshold=args.threshold,
        page_threshold=args.page_threshold,
    )

    if args.json:
        print(json.dumps(
            {
                "input_root": report.input_root,
                "output_root": report.output_root,
                "remaining": report.remaining,
                "rows": [_row_payload(row) for row in report.rows],
                "outputs": [row.__dict__ for row in report.outputs],
            },
            indent=2,
            ensure_ascii=False,
        ))
    else:
        csv_path, txt_path = write_reports(report, args.report_dir)
        print(f"Input PDFs : {len(report.rows)}")
        print(f"Done       : {report.done_count}")
        print(f"Remaining  : {len(report.remaining)}")
        print(f"Shared     : {report.count(STATUS_SHARED_OUTPUT)}")
        print(f"Reports    : {csv_path}")
        print(f"             {txt_path}")
        if report.remaining:
            print("\nRemaining files:")
            for path in report.remaining:
                print(f"  {Path(path).name}")

    if args.stage:
        staged = stage_remaining(
            report,
            input_root,
            output_root,
            args.stage,
            division_code=args.division_code,
        )
        print(f"\nStaged {len(staged)} file(s) in {Path(args.stage) / Path(input_root).name}")
        print(f"Instructions: {Path(args.stage) / 'RUN_INSTRUCTIONS.txt'}")

    if args.repair_session != "none":
        all_paths = [str(row.input_path) for row in report.rows]
        done_paths = [
            row.input_path
            for row in report.rows
            if row.status in (STATUS_DONE_SESSION, STATUS_DONE_MANIFEST, STATUS_DONE_TEXT_MATCH, STATUS_DONE_IMAGE_MATCH, STATUS_SHARED_OUTPUT)
        ]
        backup = repair_session(
            session_path,
            report.remaining,
            all_input_paths=all_paths,
            done_paths=done_paths,
            mode=args.repair_session,
        )
        print(f"\nSession repaired ({args.repair_session}); backup: {backup}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
