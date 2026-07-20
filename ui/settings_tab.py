import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QMessageBox, QGroupBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class SettingsTab(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(12)

        settings_group = QGroupBox("Pipeline Settings")
        settings_layout = QFormLayout(settings_group)
        settings_layout.setSpacing(10)

        self.conf_spin = QSpinBox()
        self.conf_spin.setRange(0, 100)
        self.conf_spin.setValue(self.config.get("confidence_threshold", 20))
        self.conf_spin.setFixedWidth(120)
        settings_layout.addRow("Confidence Threshold (0-100):", self.conf_spin)

        self.page_spin = QSpinBox()
        self.page_spin.setRange(0, 100)
        self.page_spin.setValue(self.config.get("page_index", 0))
        self.page_spin.setFixedWidth(120)
        settings_layout.addRow("PDF Page Index (0 = first):", self.page_spin)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1900, 2100)
        self.year_spin.setValue(self.config.get("earliest_year", 1950))
        self.year_spin.setFixedWidth(120)
        settings_layout.addRow("Earliest Valid Year:", self.year_spin)

        self.ocr_combo = QComboBox()
        self.ocr_combo.addItems(["tesseract", "paddleocr"])
        self.ocr_combo.setCurrentText(self.config.get("ocr_engine", "tesseract"))
        self.ocr_combo.setFixedWidth(120)
        settings_layout.addRow("OCR Engine:", self.ocr_combo)

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setSingleStep(10)
        self.dpi_spin.setValue(self.config.get("render_dpi", 150))
        self.dpi_spin.setFixedWidth(120)
        settings_layout.addRow("Render DPI (72-600):", self.dpi_spin)

        layout.addWidget(settings_group)

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("accent")
        self.save_btn.setFixedWidth(150)
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(30)

        qc_group = QGroupBox("Auto QC")
        qc_layout = QVBoxLayout(qc_group)
        qc_layout.setSpacing(8)

        self.enable_qc_check = QCheckBox("Enable Auto QC during pipeline")
        self.enable_qc_check.setChecked(self.config.get("enable_qc", True))
        self.enable_qc_check.toggled.connect(self._toggle_qc_fields)
        qc_layout.addWidget(self.enable_qc_check)

        qc_form = QFormLayout()
        qc_form.setSpacing(6)

        self.enable_docsep = QCheckBox("Auto-remove DOCSEP separator pages")
        self.enable_docsep.setChecked(self.config.get("enable_docsep_removal", True))
        qc_form.addRow("", self.enable_docsep)

        self.enable_blank_rm = QCheckBox("Remove blank pages on Finalize")
        self.enable_blank_rm.setChecked(self.config.get("enable_blank_removal", True))
        qc_form.addRow("", self.enable_blank_rm)

        self.qc_blank_spin = QDoubleSpinBox()
        self.qc_blank_spin.setRange(0.1, 10.0)
        self.qc_blank_spin.setSingleStep(0.1)
        self.qc_blank_spin.setDecimals(1)
        self.qc_blank_spin.setValue(self.config.get("qc_blank_threshold", 1.5))
        self.qc_blank_spin.setFixedWidth(100)
        qc_form.addRow("Blank Ink Ratio %:", self.qc_blank_spin)

        self.qc_rotation_spin = QSpinBox()
        self.qc_rotation_spin.setRange(10, 100)
        self.qc_rotation_spin.setValue(self.config.get("qc_rotation_threshold", 65))
        self.qc_rotation_spin.setFixedWidth(100)
        qc_form.addRow("Rotation Conf. Threshold %:", self.qc_rotation_spin)

        self.qc_mirror_spin = QSpinBox()
        self.qc_mirror_spin.setRange(1, 50)
        self.qc_mirror_spin.setValue(self.config.get("qc_mirror_threshold", 15))
        self.qc_mirror_spin.setFixedWidth(100)
        qc_form.addRow("Mirror Delta Threshold %:", self.qc_mirror_spin)

        qc_layout.addLayout(qc_form)
        layout.addWidget(qc_group)

        layout.addSpacing(30)

        format_group = QGroupBox("Output Filename Format")
        format_layout = QVBoxLayout(format_group)
        format_layout.addWidget(QLabel("{YYYYMM}{SEQ}_{DIVISION}_{Company_Name}.pdf"))
        example = QLabel("Example: 2026010001031_Pru_Life_Uk_Insurance_Corp.pdf")
        example.setStyleSheet("color: #8888aa;")
        format_layout.addWidget(example)
        layout.addWidget(format_group)

        layout.addStretch()

    def _toggle_qc_fields(self, enabled: bool):
        self.enable_docsep.setEnabled(enabled)
        self.enable_blank_rm.setEnabled(enabled)
        self.qc_blank_spin.setEnabled(enabled)
        self.qc_rotation_spin.setEnabled(enabled)
        self.qc_mirror_spin.setEnabled(enabled)

    def save_settings(self):
        self.config["confidence_threshold"] = self.conf_spin.value()
        self.config["page_index"] = self.page_spin.value()
        self.config["earliest_year"] = self.year_spin.value()
        self.config["ocr_engine"] = self.ocr_combo.currentText()
        self.config["render_dpi"] = self.dpi_spin.value()
        self.config["enable_qc"] = self.enable_qc_check.isChecked()
        self.config["enable_docsep_removal"] = self.enable_docsep.isChecked()
        self.config["enable_blank_removal"] = self.enable_blank_rm.isChecked()
        self.config["qc_blank_threshold"] = self.qc_blank_spin.value()
        self.config["qc_rotation_threshold"] = self.qc_rotation_spin.value()
        self.config["qc_mirror_threshold"] = self.qc_mirror_spin.value()

        base = Path(__file__).parent.parent
        config_file = base / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        QMessageBox.information(self, "Saved", "Settings saved successfully")
