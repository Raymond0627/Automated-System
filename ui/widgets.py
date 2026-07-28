import os
import sys
import json
import time
import numpy as np
import cv2
import fitz
from pathlib import Path
from typing import Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date

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

import pytesseract


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

    try:
        original_path = doc_info["original_path"]
        original_filename = doc_info["original_filename"]
        division_code = doc_info["division_code"]
        company_name = doc_info["company_name"]

        docsep_msg = ""
        if config.get("enable_docsep_removal", True):
            try:
                ds_result = detect_docsep_flag(original_path, dpi=render_dpi)
                result["docsep_pages"] = ds_result.get("docsep_pages", [])
                if result["docsep_pages"]:
                    docsep_msg = f" | docsep:{len(result['docsep_pages'])}"
                    result["log_messages"].append(f"[DOCSEP] {original_filename}: separator page(s) detected at index {result['docsep_pages']}")
            except Exception:
                pass

        page_index = config.get("page_index", 0)
        date_result = extract_document_date(original_path, page_index, dpi=render_dpi)
        result["blank_pages"] = list(date_result.blank_pages or [])

        total_pages = 0
        if result["blank_pages"]:
            try:
                pdf_for_ocr = fitz.open(original_path)
                total_pages = len(pdf_for_ocr)
                for blank_pg in list(result["blank_pages"]):
                    if blank_pg >= len(pdf_for_ocr):
                        continue
                    pg = pdf_for_ocr[blank_pg]
                    pix = pg.get_pixmap(dpi=render_dpi)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    text = pytesseract.image_to_string(gray, config='--psm 6').strip()
                    words = [w for w in text.split() if len(w) >= 2]
                    if len(words) >= 2:
                        result["blank_pages"].remove(blank_pg)
                        result["log_messages"].append(f"[BLANK-OCR] {original_filename} p{blank_pg+1}: {len(words)} words found - NOT blank")
                    else:
                        result["log_messages"].append(f"[BLANK-OCR] {original_filename} p{blank_pg+1}: {len(words)} word(s) - confirmed blank")
                pdf_for_ocr.close()
            except Exception:
                pass

        result["total_pages"] = total_pages

        qc_result = None
        if config.get("enable_qc", True) and not date_result.all_blank:
            try:
                gpu_mode = config.get("gpu_mode", "cpu")
                use_gpu = gpu_mode == "local_gpu"
                if gpu_mode == "remote":
                    from auto_qc import set_remote_gpu_url
                    set_remote_gpu_url(config.get("remote_gpu_url", ""))
                qc_result = run_qc_on_pdf(
                    original_path,
                    blank_threshold=config.get("qc_blank_threshold", 1.5),
                    rotation_threshold=config.get("qc_rotation_threshold", 65),
                    mirror_threshold=config.get("qc_mirror_threshold", 15),
                    dpi=render_dpi,
                    use_gpu=use_gpu,
                    gpu_mode=gpu_mode,
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

        page_ocr_data_list = []
        for idx, page_text in enumerate(date_result.page_texts):
            page_ocr_data_list.append({
                'page_text': page_text,
                'page_idx': idx,
            })
        company_result = get_company_name_for_filename(page_ocr_data_list, company_name)
        final_company = company_result['company_name']

        if date_result.all_blank:
            result["status"] = "flagged"
            result["flagged_data"] = {
                "original_path": original_path,
                "division_code": division_code,
                "company_name": final_company,
                "original_filename": original_filename,
                "error": "Document is entirely blank",
                "blank_pages": result["blank_pages"],
                "all_blank": True,
                "docsep_pages": result["docsep_pages"],
                "company_source": company_result['source'],
            }
            if qc_result:
                result["flagged_data"]["qc"] = qc_result
            result["log_messages"].append(f"[BLANK] {original_filename}: ALL BLANK ({total_pages} pages){qc_str}{docsep_msg}")
        elif date_result.confidence >= config.get("confidence_threshold", 70) and date_result.date:
            company_known = (company_result['source'] in ('header', 'addressee')
                             and not company_result.get('needs_review', False))
            if qc_failed:
                result["status"] = "flagged"
                result["flagged_data"] = {
                    "original_path": original_path,
                    "division_code": division_code,
                    "company_name": final_company,
                    "original_filename": original_filename,
                    "best_guess_date": date_result.date.isoformat() if date_result.date else None,
                    "confidence": date_result.confidence,
                    "method": date_result.method,
                    "raw_ocr_text": date_result.raw_ocr_text,
                    "blank_pages": result["blank_pages"],
                    "docsep_pages": result["docsep_pages"],
                    "company_source": company_result['source'],
                }
                if qc_result:
                    result["flagged_data"]["qc"] = qc_result
                result["log_messages"].append(f"[FLAGGED] {original_filename} ({date_result.confidence}%){blank_str}{qc_str}{docsep_msg}")
            elif not company_known:
                result["status"] = "flagged"
                result["flagged_data"] = {
                    "original_path": original_path,
                    "division_code": division_code,
                    "company_name": final_company,
                    "original_filename": original_filename,
                    "best_guess_date": date_result.date.isoformat() if date_result.date else None,
                    "confidence": date_result.confidence,
                    "method": date_result.method,
                    "raw_ocr_text": date_result.raw_ocr_text,
                    "blank_pages": result["blank_pages"],
                    "docsep_pages": result["docsep_pages"],
                    "company_source": company_result['source'],
                    "company_needs_review": True,
                }
                if qc_result:
                    result["flagged_data"]["qc"] = qc_result
                result["log_messages"].append(f"[FLAGGED-COMPANY] {original_filename} company='{final_company}' source={company_result['source']}")
            else:
                result["status"] = "confirmed"
                result["passed_data"] = {
                    "original_path": original_path,
                    "division_code": division_code,
                    "company_name": final_company,
                    "original_filename": original_filename,
                    "detected_date": date_result.date.isoformat(),
                    "confidence": date_result.confidence,
                    "method": "auto",
                    "blank_pages": result["blank_pages"],
                    "docsep_pages": result["docsep_pages"],
                    "raw_ocr_text": date_result.raw_ocr_text,
                    "company_source": company_result['source'],
                }
                if qc_result:
                    result["passed_data"]["qc_status"] = qc_result.get("qc_status", "")
                    result["passed_data"]["qc_failure_reasons"] = qc_result.get("qc_failure_reasons", "")
                result["log_messages"].append(f"[AUTO] {original_filename} -> {date_result.date} ({date_result.confidence}%){blank_str}{qc_str}{docsep_msg}")
        else:
            result["status"] = "flagged"
            result["flagged_data"] = {
                "original_path": original_path,
                "division_code": division_code,
                "company_name": final_company,
                "original_filename": original_filename,
                "best_guess_date": date_result.date.isoformat() if date_result.date else None,
                "confidence": date_result.confidence,
                "method": date_result.method,
                "raw_ocr_text": date_result.raw_ocr_text,
                "blank_pages": result["blank_pages"],
                "docsep_pages": result["docsep_pages"],
                "company_source": company_result['source'],
            }
            if qc_result:
                result["flagged_data"]["qc"] = qc_result
            result["log_messages"].append(f"[FLAGGED] {original_filename} ({date_result.confidence}%){blank_str}{qc_str}{docsep_msg}")
    except Exception as e:
        result["status"] = "error"
        result["log_messages"].append(f"[ERROR] {doc_info['original_filename']}: {e}")

    return result


class PipelineThread(QThread):
    progress = pyqtSignal(str, int)
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(list)
    error_signal = pyqtSignal(str)
    doc_processed = pyqtSignal(str, dict)

    def __init__(self, config: dict, max_files: int = 0):
        super().__init__()
        self.config = config
        self.max_files = max_files
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit("Scanning folders...", 5)
            pipeline_config = PipelineConfig(
                input_root=self.config["input_root"],
                output_root=self.config["output_root"],
                flagged_root=self.config["flagged_root"],
                confidence_threshold=self.config["confidence_threshold"],
                page_index=self.config["page_index"],
                earliest_year=self.config["earliest_year"],
                ocr_engine=self.config.get("ocr_engine", "tesseract"),
            )
            pipeline_config.enable_docsep_removal = self.config.get("enable_docsep_removal", True)
            pipeline_config.enable_blank_removal = self.config.get("enable_blank_removal", True)
            pipeline_config.rename_enabled = self.config.get("rename_enabled", True)
            pipeline_config.audit_enabled = self.config.get("audit_enabled", True)

            render_dpi = self.config.get("render_dpi", 150)
            pipeline_config.render_dpi = render_dpi
            max_workers = self.config.get("max_workers", 4)

            flagged_root = Path(self.config["flagged_root"])
            flagged_root.mkdir(parents=True, exist_ok=True)
            for f in ["flagged_index.json", "passed_index.json"]:
                p = flagged_root / f
                if p.exists():
                    p.unlink()

            batches = parse_folder_structure(pipeline_config.input_root)
            total = sum(len(b.documents) for b in batches)

            if self.max_files > 0:
                count = 0
                for batch in batches:
                    for doc in batch.documents:
                        if count >= self.max_files:
                            doc.status = "skipped"
                        count += 1
                total = min(total, self.max_files)
                self.log_message.emit(f"Found {total} PDFs (limited to {self.max_files}) in {len(batches)} divisions")
            else:
                self.log_message.emit(f"Found {total} PDFs in {len(batches)} divisions")

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

            processed = 0
            start_time = time.time()
            results_by_path = {}

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_info = {}
                for info in doc_infos:
                    future = executor.submit(_process_doc_worker, info, self.config, render_dpi)
                    future_to_info[future] = info

                for future in as_completed(future_to_info):
                    if self._cancelled:
                        self.log_message.emit("--- Pipeline Cancelled ---")
                        self.progress.emit("Cancelled", 0)
                        self.finished_signal.emit([])
                        executor.shutdown(wait=False, cancel_futures=True)
                        return

                    info = future_to_info[future]
                    try:
                        worker_result = future.result()
                    except Exception as e:
                        worker_result = {"status": "error", "log_messages": [f"[ERROR] {info['original_filename']}: {e}"]}

                    doc = info["_doc"]
                    batch = info["_batch"]

                    results_by_path[info["original_path"]] = worker_result

                    for msg in worker_result.get("log_messages", []):
                        self.log_message.emit(msg)

                    blank_pages = worker_result.get("blank_pages", [])
                    docsep_pages = worker_result.get("docsep_pages", [])
                    total_pages = worker_result.get("total_pages", 0)

                    doc.blank_pages = list(blank_pages)
                    doc.docsep_pages = list(docsep_pages)
                    doc.status = worker_result["status"]

                    if worker_result["status"] == "flagged" and worker_result.get("flagged_data"):
                        doc.flagged_data = worker_result["flagged_data"]
                        self.doc_processed.emit("pending", worker_result["flagged_data"])
                    elif worker_result["status"] == "confirmed" and worker_result.get("passed_data"):
                        passed_data = worker_result["passed_data"]
                        doc.confirmed_date = passed_data.get("detected_date")
                        doc.confirmed_method = passed_data.get("method", "auto")
                        doc.flagged_data = None
                        self.doc_processed.emit("passed", passed_data)
                    else:
                        doc.flagged_data = None

                    processed += 1
                    elapsed = time.time() - start_time
                    avg_per = elapsed / max(1, processed)
                    remaining = avg_per * (total - processed)
                    eta = f" ~{int(remaining)}s left" if remaining > 5 else ""
                    pct = 10 + int(80 * processed / max(1, total))
                    self.progress.emit(f"Processing {processed}/{total}...{eta}", pct)

            flagged = []
            for b in batches:
                for d in b.documents:
                    if d.status == "flagged" and d.flagged_data:
                        flagged.append(d.flagged_data)
            flagged_root = Path(self.config["flagged_root"])
            flagged_root.mkdir(parents=True, exist_ok=True)
            with open(flagged_root / "flagged_index.json", "w", encoding="utf-8") as f:
                json.dump(flagged, f, indent=2, ensure_ascii=False)

            confirmed = sum(1 for b in batches for d in b.documents if d.status == "confirmed")
            flagged_count = sum(1 for b in batches for d in b.documents if d.status == "flagged")
            errors = sum(1 for b in batches for d in b.documents if d.status == "error")
            blank_docs = sum(1 for b in batches for d in b.documents if d.date_result and d.date_result.all_blank)
            blank_pages_total = sum(len(d.date_result.blank_pages) for b in batches for d in b.documents if d.date_result and d.date_result.blank_pages)

            save_confirmed_documents(batches, pipeline_config)

            summary = f"--- Done: {confirmed} confirmed, {flagged_count} flagged, {errors} errors"
            if blank_pages_total > 0:
                summary += f" | {blank_pages_total} blank page(s) in {blank_docs} doc(s)"
            summary += " ---"
            self.log_message.emit(summary)
            self.progress.emit("Complete!", 100)
            self.finished_signal.emit(flagged)

        except Exception as e:
            self.error_signal.emit(str(e))
            import traceback
            self.log_message.emit(traceback.format_exc())


class DocCardWidget(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, doc: Dict, idx: int, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.doc = doc
        self._selected = False

        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        name_label.setStyleSheet("background: transparent; color: #ffffff;")
        top_row.addWidget(name_label)
        top_row.addStretch()

        if blank_pages:
            blank_label = QLabel(f"{len(blank_pages)} blank")
            blank_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            blank_label.setStyleSheet("background: transparent; color: #ff9800;")
            top_row.addWidget(blank_label)

        docsep_pages = doc.get("docsep_pages", [])
        if docsep_pages:
            docsep_label = QLabel(f"{len(docsep_pages)} DOCSEP")
            docsep_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            docsep_label.setStyleSheet("background: transparent; color: #ffab00;")
            top_row.addWidget(docsep_label)

        status_label = QLabel(status)
        status_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        status_color = "#4caf50" if status == "DONE" else "#ff9800"
        status_label.setStyleSheet(f"background: transparent; color: {status_color};")
        top_row.addWidget(status_label)
        info_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        div_label = QLabel(f"Div: {div}")
        div_label.setFont(QFont("Segoe UI", 8))
        div_label.setStyleSheet("background: transparent; color: #8888aa;")
        bottom_row.addWidget(div_label)
        conf_label = QLabel(f"Conf: {conf}%")
        conf_label.setFont(QFont("Segoe UI", 8))
        conf_label.setStyleSheet("background: transparent; color: #8888aa;")
        bottom_row.addWidget(conf_label)

        if qc_status == "failed":
            qc_reasons = qc.get("qc_failure_reasons", "")
            qc_label = QLabel(f"QC: {qc_reasons[:30]}")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet("background: transparent; color: #ff7043;")
            bottom_row.addWidget(qc_label)
        elif qc_status == "needs_review":
            qc_label = QLabel("QC: ?")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet("background: transparent; color: #ffa726;")
            bottom_row.addWidget(qc_label)

        if doc.get("company_needs_review"):
            company_name = doc.get("company_name", "")
            company_label = QLabel(company_name)
            company_label.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            company_label.setStyleSheet("background: transparent; color: #ff4444;")
            bottom_row.addWidget(company_label)

        bottom_row.addStretch()
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout)

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.setStyleSheet(self._get_style("selected"))
        elif self.doc.get("reviewed"):
            self.setStyleSheet(self._get_style("reviewed"))
        else:
            self.setStyleSheet(self._get_style("normal"))

    def _get_style(self, state: str) -> str:
        if state == "selected":
            return """
                QFrame {
                    background-color: #2a4a7a;
                    border: 1px solid #4a6fa5;
                    border-radius: 8px;
                }
            """
        elif state == "reviewed":
            return """
                QFrame {
                    background-color: #1a3a2a;
                    border: 1px solid #2d6a3f;
                    border-radius: 8px;
                }
            """
        else:
            return """
                QFrame {
                    background-color: #252640;
                    border: 1px solid #3a3b55;
                    border-radius: 8px;
                }
                QFrame:hover {
                    background-color: #2d3055;
                    border: 1px solid #4a4b65;
                }
            """

    def mousePressEvent(self, event):
        self.clicked.emit(self.idx)
        super().mousePressEvent(event)


class PassedDocCardWidget(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, doc: Dict, idx: int, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.doc = doc
        self._selected = False
        self._reviewed = False

        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        name_label.setStyleSheet("background: transparent; color: #ffffff;")
        top_row.addWidget(name_label)
        top_row.addStretch()

        if blank_pages:
            blank_label = QLabel(f"{len(blank_pages)} blank")
            blank_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            blank_label.setStyleSheet("background: transparent; color: #ff9800;")
            top_row.addWidget(blank_label)

        docsep_pages = doc.get("docsep_pages", [])
        if docsep_pages:
            docsep_label = QLabel(f"{len(docsep_pages)} DOCSEP")
            docsep_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            docsep_label.setStyleSheet("background: transparent; color: #ffab00;")
            top_row.addWidget(docsep_label)

        top_row.addStretch()
        info_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        date_label = QLabel(f"Date: {detected_date}")
        date_label.setFont(QFont("Segoe UI", 8))
        date_label.setStyleSheet("background: transparent; color: #88cc88;")
        bottom_row.addWidget(date_label)
        conf_label = QLabel(f"Conf: {confidence}%")
        conf_label.setFont(QFont("Segoe UI", 8))
        conf_label.setStyleSheet("background: transparent; color: #8888aa;")
        bottom_row.addWidget(conf_label)

        if qc_status in ("failed", "needs_review") and qc_reasons:
            qc_label = QLabel(f"QC:{qc_reasons[:30]}")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet("background: transparent; color: #ff7043;")
            bottom_row.addWidget(qc_label)

        bottom_row.addStretch()
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout)

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected and self._reviewed:
            self.setStyleSheet(self._get_style("reviewed"))
        elif selected:
            self.setStyleSheet(self._get_style("selected"))
        elif self._reviewed:
            self.setStyleSheet(self._get_style("reviewed"))
        else:
            self.setStyleSheet(self._get_style("normal"))

    def set_reviewed(self, reviewed: bool):
        self._reviewed = reviewed
        self.setStyleSheet(self._get_style("reviewed" if reviewed else ("selected" if self._selected else "normal")))

    def _get_style(self, state: str) -> str:
        if state == "selected":
            return """
                QFrame {
                    background-color: #1a3a2a;
                    border: 1px solid #4caf50;
                    border-radius: 8px;
                }
            """
        elif state == "reviewed":
            return """
                QFrame {
                    background-color: #1a2a3a;
                    border: 1px solid #4a6fa5;
                    border-radius: 8px;
                }
                QFrame:hover {
                    background-color: #1f3045;
                    border: 1px solid #5a7fb5;
                }
            """
        else:
            return """
                QFrame {
                    background-color: #1a2a1a;
                    border: 1px solid #2d4a2d;
                    border-radius: 8px;
                }
                QFrame:hover {
                    background-color: #1f351f;
                    border: 1px solid #3a5a3a;
                }
            """

    def mousePressEvent(self, event):
        self.clicked.emit(self.idx)
        super().mousePressEvent(event)
