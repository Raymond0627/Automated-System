"""Stable document identity and isolated cloning.

Documents used to be identified by ``original_path``.  A logical duplicate
shares that path with its original (the file is copied with a ``(n)`` suffix
only at finalize time), so every path-keyed lookup conflated the two and edits
leaked between them.  These helpers give each document a stable ``doc_id`` and
make a duplicate an independent copy of the document state.
"""

import copy
import hashlib
import os
import uuid


def new_doc_id() -> str:
    return uuid.uuid4().hex


def _seed(doc) -> str:
    path = doc.get("original_path") or doc.get("modified_path") or ""
    if path:
        path = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    name = doc.get("original_filename", "") or ""
    return f"{path}|{name}"


def ensure_doc_id(doc, prefix: str = "doc:") -> str:
    """Return the document's id, assigning a stable one when missing.

    The id is derived from the source path *and* the filename so two logical
    duplicates (same path, ``f.pdf`` vs ``f(1).pdf``) get different ids.  It is
    deterministic, so old indexes that predate ``doc_id`` backfill consistently.
    """
    existing = doc.get("doc_id")
    if existing:
        return existing
    seed = _seed(doc)
    if seed and seed != "|":
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
        doc["doc_id"] = f"{prefix}{digest}"
    else:
        doc["doc_id"] = new_doc_id()
    return doc["doc_id"]


def doc_key(doc) -> str:
    """Identity used for persistence lookups. Falls back to the legacy path key."""
    if not doc:
        return ""
    key = doc.get("doc_id")
    if key:
        return key
    path = doc.get("original_path", "") or doc.get("modified_path", "")
    name = doc.get("original_filename", "") or ""
    if path:
        path = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    return f"{path}|{name}"


def clone_document(doc) -> dict:
    """Deep-copy a document for duplication and give it a fresh identity.

    Mutable fields (``blank_pages``, ``docsep_pages``, ``qc`` ...) become
    independent objects; immutable ``bytes`` are shared, so a 186 MB pending
    buffer is not duplicated in memory.
    """
    clone = copy.deepcopy(doc)
    clone["doc_id"] = new_doc_id()
    return clone
