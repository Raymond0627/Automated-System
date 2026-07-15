import os
import json
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QGroupBox, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPixmap

from .widgets import PipelineThread


class DashboardTab(QWidget):
    log_signal = pyqtSignal(str)
    pipeline_finished = pyqtSignal()
    doc_update = pyqtSignal(str, dict)
    pipeline_started = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.pipeline_thread = None
        self._cancel_requested = False
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 10, 14, 10)

        header = QHBoxLayout()
        logo_path = Path(__file__).parent.parent / "Lumeed Logo.png"
        if logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(pixmap.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            logo_label.setFixedSize(40, 40)
            header.addWidget(logo_label)
        title = QLabel("Lumeed QScan")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e0ff; padding: 0;")
        header.addWidget(title)
        subtitle = QLabel("PDF Auto-Rename & OCR Pipeline")
        subtitle.setFont(QFont("Segoe UI", 9))
        subtitle.setStyleSheet("color: #7778a0; padding: 6px 0 0 4px;")
        header.addWidget(subtitle)
        header.addStretch()
        layout.addLayout(header)

        folder_group = QGroupBox("Folder Configuration")
        folder_layout = QGridLayout(folder_group)
        folder_layout.setSpacing(6)

        self.input_var = QLineEdit(self.config["input_root"])
        self.output_var = QLineEdit(self.config["output_root"])
        self.flagged_var = QLineEdit(self.config["flagged_root"])
        for v in (self.input_var, self.output_var, self.flagged_var):
            v.setFont(QFont("Segoe UI", 9))

        folder_layout.addWidget(QLabel("Input:"), 0, 0)
        folder_layout.addWidget(self.input_var, 0, 1)
        btn_in = QPushButton("Browse")
        btn_in.setFixedSize(72, 26)
        btn_in.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn_in.clicked.connect(lambda: self._browse(self.input_var))
        folder_layout.addWidget(btn_in, 0, 2)

        folder_layout.addWidget(QLabel("Output:"), 1, 0)
        folder_layout.addWidget(self.output_var, 1, 1)
        btn_out = QPushButton("Browse")
        btn_out.setFixedSize(72, 26)
        btn_out.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn_out.clicked.connect(lambda: self._browse(self.output_var))
        folder_layout.addWidget(btn_out, 1, 2)

        folder_layout.addWidget(QLabel("Flagged:"), 2, 0)
        folder_layout.addWidget(self.flagged_var, 2, 1)
        btn_flag = QPushButton("Browse")
        btn_flag.setFixedSize(72, 26)
        btn_flag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn_flag.clicked.connect(lambda: self._browse(self.flagged_var))
        folder_layout.addWidget(btn_flag, 2, 2)

        layout.addWidget(folder_group)

        pipeline_group = QGroupBox("Pipeline Control")
        pipeline_layout = QVBoxLayout(pipeline_group)
        pipeline_layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.scan_btn = QPushButton("Scan Folder")
        self.scan_btn.setFixedHeight(28)
        self.scan_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.scan_btn.clicked.connect(self.scan_folder)
        btn_row.addWidget(self.scan_btn)

        self.run_btn = QPushButton("Run Pipeline")
        self.run_btn.setObjectName("accent")
        self.run_btn.setFixedHeight(28)
        self.run_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.run_btn.clicked.connect(self.run_pipeline)
        btn_row.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setFixedHeight(28)
        self.cancel_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_pipeline)
        btn_row.addWidget(self.cancel_btn)

        pipeline_layout.addLayout(btn_row)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(28)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Idle")
        progress_row.addWidget(self.progress_bar, 1)
        pipeline_layout.addLayout(progress_row)

        layout.addWidget(pipeline_group)

        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)
        log_layout.setSpacing(4)
        log_layout.setContentsMargins(6, 10, 6, 6)

        log_toolbar = QHBoxLayout()
        self.scan_label = QLabel("")
        self.scan_label.setStyleSheet("color: #7778a0; font-size: 8pt;")
        log_toolbar.addWidget(self.scan_label)
        log_toolbar.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(60, 24)
        clear_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        clear_btn.clicked.connect(self._clear_log)
        log_toolbar.addWidget(clear_btn)
        log_layout.addLayout(log_toolbar)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(7)
        self.log_table.setHorizontalHeaderLabels(["Time", "Status", "File", "Detected Date", "Confidence", "Blank Pages", "QC"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.log_table.setShowGrid(False)
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setMinimumHeight(120)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.setColumnWidth(5, 70)
        self.log_table.setColumnWidth(6, 80)
        log_layout.addWidget(self.log_table)
        layout.addWidget(log_group, 1)

        self.log_signal.connect(self._append_log)

    def _clear_log(self):
        self.log_table.setRowCount(0)

    def _browse(self, var: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Folder", var.text() or "/")
        if path:
            var.setText(path)
            self.config["input_root"] = self.input_var.text()
            self.config["output_root"] = self.output_var.text()
            self.config["flagged_root"] = self.flagged_var.text()
            self._save_config()

    def _save_config(self):
        base = Path(__file__).parent.parent
        config_file = base / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def _append_log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        detected_date = ""
        confidence = ""
        filename = ""
        blank_count = ""
        qc_text = ""

        # Extract QC suffix if present
        qc_suffix = ""
        if " | QC:" in msg:
            parts = msg.split(" | QC:", 1)
            msg = parts[0]
            qc_suffix = "QC:" + parts[1]
            if qc_suffix.startswith("QC:FAIL"):
                qc_text = "FAIL"
            elif qc_suffix.startswith("QC:Passed"):
                qc_text = "OK"

        if "[AUTO]" in msg:
            status = "AUTO"
            color = "#4caf50"
            parts = msg.split("[AUTO] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if " | " in detail:
                    main_part, blank_part = detail.split(" | ", 1)
                    blank_count = blank_part.replace(" blank", "")
                    detail = main_part
                if " -> " in detail:
                    filename, rest = detail.split(" -> ", 1)
                    if " (" in rest:
                        detected_date = rest.split(" (")[0]
                        confidence = rest.split("(")[1].split(")")[0]
                    else:
                        detected_date = rest
                else:
                    filename = detail
        elif "[FLAGGED]" in msg:
            status = "FLAGGED"
            color = "#ff9800"
            parts = msg.split("[FLAGGED] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if " | " in detail:
                    main_part, blank_part = detail.split(" | ", 1)
                    blank_count = blank_part.replace(" blank", "")
                    detail = main_part
                if " (" in detail:
                    filename = detail.rsplit(" (", 1)[0]
                    confidence = detail.rsplit(" (", 1)[1].rstrip(")")
                else:
                    filename = detail
        elif "[ERROR]" in msg:
            status = "ERROR"
            color = "#f44336"
            parts = msg.split("[ERROR] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    confidence = detail.split(": ", 1)[1]
                else:
                    confidence = detail
        elif "[BLANK]" in msg:
            status = "BLANK"
            color = "#607d8b"
            parts = msg.split("[BLANK] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    rest = detail.split(": ", 1)[1]
                    if "ALL BLANK" in rest:
                        blank_count = "ALL"
                    else:
                        blank_count = rest
        elif "[DOCSEP]" in msg:
            status = "DOCSEP"
            color = "#9c27b0"
            parts = msg.split("[DOCSEP] ", 1)
            if len(parts) > 1:
                detail = parts[1]
                if ": " in detail:
                    filename = detail.split(": ")[0]
                    confidence = detail.split(": ", 1)[1]
        elif "Done" in msg:
            status = "DONE"
            color = "#2196f3"
            confidence = msg.replace("--- ", "").replace(" ---", "")
        else:
            return

        row = self.log_table.rowCount()
        self.log_table.insertRow(row)

        align_center = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter

        time_item = QTableWidgetItem(timestamp)
        time_item.setForeground(QColor("#555570"))
        time_item.setFont(QFont("Consolas", 8))
        time_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 0, time_item)

        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor(color))
        status_item.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        status_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 1, status_item)

        file_item = QTableWidgetItem(filename)
        file_item.setForeground(QColor("#d0d0e0"))
        file_item.setFont(QFont("Consolas", 8))
        file_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 2, file_item)

        date_item = QTableWidgetItem(detected_date)
        date_item.setForeground(QColor("#90d090") if detected_date else QColor("#555570"))
        date_item.setFont(QFont("Consolas", 8))
        date_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 3, date_item)

        conf_item = QTableWidgetItem(confidence)
        conf_item.setForeground(QColor("#9090a0"))
        conf_item.setFont(QFont("Consolas", 8))
        conf_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 4, conf_item)

        blank_item = QTableWidgetItem(blank_count)
        blank_item.setForeground(QColor("#ff9800") if blank_count else QColor("#555570"))
        blank_item.setFont(QFont("Consolas", 8))
        blank_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 5, blank_item)

        qc_item = QTableWidgetItem(qc_text)
        if qc_text == "FAIL":
            qc_item.setForeground(QColor("#ff7043"))
        elif qc_text == "OK":
            qc_item.setForeground(QColor("#66bb6a"))
        else:
            qc_item.setForeground(QColor("#555570"))
        qc_item.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        qc_item.setTextAlignment(align_center)
        self.log_table.setItem(row, 6, qc_item)

        self.log_table.scrollToBottom()

    def scan_folder(self):
        path = self.input_var.text()
        if not os.path.isdir(path):
            QMessageBox.critical(self, "Error", f"Input folder not found:\n{path}")
            return
        pdfs, divs, companies = 0, 0, 0
        for d in Path(path).iterdir():
            if d.is_dir():
                divs += 1
                for c in d.iterdir():
                    if c.is_dir():
                        companies += 1
                        pdfs += len(list(c.glob("*.pdf")))
        ocr = self.config.get("ocr_engine", "tesseract")
        thresh = self.config.get("confidence_threshold", 20)
        year = self.config.get("earliest_year", 1950)
        self.scan_label.setText(f"OCR: {ocr.upper()}  |  Threshold: {thresh}%  |  Year: {year}+  |  {pdfs} PDFs in {divs} division(s)")

    def run_pipeline(self):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            return

        self.config["input_root"] = self.input_var.text()
        self.config["output_root"] = self.output_var.text()
        self.config["flagged_root"] = self.flagged_var.text()
        self._save_config()

        if not os.path.isdir(self.config["input_root"]):
            QMessageBox.critical(self, "Error", "Input folder does not exist")
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Running...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.cancel_btn.setEnabled(True)
        self.log_signal.emit("=== Pipeline Started ===")
        self.pipeline_started.emit()

        self.pipeline_thread = PipelineThread(self.config, 0)
        self.pipeline_thread.progress.connect(self._on_progress)
        self.pipeline_thread.log_message.connect(self._append_log)
        self.pipeline_thread.finished_signal.connect(self._on_finished)
        self.pipeline_thread.error_signal.connect(self._on_error)
        self.pipeline_thread.doc_processed.connect(self.doc_update)
        self.pipeline_thread.start()

    def _on_progress(self, msg: str, pct: int):
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(msg)

    def _on_finished(self, flagged: list):
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run Pipeline")
        self.cancel_btn.setEnabled(False)
        if self._cancel_requested:
            self.progress_bar.setFormat("Cancelled")
            self._cancel_requested = False
        else:
            self.progress_bar.setFormat("Complete!")
        self.progress_bar.setValue(100)
        self.pipeline_finished.emit()

    def _on_error(self, msg: str):
        self._append_log(f"Pipeline error: {msg}")
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run Pipeline")
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setFormat("Error")

    def cancel_pipeline(self):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            self.pipeline_thread.cancel()
            self._cancel_requested = True
            self.cancel_btn.setEnabled(False)
            self.progress_bar.setFormat("Cancelling...")
            self.log_signal.emit("--- Cancel requested ---")
