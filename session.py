import os
import json
import base64
from pathlib import Path
from datetime import datetime

from paths import BASE_DIR

SESSION_DIR = BASE_DIR / "sessions"
SESSION_FILE = SESSION_DIR / "latest_session.json"
SESSION_TMP = SESSION_DIR / "latest_session.json.tmp"
SESSION_VERSION = 1

scan_info = None


def set_scan_info(info) -> None:
    global scan_info
    scan_info = info


def get_scan_info() -> dict:
    return scan_info


def clear_scan_info() -> None:
    global scan_info
    scan_info = None


def _b64(data):
    if data is None:
        return None
    return base64.b64encode(bytes(data)).decode("ascii")


def _unb64(text):
    if not text:
        return None
    return base64.b64decode(text.encode("ascii"))


def atomic_write(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(str(tmp), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def load_snapshot() -> dict:
    path = Path(SESSION_FILE)
    if not path.exists():
        return None
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            snap = json.load(f)
        if not isinstance(snap, dict) or not snap:
            return None
        return snap
    except Exception:
        return None


def discard_snapshot() -> None:
    clear_scan_info()
    try:
        Path(SESSION_FILE).unlink()
    except OSError:
        pass
    try:
        Path(SESSION_TMP).unlink()
    except OSError:
        pass


def _clean_doc(doc: dict, keep_bytes: bool) -> dict:
    clean = dict(doc)
    pb = clean.get("_pending_pdf_bytes")
    if pb is not None:
        if keep_bytes:
            clean["_pending_pdf_bytes"] = _b64(pb)
        else:
            clean.pop("_pending_pdf_bytes", None)
    return clean


def serialize(rt) -> dict:
    active_doc = rt._get_current_doc()
    active_doc_key = _path_key(active_doc) if active_doc is not None else ""

    active_bytes = None
    if (
        rt.document_modified
        and rt.current_pdf_doc is not None
        and rt.active_index >= 0
        and active_doc is not None
    ):
        active_bytes = rt.current_pdf_doc.tobytes(garbage=4, deflate=True)

    all_flagged = []
    for d in rt.all_flagged_docs:
        keep = bool(active_bytes) and _path_key(d) == active_doc_key
        if keep:
            d["_pending_pdf_bytes"] = active_bytes
        all_flagged.append(_clean_doc(d, keep))

    auto = [_clean_doc(d, True) for d in rt.auto_confirmed_docs]
    if rt.active_tab == "auto_confirmed" and active_bytes and active_doc_key:
        for d in auto:
            if _path_key(d) == active_doc_key:
                d["_pending_pdf_bytes"] = _b64(active_bytes)
                break

    snap = {
        "version": SESSION_VERSION,
        "saved_at": datetime.now().isoformat(),
        "active_tab": rt.active_tab,
        "active_index": rt.active_index,
        "view_mode": getattr(rt, "view_mode", "variable"),
        "zoom_level": getattr(rt, "zoom_level", 100),
        "all_flagged_docs": all_flagged,
        "auto_confirmed_docs": auto,
        "reviewed_active_index": rt.active_index if rt.active_tab == "reviewed" else -1,
        "scan": scan_info,
    }
    return snap


def _path_key(doc):
    if not doc:
        return ""
    p = doc.get("original_path", "") or doc.get("modified_path", "")
    return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""


def apply(rt, snap: dict) -> dict:
    all_flagged = []
    for d in snap.get("all_flagged_docs", []):
        clean = dict(d)
        pb = clean.get("_pending_pdf_bytes")
        if isinstance(pb, str):
            clean["_pending_pdf_bytes"] = _unb64(pb)
        all_flagged.append(clean)

    auto = []
    for d in snap.get("auto_confirmed_docs", []):
        clean = dict(d)
        pb = clean.get("_pending_pdf_bytes")
        if isinstance(pb, str):
            clean["_pending_pdf_bytes"] = _unb64(pb)
        auto.append(clean)

    rt.all_flagged_docs = all_flagged
    rt.pending_docs = [d for d in all_flagged if not d.get("reviewed")]
    rt.auto_confirmed_docs = auto
    rt.view_mode = snap.get("view_mode", "variable")
    rt.zoom_level = snap.get("zoom_level", 100)

    set_scan_info(snap.get("scan"))
    return {
        "active_tab": snap.get("active_tab", "pending"),
        "active_index": snap.get("active_index", -1),
        "reviewed_active_index": snap.get("reviewed_active_index", -1),
        "scan": snap.get("scan"),
    }