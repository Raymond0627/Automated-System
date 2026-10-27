import os
import sys
import json
import time
import gc
import shutil
import tempfile
import numpy as np
import cv2
import fitz
from pathlib import Path
from typing import Dict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime

from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR
from date_extractor import extract_document_date, DateResult
from pipeline import PipelineConfig, parse_folder_structure, save_confirmed_documents
from auto_qc import run_qc_on_pdf, detect_docsep_flag
from company_extractor import get_company_name_for_filename
from .styles import get_palette_dict, THEME_DARK

import pytesseract


def _is_network_path(path: str) -> bool:
    """True for UNC shares and mapped network drives (best-effort, Windows)."""
    try:
        norm = os.path.abspath(path)
    except Exception:
        norm = path
    if norm.startswith("\\\\") or norm.startswith("//"):
        return True
    if os.name == "nt" and len(norm) >= 2 and norm[1] == ":":
        try:
            import ctypes

            drive = norm[:2] + "\\"
            DRIVE_REMOTE = 4
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_REMOTE
        except Exception:
            return False
    if norm.startswith("/"):
        try:
            return os.statvfs("/").f_flag & getattr(os, "ST_REMOTE", 0) != 0
        except Exception:
            return False
    return False


def _stage_local_copy(path: str, should_cancel=None):
    """Copy a network file to a local temp dir so PDF/OCR work runs on a fast
    local disk instead of crossing the network per page. Returns
    (work_path, temp_dir); temp_dir is None when no copy was made."""
    src = Path(path)
    if not _is_network_path(path):
        return path, None
    temp_dir = tempfile.mkdtemp(prefix="lumeedqscan_")
    work_path = Path(temp_dir) / src.name
    try:
        with open(src, "rb") as fsrc, open(work_path, "wb") as fdst:
            while True:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("cancelled while staging")
                chunk = fsrc.read(1024 * 1024)
                if not chunk:
                    break
                fdst.write(chunk)
    except Exception:
        _cleanup_staged(temp_dir)
        return path, None
    return str(work_path), temp_dir


def _cleanup_staged(temp_dir):
    if not temp_dir:
        return
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


def _process_doc_worker(doc_info: dict, config: dict, render_dpi: int) -> dict:
    """Process a single document in a worker thread. Returns result dict."""
    result = {
        "status": "error",
        "flagged_data": None,
        "passed_data": None,
        "log_messages": [],
        "blank_pages": [],
        "docsep_pages": [],
        "total_pages": 0,
    }

    ocr_timeout = config.get("ocr_timeout_sec", 120) or 0
    should_cancel = doc_info.get("_should_cancel")
    original_path = doc_info["original_path"]
    temp_dir = None
    _shared_doc = None
    started = time.time()
    try:
        work_path, temp_dir = _stage_local_copy(original_path, should_cancel=should_cancel)
        if temp_dir:
            result["log_messages"].append(f"[STAGE] {doc_info['original_filename']}: copied to local temp for processing")

        original_filename = doc_info["original_filename"]
        division_code = doc_info["division_code"]
        company_name = doc_info["company_name"]

        docsep_msg = ""
        try:
            _shared_doc = fitz.open(work_path)
        except Exception:
            pass

        stage_t0 = time.time()
        if config.get("enable_docsep_removal", True):
            try:
                ds_result = detect_docsep_flag(work_path, dpi=render_dpi, doc=_shared_doc)
                result["docsep_pages"] = ds_result.get("docsep_pages", [])
                if result["docsep_pages"]:
                    docsep_msg = f" | docsep:{len(result['docsep_pages'])}"
                    result["log_messages"].append(f"[DOCSEP] {original_filename}: separator page(s) detected at index {result['docsep_pages']}")
            except Exception:
                pass

        # When the date scan is off and the PDF already carries native text, the
        # per-page OCR pass is redundant (blank verification and company
        # matching can use the native text), so skip it.
        skip_ocr = False
        if not config.get("document_date_enabled", False):
            try:
                if _shared_doc is not None and len(_shared_doc) > 0:
                    probe_pages = min(len(_shared_doc), 3)
                    native_chars = sum(
                        len(_shared_doc[i].get_text().strip()) for i in range(probe_pages)
                    )
                    skip_ocr = native_chars >= 40
            except Exception:
                skip_ocr = False

        page_index = config.get("page_index", 0)
        include_date = config.get("document_date_enabled", False)
        date_result = extract_document_date(
            work_path,
            page_index,
            dpi=render_dpi,
            doc=_shared_doc,
            docsep_pages=result.get("docsep_pages", []),
            include_date=include_date,
            skip_ocr=skip_ocr,
            should_cancel=should_cancel,
            ocr_timeout=ocr_timeout,
            page_progress_callback=doc_info.get("_page_progress"),
        )
        result["blank_pages"] = list(date_result.blank_pages or [])
        result["log_messages"].append(
            f"[TIMING] {original_filename}: docsep+blank {time.time() - stage_t0:.1f}s"
        )

        total_pages = 0
        if result["blank_pages"]:
            pdf_for_ocr = None
            try:
                pdf_for_ocr = fitz.open(work_path) if _shared_doc is None else _shared_doc
                total_pages = len(pdf_for_ocr)
                for blank_pg in list(result["blank_pages"]):
                    if blank_pg >= total_pages:
                        continue
                    pg = pdf_for_ocr[blank_pg]
                    pix = pg.get_pixmap(dpi=render_dpi)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                    del pix
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    del img
                    text = pytesseract.image_to_string(gray, config='--psm 6', timeout=ocr_timeout).strip()
                    del gray
                    words = [w for w in text.split() if len(w) >= 2]
                    if len(words) >= 2:
                        result["blank_pages"].remove(blank_pg)
                        result["log_messages"].append(f"[BLANK-OCR] {original_filename} p{blank_pg+1}: {len(words)} words found - NOT blank")
                    else:
                        result["log_messages"].append(f"[BLANK-OCR] {original_filename} p{blank_pg+1}: {len(words)} word(s) - confirmed blank")
            except Exception as e:
                result["log_messages"].append(f"[BLANK-OCR-ERROR] {original_filename}: {e}")
            finally:
                if pdf_for_ocr and _shared_doc is None:
                    try:
                        pdf_for_ocr.close()
                    except Exception:
                        pass
        elif _shared_doc is not None:
            total_pages = len(_shared_doc)
        else:
            try:
                with fitz.open(work_path) as _tmp:
                    total_pages = len(_tmp)
            except Exception:
                total_pages = 0

        result["total_pages"] = total_pages

        qc_result = None
        rotation_enabled = bool(config.get("qc_rotation_enabled", False))
        if config.get("enable_qc", True) and not date_result.all_blank:
            if not rotation_enabled:
                # With the rotation check off, QC has nothing left to do that the
                # blank scan in extract_document_date has not already done. Skip
                # the extra page pass so huge documents are not rasterised twice.
                qc_result = {
                    "blank_detected": bool(result["blank_pages"]),
                    "blank_ink_ratio": 0.0,
                    "rotation_detected": "skipped",
                    "rotation_confidence": 0.0,
                    "mirrored_detected": False,
                    "mirror_delta": 0.0,
                    "mirror_needs_review": False,
                    "qc_status": "passed",
                    "qc_failure_reasons": "",
                    "pages_checked": 0,
                    "total_pages": total_pages,
                }
            else:
                try:
                    qc_t0 = time.time()
                    gpu_mode = config.get("gpu_mode", "cpu")
                    use_gpu = gpu_mode == "local_gpu"
                    if gpu_mode == "remote":
                        from auto_qc import set_remote_gpu_url
                        set_remote_gpu_url(config.get("remote_gpu_url", ""))
                    # Do not buffer every page image: QC renders each page itself
                    # and releases it immediately, so memory stays flat no matter
                    # how many pages the document has.
                    qc_result = _run_qc_on_pages(
                        [],
                        work_path=work_path,
                        blank_threshold=config.get("qc_blank_threshold", 1.5),
                        rotation_threshold=config.get("qc_rotation_threshold", 65),
                        dpi=render_dpi,
                        use_gpu=use_gpu,
                        gpu_mode=gpu_mode,
                        ocr_timeout=ocr_timeout,
                        should_cancel=should_cancel,
                        rotation_enabled=rotation_enabled,
                    )
                    result["log_messages"].append(
                        f"[TIMING] {original_filename}: qc {time.time() - qc_t0:.1f}s ({total_pages} pages)"
                    )
                except Exception:
                    pass

        qc_failed = qc_result and qc_result.get("qc_status") == "failed"
        qc_str = ""
        if qc_result:
            if qc_failed:
                reasons = qc_result.get("qc_failure_reasons", "unknown")
                qc_str = f" | QC:FAIL({reasons})"
            else:
                qc_str = " | QC:Passed"

        blank_str = f" | {len(result['blank_pages'])}/{total_pages} blank" if result["blank_pages"] else ""

        # Extract native PDF page texts for accurate company extraction
        try:
            if _shared_doc is not None:
                page_texts = [{"page_text": _shared_doc[i].get_text().strip(), "page_idx": i} for i in range(len(_shared_doc))]
            else:
                with fitz.open(work_path) as _d:
                    page_texts = [{"page_text": _d[i].get_text().strip(), "page_idx": i} for i in range(len(_d))]
        except Exception:
            page_texts = [{"page_text": t, "page_idx": i} for i, t in enumerate(date_result.page_texts)]

        company_result = get_company_name_for_filename(
            page_texts,
            context={"rel_dir": doc_info.get("rel_path", ""), "original_path": original_path},
        )
        final_company = company_result.get('company_name', '')
        company_confidence = company_result.get('company_confidence', 0)
        company_tier = company_result.get('tier_used', '')

        # Auto-Confirm Gate: Company >= threshold, QC passed, and (when the
        # Document date setting is on) a valid confident date.
        company_conf_threshold = int(config.get("company_confidence_threshold", 90) or 90)
        earliest_year = config.get("earliest_year", 1950)
        date_valid = bool(date_result.date and date_result.date.year >= earliest_year and date_result.date <= date.today())
        date_conf_pass = date_result.confidence >= 90
        company_conf_pass = company_confidence >= company_conf_threshold
        qc_passed = not qc_failed

        if not include_date:
            date_valid = date_conf_pass = True

        is_auto_confirmed = (not date_result.all_blank) and date_valid and date_conf_pass and company_conf_pass and qc_passed

        if is_auto_confirmed:
            result["status"] = "confirmed"
            result["passed_data"] = {
                "original_path": original_path,
                "division_code": division_code,
                "company_name": final_company,
                "company_confidence": company_confidence,
                "company_tier": company_tier,
                "original_filename": original_filename,
                "detected_date": date_result.date.isoformat() if date_result.date else "",
                "confidence": date_result.confidence,
                "method": date_result.method,
                "blank_pages": result["blank_pages"],
                "docsep_pages": result["docsep_pages"],
                "raw_ocr_text": date_result.raw_ocr_text,
                "company_source": company_tier,
                "resolved_letterhead": company_result.get("resolved_letterhead", ""),
            }
            if qc_result:
                result["passed_data"]["qc_status"] = qc_result.get("qc_status", "")
                result["passed_data"]["qc_failure_reasons"] = qc_result.get("qc_failure_reasons", "")
            result["log_messages"].append(f"[AUTO] {original_filename} -> {date_result.date or ''} ({date_result.confidence}%) | {final_company} ({company_confidence}%){blank_str}{qc_str}{docsep_msg}")
        else:
            result["status"] = "flagged"
            result["flagged_data"] = {
                "original_path": original_path,
                "division_code": division_code,
                "company_name": final_company,
                "company_confidence": company_confidence,
                "company_tier": company_tier,
                "original_filename": original_filename,
                "best_guess_date": date_result.date.isoformat() if date_result.date else None,
                "confidence": date_result.confidence,
                "method": date_result.method,
                "raw_ocr_text": date_result.raw_ocr_text,
                "blank_pages": result["blank_pages"],
                "docsep_pages": result["docsep_pages"],
                "all_blank": date_result.all_blank,
                "error": "Document is entirely blank" if date_result.all_blank else "",
                "company_source": company_tier,
                "company_needs_review": company_confidence < company_conf_threshold,
                "resolved_letterhead": company_result.get("resolved_letterhead", ""),
            }
            if qc_result:
                result["flagged_data"]["qc"] = qc_result

            if date_result.all_blank:
                result["log_messages"].append(f"[BLANK] {original_filename}: ALL BLANK ({total_pages} pages){qc_str}{docsep_msg}")
            elif not company_conf_pass:
                result["log_messages"].append(f"[FLAGGED-COMPANY] {original_filename} company='{final_company}' ({company_confidence}%) tier={company_tier}")
            else:
                result["log_messages"].append(f"[FLAGGED] {original_filename} ({date_result.confidence}%){blank_str}{qc_str}{docsep_msg}")
    except Exception as e:
        result["status"] = "error"
        result["log_messages"].append(f"[ERROR] {doc_info['original_filename']}: {e}")
    finally:
        if _shared_doc is not None:
            try:
                _shared_doc.close()
            except Exception:
                pass
        _cleanup_staged(temp_dir)
        result["log_messages"].append(
            f"[TIMING] {doc_info['original_filename']}: total {time.time() - started:.1f}s"
        )

    return result


def _run_qc_on_pages(
    page_images,
    work_path: str,
    blank_threshold: float,
    rotation_threshold: float,
    dpi: int,
    use_gpu: bool,
    gpu_mode: str,
    ocr_timeout: float,
    should_cancel=None,
    rotation_enabled: bool = True,
) -> dict:
    """Run QC over every page.

    ``page_images`` may be empty: pages not present in it are rendered here one
    at a time and released immediately, so memory stays flat regardless of the
    document length. Callers should pass an empty list for large documents.
    """
    import fitz
    from auto_qc import run_qc

    agg = {
        "blank_detected": False,
        "blank_ink_ratio": 0.0,
        "rotation_detected": "none",
        "rotation_confidence": 100.0,
        "mirrored_detected": False,
        "mirror_delta": 0.0,
        "mirror_needs_review": False,
        "qc_status": "passed",
        "qc_failure_reasons": "",
        "pages_checked": 0,
        "total_pages": 0,
    }

    images_by_idx = {idx: img for idx, img in page_images}
    total = 0
    try:
        with fitz.open(work_path) as doc:
            total = len(doc)
            for pn in range(total):
                if should_cancel is not None and should_cancel():
                    break
                img = images_by_idx.get(pn)
                if img is None:
                    page = doc[pn]
                    pix = page.get_pixmap(dpi=dpi)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    del pix
                agg["pages_checked"] += 1
                result = run_qc(
                    img,
                    blank_threshold,
                    rotation_threshold,
                    use_gpu=use_gpu,
                    gpu_mode=gpu_mode,
                    dpi=dpi,
                    ocr_timeout=ocr_timeout,
                    rotation_enabled=rotation_enabled,
                )
                del img
                if result["blank_detected"]:
                    agg["blank_detected"] = True
                agg["blank_ink_ratio"] = max(agg["blank_ink_ratio"], result["blank_ink_ratio"])
                if result["rotation_detected"] not in ("none", "skipped"):
                    agg["rotation_detected"] = result["rotation_detected"]
                    agg["rotation_confidence"] = min(agg["rotation_confidence"], result["rotation_confidence"])
                if result["qc_status"] != "passed":
                    agg["qc_status"] = result["qc_status"]
                if result["qc_failure_reasons"]:
                    agg["qc_failure_reasons"] = (
                        agg["qc_failure_reasons"] + "; " + result["qc_failure_reasons"]
                        if agg["qc_failure_reasons"]
                        else result["qc_failure_reasons"]
                    )
    except Exception:
        pass
    agg["total_pages"] = total
    return agg


def _json_safe_flagged(flagged):
    """Return a copy of flagged result docs with non-JSON-serializable values
    (e.g. '_pending_pdf_bytes' as raw bytes) removed so the merged flagged
    index can always be written without raising during json.dump."""
    safe = []
    for item in flagged:
        if not isinstance(item, dict):
            safe.append(item)
            continue
        copy = dict(item)
        copy.pop("_pending_pdf_bytes", None)
        safe.append(copy)
    return safe


def _flagged_key(doc: dict) -> str:
    path = doc.get("original_path") or doc.get("modified_path") or ""
    if path:
        path = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    name = doc.get("original_filename", "") or ""
    return f"{path}|{name}"


def _merge_flagged_docs(existing, new):
    """Merge resume results, replacing stale entries for the same source.

    A resumed document can already exist in the restored pending list.  New
    OCR/QC results must replace that entry instead of adding a duplicate.
    """
    merged = list(existing or [])
    positions = {_flagged_key(doc): index for index, doc in enumerate(merged)}
    for doc in new or []:
        key = _flagged_key(doc)
        if key in positions:
            merged[positions[key]] = doc
        else:
            positions[key] = len(merged)
            merged.append(doc)
    return merged


class PipelineThread(QThread):
    progress = pyqtSignal(str, int)
    page_progress = pyqtSignal(str, int, int)
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(list)
    error_signal = pyqtSignal(str)
    doc_processed = pyqtSignal(str, dict)
    scan_plan = pyqtSignal(list)
    doc_completed = pyqtSignal(str, str)

    def __init__(
        self,
        config: dict,
        max_files: int = 0,
        plan: list = None,
        existing_paths: set = None,
        merged_flagged: list = None,
        merged_confirmed: list = None,
    ):
        super().__init__()
        self.config = config
        self.max_files = max_files
        self._cancelled = False
        self.plan = plan
        self.existing_paths = set(existing_paths or ())
        self.merged_flagged = list(merged_flagged or [])
        self.merged_confirmed = list(merged_confirmed or [])

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit("Scanning folders...", 5)
            division_code = self.config.get("division_code", "")
            pipeline_config = PipelineConfig(
                input_root=self.config["input_root"],
                output_root=self.config["output_root"],
                flagged_root=self.config["flagged_root"],
                confidence_threshold=self.config["confidence_threshold"],
                page_index=self.config["page_index"],
                earliest_year=self.config["earliest_year"],
                ocr_engine=self.config.get("ocr_engine", "tesseract"),
                division_code=division_code,
            )
            pipeline_config.enable_docsep_removal = self.config.get("enable_docsep_removal", True)
            pipeline_config.enable_blank_removal = self.config.get("enable_blank_removal", True)
            pipeline_config.rename_enabled = self.config.get("rename_enabled", True)
            pipeline_config.document_date_enabled = self.config.get("document_date_enabled", False)
            pipeline_config.audit_enabled = self.config.get("audit_enabled", True)
            pipeline_config.qc_rotation_enabled = self.config.get("qc_rotation_enabled", False)
            pipeline_config.output_layout = self.config.get("output_layout", "flat")

            render_dpi = self.config.get("render_dpi", 200)
            pipeline_config.render_dpi = render_dpi
            max_workers = self.config.get("max_workers", 1)
            batch_size = self.config.get("batch_size", 50)

            flagged_root_str = self.config.get("flagged_root", "")
            flagged_root = Path(flagged_root_str) if flagged_root_str else None

            if self.plan is not None:
                remaining = [p for p in self.plan if p not in self.existing_paths]
                self.doc_completed.emit("plan", f"Resuming scan: {len(remaining)}/{len(self.plan)} remaining, {len(self.existing_paths)} already processed")
                doc_infos = []
                seen = set()
                for p in sorted(remaining):
                    norm = os.path.normcase(p)
                    c = os.path.normcase(self.config["input_root"])
                    if not norm.startswith(c):
                        c2 = os.path.normcase(os.path.dirname(c))
                        rel = os.path.relpath(p, c2)
                    else:
                        rel = os.path.relpath(p, c)
                    if rel.startswith(".."):
                        rel = os.path.basename(p)
                    doc_infos.append({
                        "original_path": p,
                        "original_filename": os.path.basename(p),
                        "division_code": division_code,
                        "company_name": "",
                        "rel_path": rel,
                    })
                total = len(doc_infos)
                self.log_message.emit(f"Resume scan: {total} document(s) remaining")
            else:
                if flagged_root:
                    flagged_root.mkdir(parents=True, exist_ok=True)
                    for f in ["flagged_index.json", "passed_index.json"]:
                        pp = flagged_root / f
                        if pp.exists():
                            pp.unlink()

                scan_stats = {"dirs": 0, "pdfs": 0, "last": 0.0}

                def scan_progress(dirs, pdfs, current):
                    now = time.time()
                    if now - scan_stats["last"] < 0.25:
                        return
                    scan_stats["last"] = now
                    self.progress.emit(
                        f"Scanning folders... {dirs} folders, {pdfs} PDFs found", 5
                    )

                batches = parse_folder_structure(
                    pipeline_config.input_root,
                    division_code,
                    progress_callback=scan_progress,
                    should_cancel=lambda: self._cancelled,
                )
                if self._cancelled:
                    self.log_message.emit("--- Pipeline Cancelled ---")
                    self.progress.emit("Cancelled", 0)
                    self.finished_signal.emit([])
                    return
                total = sum(len(b.documents) for b in batches)

                if self.max_files > 0:
                    count = 0
                    for batch in batches:
                        for doc in batch.documents:
                            if count >= self.max_files:
                                doc.status = "skipped"
                            count += 1
                    total = min(total, self.max_files)
                    self.log_message.emit(f"Found {total} PDFs (limited to {self.max_files})")
                else:
                    self.log_message.emit(f"Found {total} PDFs")

                doc_infos = []
                for batch in batches:
                    for doc in batch.documents:
                        if doc.status == "skipped":
                            continue
                        doc_infos.append({
                            "original_path": doc.original_path,
                            "original_filename": doc.original_filename,
                            "division_code": doc.division_code,
                            "company_name": doc.company_name,
                            "_batch": batch,
                            "_doc": doc,
                        })

            self.scan_plan.emit([info["original_path"] for info in doc_infos])

            # Let in-flight workers abort between pages once Cancel is pressed.
            for info in doc_infos:
                info["_should_cancel"] = lambda: self._cancelled

            def _make_page_progress(name):
                def _cb(page_idx, total_pages):
                    self.page_progress.emit(name, page_idx, total_pages)
                return _cb

            for info in doc_infos:
                info["_page_progress"] = _make_page_progress(info["original_filename"])

            # Extra workers beyond the number of documents add nothing: the pool
            # creates threads lazily per submitted task, so clamp for clarity.
            max_workers = max(1, min(int(max_workers), max(1, len(doc_infos))))

            processed = 0
            start_time = time.time()
            results_by_path = {}
            new_flagged = []

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_info = {}
                submitted = 0

                for info in doc_infos:
                    if self._cancelled:
                        break
                    future = executor.submit(_process_doc_worker, info, self.config, render_dpi)
                    future_to_info[future] = info
                    submitted += 1

                    if submitted % batch_size == 0:
                        self.log_message.emit(f"[INFO] Submitted {submitted}/{len(doc_infos)} documents...")

                pending = set(future_to_info)
                while pending:
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    if self._cancelled:
                        self.log_message.emit("--- Pipeline Cancelled ---")
                        self.progress.emit("Cancelled", 0)
                        self.finished_signal.emit([])
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    if not done:
                        # Heartbeat keeps the UI responsive while a long document
                        # is still being processed.
                        elapsed = time.time() - start_time
                        avg_per = elapsed / max(1, processed)
                        remaining = avg_per * (total - processed)
                        eta = f" ~{int(remaining)}s left" if remaining > 5 else ""
                        pct = 10 + int(80 * processed / max(1, total))
                        self.progress.emit(f"Processing {processed}/{total} (working...){eta}", pct)
                        continue

                    for future in done:
                        info = future_to_info[future]
                        try:
                            worker_result = future.result()
                        except Exception as e:
                            worker_result = {"status": "error", "log_messages": [f"[ERROR] {info['original_filename']}: {e}"]}

                        doc = info.get("_doc")
                        batch = info.get("_batch")
                        original_path = info["original_path"]

                        results_by_path[original_path] = worker_result

                        for msg in worker_result.get("log_messages", []):
                            self.log_message.emit(msg)

                        status = worker_result["status"]

                        if status == "confirmed" and worker_result.get("passed_data"):
                            passed_data = worker_result["passed_data"]
                            self.doc_processed.emit("passed", passed_data)
                        elif worker_result.get("flagged_data"):
                            flagged_data = worker_result["flagged_data"]
                            new_flagged.append(flagged_data)
                            self.doc_processed.emit("pending", flagged_data)

                        if doc is not None:
                            doc.blank_pages = list(worker_result.get("blank_pages", []))
                            doc.docsep_pages = list(worker_result.get("docsep_pages", []))
                            doc.status = status
                            if status == "flagged" and worker_result.get("flagged_data"):
                                doc.flagged_data = worker_result["flagged_data"]
                                doc.company_name = worker_result["flagged_data"].get("company_name", doc.company_name)
                                doc.company_confidence = worker_result["flagged_data"].get("company_confidence", 0)
                            elif status == "confirmed" and worker_result.get("passed_data"):
                                doc.confirmed_date = passed_data.get("detected_date")
                                doc.confirmed_method = passed_data.get("method", "auto")
                                doc.company_name = passed_data.get("company_name", doc.company_name)
                                doc.company_confidence = passed_data.get("company_confidence", 100)
                                doc.flagged_data = None
                            else:
                                doc.flagged_data = None

                        self.doc_completed.emit("doc", original_path)

                        processed += 1
                        elapsed = time.time() - start_time
                        avg_per = elapsed / max(1, processed)
                        remaining = avg_per * (total - processed)
                        eta = f" ~{int(remaining)}s left" if remaining > 5 else ""
                        pct = 10 + int(80 * processed / max(1, total))
                        self.progress.emit(f"Processing {processed}/{total}...{eta}", pct)

                        if processed % 50 == 0:
                            gc.collect()

            if self.plan is None:
                fresh_flagged = []
                for b in batches:
                    for d in b.documents:
                        if d.status == "flagged" and d.flagged_data:
                            fresh_flagged.append(d.flagged_data)
                flagged = _merge_flagged_docs(self.merged_flagged, fresh_flagged)
            else:
                flagged = _merge_flagged_docs(self.merged_flagged, new_flagged)

            confirmed = list(self.merged_confirmed)
            if self.plan is not None:
                confirmed_count = len(confirmed)
                flagged_count = len(flagged)
                errors = 0
            else:
                confirmed_count = sum(1 for b in batches for d in b.documents if d.status == "confirmed")
                flagged_count = sum(1 for b in batches for d in b.documents if d.status == "flagged")
                errors = sum(1 for b in batches for d in b.documents if d.status == "error")
            blank_docs = 0

            try:
                if self.plan is not None:
                    if flagged_root:
                        if new_flagged:
                            self.progress.emit("Writing flagged index...", 95)
                        self._write_merged_indexes(pipeline_config, flagged)
                elif flagged_root:
                    flagged_root.mkdir(parents=True, exist_ok=True)
                    with open(flagged_root / "flagged_index.json", "w", encoding="utf-8") as f:
                        json.dump(_json_safe_flagged(flagged), f, indent=2, ensure_ascii=False)
                    save_confirmed_documents(batches, pipeline_config)
            except Exception as e:
                self.log_message.emit(f"[WARN] Failed to write result indexes: {e}")

            summary = f"--- Done: {confirmed_count} confirmed, {flagged_count} flagged, {errors} errors"
            summary += " ---"
            self.log_message.emit(summary)
            self.progress.emit("Complete!", 100)
            self.finished_signal.emit(flagged)

        except Exception as e:
            self.error_signal.emit(str(e))
            import traceback
            self.log_message.emit(traceback.format_exc())
        finally:
            gc.collect()

    def _write_merged_indexes(self, config, flagged):
        if not getattr(config, "flagged_root", None):
            return
        flagged_root = Path(config.flagged_root)
        flagged_root.mkdir(parents=True, exist_ok=True)
        with open(flagged_root / "flagged_index.json", "w", encoding="utf-8") as f:
            json.dump(_json_safe_flagged(flagged), f, indent=2, ensure_ascii=False)


class DocCardWidget(QFrame):
    clicked = pyqtSignal(int, object)
    contextMenuRequested = pyqtSignal(int, object)

    def __init__(self, doc: Dict, idx: int, theme: str = THEME_DARK, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.doc = doc
        self.theme = theme
        self._selected = False
        self._is_duplicate = doc.get("is_duplicate", False)

        p = get_palette_dict(self.theme)

        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos, i=idx, g=self.mapToGlobal: self.contextMenuRequested.emit(i, g(pos)))
        self.setStyleSheet(self._get_style("normal"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        name = doc.get("original_filename", "Unknown")
        conf = doc.get("confidence", 0)
        div = doc.get("division_code", "?")
        blank_pages = doc.get("blank_pages", [])
        status = "DONE" if doc.get("reviewed") else "TODO"
        qc = doc.get("qc", {})
        qc_status = qc.get("qc_status", "")

        top_row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_label.setStyleSheet(f"background: transparent; color: {p['card_title_color']};")
        if self._is_duplicate:
            name_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title']}; text-decoration: underline;")
        self.name_label = name_label
        top_row.addWidget(name_label)
        top_row.addStretch()

        if self._is_duplicate:
            dup_label = QLabel("(Duplicate)")
            dup_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            dup_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title']};")
            top_row.addWidget(dup_label)

        if blank_pages:
            blank_label = QLabel(f"{len(blank_pages)} blank")
            blank_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            blank_label.setStyleSheet(f"background: transparent; color: {p['status_todo']};")
            top_row.addWidget(blank_label)

        docsep_pages = doc.get("docsep_pages", [])
        if docsep_pages:
            docsep_label = QLabel(f"{len(docsep_pages)} DOCSEP")
            docsep_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            docsep_label.setStyleSheet(f"background: transparent; color: {p['status_warn']};")
            top_row.addWidget(docsep_label)

        status_label = QLabel(status)
        status_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        status_color = p["status_done"] if status == "DONE" else p["status_todo"]
        status_label.setStyleSheet(f"background: transparent; color: {status_color};")
        top_row.addWidget(status_label)
        info_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        div_label = QLabel(f"Div: {div}")
        div_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        div_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        self.div_label = div_label
        bottom_row.addWidget(div_label)
        conf_label = QLabel(f"Conf: {conf}%")
        conf_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        conf_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        self.conf_label = conf_label
        bottom_row.addWidget(conf_label)

        if qc_status == "failed":
            qc_reasons = qc.get("qc_failure_reasons", "")
            qc_label = QLabel(f"QC: {qc_reasons[:30]}")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet(f"background: transparent; color: {p['status_bad']};")
            bottom_row.addWidget(qc_label)
        elif qc_status == "needs_review":
            qc_label = QLabel("QC: ?")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet(f"background: transparent; color: {p['status_warn']};")
            bottom_row.addWidget(qc_label)

        if doc.get("company_needs_review"):
            company_name = doc.get("company_name", "")
            company_label = QLabel(company_name)
            company_label.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            company_label.setStyleSheet(f"background: transparent; color: {p['status_bad']};")
            bottom_row.addWidget(company_label)

        bottom_row.addStretch()
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout)

    def set_theme(self, theme: str):
        self.theme = theme
        p = get_palette_dict(theme)
        if hasattr(self, "name_label"):
            self.name_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title'] if self._is_duplicate else p['card_title_color']};{' text-decoration: underline;' if self._is_duplicate else ''}")
        if hasattr(self, "div_label"):
            self.div_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        if hasattr(self, "conf_label"):
            self.conf_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        self.setStyleSheet(self._get_style("selected" if self._selected else ("reviewed" if self.doc.get("reviewed") else ("duplicate" if self._is_duplicate else "normal"))))

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.setStyleSheet(self._get_style("selected"))
        elif self.doc.get("reviewed"):
            self.setStyleSheet(self._get_style("reviewed"))
        elif self._is_duplicate:
            self.setStyleSheet(self._get_style("duplicate"))
        else:
            self.setStyleSheet(self._get_style("normal"))

    def set_duplicate(self, is_duplicate: bool):
        self._is_duplicate = is_duplicate
        self.doc["is_duplicate"] = is_duplicate
        self.setStyleSheet(self._get_style("duplicate" if is_duplicate else ("normal" if not self._selected else "selected")))

    def _get_style(self, state: str) -> str:
        p = get_palette_dict(self.theme)
        if state == "selected":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_selected']};
                    border: 1px solid {p['card_border_selected']};
                    border-radius: 8px;
                }}
            """
        elif state == "reviewed":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_reviewed']};
                    border: 1px solid {p['card_border_reviewed']};
                    border-radius: 8px;
                }}
            """
        elif state == "duplicate":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_duplicate']};
                    border: 1px solid {p['card_border_duplicate']};
                    border-radius: 8px;
                }}
                QFrame:hover {{
                    border: 1px solid {p['card_border_duplicate']};
                }}
            """
        else:
            return f"""
                QFrame {{
                    background-color: {p['bg_card']};
                    border: 1px solid {p['card_border_normal']};
                    border-radius: 8px;
                }}
                QFrame:hover {{
                    background-color: {p['bg_card_hover']};
                    border: 1px solid {p['border_focus']};
                }}
            """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.idx, event.modifiers())
        super().mousePressEvent(event)


class PassedDocCardWidget(QFrame):
    clicked = pyqtSignal(int, object)
    contextMenuRequested = pyqtSignal(int, object)

    def __init__(self, doc: Dict, idx: int, theme: str = THEME_DARK, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.doc = doc
        self.theme = theme
        self._selected = False
        self._reviewed = False
        self._is_duplicate = doc.get("is_duplicate", False)

        p = get_palette_dict(self.theme)

        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos, i=idx, g=self.mapToGlobal: self.contextMenuRequested.emit(i, g(pos)))
        self.setStyleSheet(self._get_style("normal"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        name = doc.get("original_filename", "Unknown")
        detected_date = doc.get("detected_date", "")
        confidence = doc.get("confidence", 0)
        method = doc.get("method", "")
        blank_pages = doc.get("blank_pages", [])
        qc_status = doc.get("qc_status", "")
        qc_reasons = doc.get("qc_failure_reasons", "")

        top_row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_label.setStyleSheet(f"background: transparent; color: {p['card_title_color']};")
        if self._is_duplicate:
            name_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title']}; text-decoration: underline;")
        self.name_label = name_label
        top_row.addWidget(name_label)
        top_row.addStretch()

        if self._is_duplicate:
            dup_label = QLabel("(Duplicate)")
            dup_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            dup_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title']};")
            top_row.addWidget(dup_label)

        if blank_pages:
            blank_label = QLabel(f"{len(blank_pages)} blank")
            blank_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            blank_label.setStyleSheet(f"background: transparent; color: {p['status_todo']};")
            top_row.addWidget(blank_label)

        docsep_pages = doc.get("docsep_pages", [])
        if docsep_pages:
            docsep_label = QLabel(f"{len(docsep_pages)} DOCSEP")
            docsep_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            docsep_label.setStyleSheet(f"background: transparent; color: {p['status_warn']};")
            top_row.addWidget(docsep_label)

        top_row.addStretch()
        info_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        date_label = QLabel(f"Date: {detected_date}")
        date_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        date_label.setStyleSheet(f"background: transparent; color: {p['status_done']};")
        self.date_label = date_label
        bottom_row.addWidget(date_label)
        conf_label = QLabel(f"Conf: {confidence}%")
        conf_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        conf_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        self.conf_label = conf_label
        bottom_row.addWidget(conf_label)

        if qc_status in ("failed", "needs_review") and qc_reasons:
            qc_label = QLabel(f"QC:{qc_reasons[:30]}")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet(f"background: transparent; color: {p['status_bad']};")
            bottom_row.addWidget(qc_label)

        bottom_row.addStretch()
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout)

    def set_theme(self, theme: str):
        self.theme = theme
        p = get_palette_dict(theme)
        if hasattr(self, "name_label"):
            self.name_label.setStyleSheet(f"background: transparent; color: {p['card_dup_title'] if self._is_duplicate else p['card_title_color']};{' text-decoration: underline;' if self._is_duplicate else ''}")
        if hasattr(self, "date_label"):
            self.date_label.setStyleSheet(f"background: transparent; color: {p['status_done']};")
        if hasattr(self, "conf_label"):
            self.conf_label.setStyleSheet(f"background: transparent; color: {p['text_secondary']};")
        self.setStyleSheet(self._get_style("reviewed" if self._reviewed else ("selected" if self._selected else ("duplicate" if self._is_duplicate else "normal"))))

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected and self._reviewed:
            self.setStyleSheet(self._get_style("reviewed"))
        elif selected:
            self.setStyleSheet(self._get_style("selected"))
        elif self._reviewed:
            self.setStyleSheet(self._get_style("reviewed"))
        elif self._is_duplicate:
            self.setStyleSheet(self._get_style("duplicate"))
        else:
            self.setStyleSheet(self._get_style("normal"))

    def set_reviewed(self, reviewed: bool):
        self._reviewed = reviewed
        self.setStyleSheet(self._get_style("reviewed" if reviewed else ("selected" if self._selected else ("duplicate" if self._is_duplicate else "normal"))))

    def set_duplicate(self, is_duplicate: bool):
        self._is_duplicate = is_duplicate
        self.doc["is_duplicate"] = is_duplicate
        if self._reviewed:
            self.setStyleSheet(self._get_style("reviewed"))
        elif self._selected:
            self.setStyleSheet(self._get_style("selected"))
        else:
            self.setStyleSheet(self._get_style("duplicate" if is_duplicate else "normal"))

    def _get_style(self, state: str) -> str:
        p = get_palette_dict(self.theme)
        if state == "selected":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_selected']};
                    border: 1px solid {p['card_border_selected']};
                    border-radius: 8px;
                }}
            """
        elif state == "reviewed":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_reviewed']};
                    border: 1px solid {p['card_border_reviewed']};
                    border-radius: 8px;
                }}
            """
        elif state == "duplicate":
            return f"""
                QFrame {{
                    background-color: {p['bg_card_duplicate']};
                    border: 1px solid {p['card_border_duplicate']};
                    border-radius: 8px;
                }}
                QFrame:hover {{
                    border: 1px solid {p['card_border_duplicate']};
                }}
            """
        else:
            return f"""
                QFrame {{
                    background-color: {p['bg_card']};
                    border: 1px solid {p['card_border_normal']};
                    border-radius: 8px;
                }}
                QFrame:hover {{
                    background-color: {p['bg_card_hover']};
                    border: 1px solid {p['border_focus']};
                }}
            """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.idx, event.modifiers())
        super().mousePressEvent(event)
