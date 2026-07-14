import json
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSpinBox, QComboBox, QMessageBox, QGroupBox, QFormLayout, QScrollArea
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

        layout.addWidget(settings_group)

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("accent")
        self.save_btn.setFixedWidth(150)
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(30)

        format_group = QGroupBox("Output Filename Format")
        format_layout = QVBoxLayout(format_group)
        format_layout.addWidget(QLabel("{YYYYMM}{SEQ}_{DIVISION}_{Company_Name}.pdf"))
        example = QLabel("Example: 2026010001031_Pru_Life_Uk_Insurance_Corp.pdf")
        example.setStyleSheet("color: #8888aa;")
        format_layout.addWidget(example)
        layout.addWidget(format_group)

        layout.addStretch()

    def save_settings(self):
        self.config["confidence_threshold"] = self.conf_spin.value()
        self.config["page_index"] = self.page_spin.value()
        self.config["earliest_year"] = self.year_spin.value()
        self.config["ocr_engine"] = self.ocr_combo.currentText()

        base = Path(__file__).parent.parent
        config_file = base / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        QMessageBox.information(self, "Saved", "Settings saved successfully")
