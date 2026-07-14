import os
import sys
import json
import time
import fitz
from pathlib import Path
from typing import Dict

from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor

sys.path.insert(0, str(Path(__file__).parent.parent))
from date_extractor import extract_document_date
from pipeline import PipelineConfig, parse_folder_structure, save_confirmed_documents


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

            processed = 0
            start_time = time.time()
            for batch in batches:
                for doc in batch.documents:
                    if doc.status == "skipped":
                        continue
                    try:
                        result = extract_document_date(doc.original_path, pipeline_config.page_index)
                        doc.date_result = result

                        try:
                            doc_file = fitz.open(doc.original_path)
                            total_pages = len(doc_file)
                            doc_file.close()
                        except Exception:
                            total_pages = len(result.blank_pages) + 1

                        if result.all_blank:
                            doc.status = "flagged"
                            doc.flagged_data = {
                                "original_path": doc.original_path,
                                "division_code": doc.division_code,
                                "company_name": doc.company_name,
                                "original_filename": doc.original_filename,
                                "error": "Document is entirely blank",
                                "blank_pages": result.blank_pages,
                                "all_blank": True,
                            }
                            self.log_message.emit(f"[BLANK] {doc.original_filename}: ALL BLANK ({total_pages} pages)")
                            self.doc_processed.emit("pending", doc.flagged_data)
                        elif result.confidence >= pipeline_config.confidence_threshold and result.date:
                            doc.confirmed_date = result.date
                            doc.confirmed_method = "auto"
                            doc.status = "confirmed"
                            blank_str = f" | {len(result.blank_pages)}/{total_pages} blank" if result.blank_pages else ""
                            self.log_message.emit(f"[AUTO] {doc.original_filename} -> {result.date} ({result.confidence}%){blank_str}")
                            self.doc_processed.emit("passed", {
                                "original_path": doc.original_path,
                                "division_code": doc.division_code,
                                "company_name": doc.company_name,
                                "original_filename": doc.original_filename,
                                "detected_date": doc.confirmed_date.isoformat(),
                                "confidence": result.confidence,
                                "method": doc.confirmed_method,
                                "blank_pages": result.blank_pages,
                            })
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
                                "blank_pages": result.blank_pages,
                            }
                            blank_str = f" | {len(result.blank_pages)}/{total_pages} blank" if result.blank_pages else ""
                            self.log_message.emit(f"[FLAGGED] {doc.original_filename} ({result.confidence}%){blank_str}")
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
