import os
import sys
import json
import time
import numpy as np
import cv2
import fitz
from pathlib import Path
from typing import Dict

from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor

sys.path.insert(0, str(Path(__file__).parent.parent))
from date_extractor import extract_document_date
from pipeline import PipelineConfig, parse_folder_structure, save_confirmed_documents
from auto_qc import run_qc_on_pdf, detect_docsep_flag

import pytesseract


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

            flagged_root = Path(self.config["flagged_root"])
            render_dpi = self.config.get("render_dpi", 150)
            pipeline_config.render_dpi = render_dpi
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

            processed = 0
            start_time = time.time()
            for batch in batches:
                for doc in batch.documents:
                    if doc.status == "skipped":
                        continue
                    try:
                        # DOCSEP detection: tag separator pages (removal happens at finalize)
                        docsep_msg = ""
                        if self.config.get("enable_docsep_removal", True):
                            try:
                                ds_result = detect_docsep_flag(doc.original_path, dpi=render_dpi)
                                doc.docsep_pages = ds_result.get("docsep_pages", [])
                                if doc.docsep_pages:
                                    docsep_msg = f" | docsep:{len(doc.docsep_pages)}"
                                    self.log_message.emit(f"[DOCSEP] {doc.original_filename}: separator page(s) detected at index {doc.docsep_pages}")
                            except Exception:
                                pass

                        result = extract_document_date(doc.original_path, pipeline_config.page_index, dpi=render_dpi)
                        doc.date_result = result
                        doc.blank_pages = list(result.blank_pages or [])

                        # Layer 3: OCR text check — re-check blank-flagged pages with Tesseract
                        if doc.blank_pages:
                            try:
                                pdf_for_ocr = fitz.open(doc.original_path)
                                for blank_pg in list(doc.blank_pages):
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
                                        doc.blank_pages.remove(blank_pg)
                                        self.log_message.emit(f"[BLANK-OCR] {doc.original_filename} p{blank_pg+1}: {len(words)} words found — NOT blank")
                                    else:
                                        self.log_message.emit(f"[BLANK-OCR] {doc.original_filename} p{blank_pg+1}: {len(words)} word(s) — confirmed blank")
                                pdf_for_ocr.close()
                            except Exception:
                                pass

                        try:
                            doc_file = fitz.open(doc.original_path)
                            total_pages = len(doc_file)
                            doc_file.close()
                        except Exception:
                            total_pages = len(doc.blank_pages) + 1

                        qc_result = None
                        if self.config.get("enable_qc", True) and not result.all_blank:
                            try:
                                qc_result = run_qc_on_pdf(
                                    doc.original_path,
                                    blank_threshold=self.config.get("qc_blank_threshold", 1.5),
                                    rotation_threshold=self.config.get("qc_rotation_threshold", 65),
                                    mirror_threshold=self.config.get("qc_mirror_threshold", 15),
                                    dpi=render_dpi,
                                )
                            except Exception:
                                pass

                        qc_failed = qc_result and qc_result.get("qc_status") in ("failed", "needs_review")
                        qc_str = ""
                        if qc_result:
                            if qc_failed:
                                reasons = qc_result.get("qc_failure_reasons", "unknown")
                                qc_str = f" | QC:FAIL({reasons})"
                            else:
                                qc_str = " | QC:Passed"

                        if result.all_blank:
                            doc.status = "flagged"
                            doc.flagged_data = {
                                "original_path": doc.original_path,
                                "division_code": doc.division_code,
                                "company_name": doc.company_name,
                                "original_filename": doc.original_filename,
                                "error": "Document is entirely blank",
                                "blank_pages": doc.blank_pages,
                                "all_blank": True,
                                "docsep_pages": doc.docsep_pages,
                            }
                            if qc_result:
                                doc.flagged_data["qc"] = qc_result
                            self.log_message.emit(f"[BLANK] {doc.original_filename}: ALL BLANK ({total_pages} pages){qc_str}{docsep_msg}")
                            self.doc_processed.emit("pending", doc.flagged_data)
                        elif result.confidence >= pipeline_config.confidence_threshold and result.date:
                            blank_str = f" | {len(doc.blank_pages)}/{total_pages} blank" if doc.blank_pages else ""
                            if qc_failed:
                                doc.status = "flagged"
                                doc.flagged_data = {
                                    "original_path": doc.original_path,
                                    "division_code": doc.division_code,
                                    "company_name": doc.company_name,
                                    "original_filename": doc.original_filename,
                                    "best_guess_date": result.date.isoformat() if result.date else None,
                                    "confidence": result.confidence,
                                    "method": result.method,
                                    "raw_ocr_text": result.raw_ocr_text,
                                    "blank_pages": doc.blank_pages,
                                    "docsep_pages": doc.docsep_pages,
                                }
                                if qc_result:
                                    doc.flagged_data["qc"] = qc_result
                                self.log_message.emit(f"[FLAGGED] {doc.original_filename} ({result.confidence}%){blank_str}{qc_str}{docsep_msg}")
                                self.doc_processed.emit("pending", doc.flagged_data)
                            else:
                                doc.confirmed_date = result.date
                                doc.confirmed_method = "auto"
                                doc.status = "confirmed"
                                passed_data = {
                                    "original_path": doc.original_path,
                                    "division_code": doc.division_code,
                                    "company_name": doc.company_name,
                                    "original_filename": doc.original_filename,
                                    "detected_date": doc.confirmed_date.isoformat(),
                                    "confidence": result.confidence,
                                    "method": doc.confirmed_method,
                                    "blank_pages": doc.blank_pages,
                                    "docsep_pages": doc.docsep_pages,
                                }
                                if qc_result:
                                    passed_data["qc_status"] = qc_result.get("qc_status", "")
                                    passed_data["qc_failure_reasons"] = qc_result.get("qc_failure_reasons", "")
                                self.log_message.emit(f"[AUTO] {doc.original_filename} -> {result.date} ({result.confidence}%){blank_str}{qc_str}{docsep_msg}")
                                self.doc_processed.emit("passed", passed_data)
                        else:
                            doc.status = "flagged"
                            doc.flagged_data = {
                                "original_path": doc.original_path,
                                "division_code": doc.division_code,
                                "company_name": doc.company_name,
                                "original_filename": doc.original_filename,
                                "best_guess_date": result.date.isoformat() if result.date else None,
                                "confidence": result.confidence,
                                "method": result.method,
                                "raw_ocr_text": result.raw_ocr_text,
                                "blank_pages": doc.blank_pages,
                                "docsep_pages": doc.docsep_pages,
                            }
                            if qc_result:
                                doc.flagged_data["qc"] = qc_result
                            blank_str = f" | {len(doc.blank_pages)}/{total_pages} blank" if doc.blank_pages else ""
                            self.log_message.emit(f"[FLAGGED] {doc.original_filename} ({result.confidence}%){blank_str}{qc_str}{docsep_msg}")
                            self.doc_processed.emit("pending", doc.flagged_data)
                    except Exception as e:
                        doc.status = "error"
                        self.log_message.emit(f"[ERROR] {doc.original_filename}: {e}")

                    processed += 1
                    if self._cancelled:
                        self.log_message.emit("--- Pipeline Cancelled ---")
                        self.progress.emit("Cancelled", 0)
                        self.finished_signal.emit([])
                        return

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

        status_label = QLabel("PASSED")
        status_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        status_label.setStyleSheet("background: transparent; color: #4caf50;")
        top_row.addWidget(status_label)
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
        if method:
            method_label = QLabel(f"({method})")
            method_label.setFont(QFont("Segoe UI", 7))
            method_label.setStyleSheet("background: transparent; color: #666688;")
            bottom_row.addWidget(method_label)

        if qc_status == "passed":
            qc_label = QLabel("QC:Passed")
            qc_label.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            qc_label.setStyleSheet("background: transparent; color: #66bb6a;")
            bottom_row.addWidget(qc_label)
        elif qc_status in ("failed", "needs_review") and qc_reasons:
            qc_label = QLabel(f"QC:{qc_reasons[:30]}")
            qc_label.setFont(QFont("Segoe UI", 7))
            qc_label.setStyleSheet("background: transparent; color: #ff7043;")
            bottom_row.addWidget(qc_label)

        bottom_row.addStretch()
        info_layout.addLayout(bottom_row)

        layout.addLayout(info_layout)

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.setStyleSheet(self._get_style("selected"))
        else:
            self.setStyleSheet(self._get_style("normal"))

    def _get_style(self, state: str) -> str:
        if state == "selected":
            return """
                QFrame {
                    background-color: #1a3a2a;
                    border: 1px solid #4caf50;
                    border-radius: 8px;
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
