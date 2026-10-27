"""Persistent original -> finalized mapping for the output folder.

The manifest is the reliable way to answer "which input PDFs are already
done?" without guessing from renamed filenames.  One row is appended for every
document finalized by ``pipeline.finalize_single_document``.
"""

import csv
import hashlib
import os
import threading
from pathlib import Path

MANIFEST_FILENAME = "rename_manifest.csv"
FIELDS = [
    "timestamp",
    "original_path",
    "original_sha256",
    "original_size",
    "output_path",
    "output_sha256",
    "final_filename",
    "division_code",
    "company_name",
    "document_date",
]

_LOCK = threading.Lock()


def manifest_path(output_root) -> Path:
    return Path(output_root) / MANIFEST_FILENAME


def sha256_file(path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def append_manifest_entry(output_root, entry: dict) -> Path:
    """Append one finalized document to ``rename_manifest.csv``.

    The header is written once.  A failed manifest write must never abort a
    finalize, so callers are expected to wrap this, but this function itself
    keeps the write as small and safe as possible.
    """
    path = manifest_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {name: entry.get(name, "") for name in FIELDS}
    with _LOCK:
        write_header = not path.exists() or path.stat().st_size == 0
        with open(str(path), "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    return path


def load_manifest(output_root):
    """Return manifest rows, ignoring a missing or unreadable manifest."""
    path = manifest_path(output_root)
    if not path.exists():
        return []
    try:
        with open(str(path), "r", newline="", encoding="utf-8") as f:
            return [dict(row) for row in csv.DictReader(f)]
    except (OSError, csv.Error):
        return []
