import json
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import BASE_DIR

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QMessageBox, QGroupBox,
    QFormLayout, QScrollArea, QLineEdit
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
        settings_outer = QHBoxLayout(settings_group)
        settings_outer.setSpacing(30)

        left_form = QFormLayout()
        left_form.setSpacing(10)

        self.conf_spin = QSpinBox()
        self.conf_spin.setRange(0, 100)
        self.conf_spin.setValue(self.config.get("confidence_threshold", 20))
        self.conf_spin.setFixedWidth(120)
        left_form.addRow("Confidence Threshold (0-100):", self.conf_spin)

        self.page_spin = QSpinBox()
        self.page_spin.setRange(0, 100)
        self.page_spin.setValue(self.config.get("page_index", 0))
        self.page_spin.setFixedWidth(120)
        left_form.addRow("PDF Page Index (0 = first):", self.page_spin)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1900, 2100)
        self.year_spin.setValue(self.config.get("earliest_year", 1950))
        self.year_spin.setFixedWidth(120)
        left_form.addRow("Earliest Valid Year:", self.year_spin)

        right_form = QFormLayout()
        right_form.setSpacing(10)

        self.ocr_combo = QComboBox()
        self.ocr_combo.addItems(["tesseract", "paddleocr"])
        self.ocr_combo.setCurrentText(self.config.get("ocr_engine", "tesseract"))
        self.ocr_combo.setFixedWidth(120)
        right_form.addRow("OCR Engine:", self.ocr_combo)

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setSingleStep(10)
        self.dpi_spin.setValue(self.config.get("render_dpi", 150))
        self.dpi_spin.setFixedWidth(120)
        right_form.addRow("Render DPI (72-600):", self.dpi_spin)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(self.config.get("max_workers", 4))
        self.workers_spin.setFixedWidth(120)
        right_form.addRow("Parallel Workers (1-16):", self.workers_spin)

        settings_outer.addLayout(left_form)
        settings_outer.addLayout(right_form)

        layout.addWidget(settings_group)

        gpu_group = QGroupBox("GPU / Acceleration")
        gpu_outer = QHBoxLayout(gpu_group)
        gpu_outer.setSpacing(30)

        gpu_left = QFormLayout()
        gpu_left.setSpacing(10)

        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(["CPU Only", "Local GPU (CUDA)", "Remote GPU Server"])
        current_gpu = self.config.get("gpu_mode", "cpu")
        gpu_map = {"cpu": "CPU Only", "local_gpu": "Local GPU (CUDA)", "remote": "Remote GPU Server"}
        self.gpu_combo.setCurrentText(gpu_map.get(current_gpu, "CPU Only"))
        self.gpu_combo.setFixedWidth(200)
        self.gpu_combo.currentTextChanged.connect(self._toggle_gpu_fields)
        gpu_left.addRow("Acceleration Mode:", self.gpu_combo)

        self.remote_url_input = QLineEdit()
        self.remote_url_input.setPlaceholderText("http://your-gpu-server:8000")
        self.remote_url_input.setText(self.config.get("remote_gpu_url", ""))
        self.remote_url_input.setFixedWidth(300)
        gpu_left.addRow("Remote Server URL:", self.remote_url_input)

        gpu_right = QFormLayout()
        gpu_right.setSpacing(10)

        self.detect_gpu_btn = QPushButton("Detect CUDA")
        self.detect_gpu_btn.setFixedWidth(120)
        self.detect_gpu_btn.clicked.connect(self._detect_cuda)
        gpu_right.addRow("", self.detect_gpu_btn)

        self.gpu_status_label = QLabel("")
        self.gpu_status_label.setStyleSheet("color: #8888aa;")
        gpu_right.addRow("", self.gpu_status_label)

        gpu_outer.addLayout(gpu_left)
        gpu_outer.addLayout(gpu_right)

        layout.addWidget(gpu_group)

        self._toggle_gpu_fields(self.gpu_combo.currentText())

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setObjectName("accent")
        self.save_btn.setFixedWidth(150)
        self.save_btn.clicked.connect(self.save_settings)
        layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addSpacing(30)

        qc_group = QGroupBox("Auto QC")
        qc_outer = QVBoxLayout(qc_group)
        qc_outer.setSpacing(8)

        self.enable_qc_check = QCheckBox("Enable Auto QC during pipeline")
        self.enable_qc_check.setChecked(self.config.get("enable_qc", True))
        self.enable_qc_check.toggled.connect(self._toggle_qc_fields)
        qc_outer.addWidget(self.enable_qc_check)

        qc_cols = QHBoxLayout()
        qc_cols.setSpacing(30)

        qc_left = QFormLayout()
        qc_left.setSpacing(6)

        self.enable_docsep = QCheckBox("Auto-remove DOCSEP separator pages")
        self.enable_docsep.setChecked(self.config.get("enable_docsep_removal", True))
        qc_left.addRow("", self.enable_docsep)

        self.qc_blank_spin = QDoubleSpinBox()
        self.qc_blank_spin.setRange(0.1, 10.0)
        self.qc_blank_spin.setSingleStep(0.1)
        self.qc_blank_spin.setDecimals(1)
        self.qc_blank_spin.setValue(self.config.get("qc_blank_threshold", 1.5))
        self.qc_blank_spin.setFixedWidth(100)
        qc_left.addRow("Blank Ink Ratio %:", self.qc_blank_spin)

        self.qc_rotation_spin = QSpinBox()
        self.qc_rotation_spin.setRange(10, 100)
        self.qc_rotation_spin.setValue(self.config.get("qc_rotation_threshold", 65))
        self.qc_rotation_spin.setFixedWidth(100)
        qc_left.addRow("Rotation Conf. Threshold %:", self.qc_rotation_spin)

        qc_right = QFormLayout()
        qc_right.setSpacing(6)

        self.enable_blank_rm = QCheckBox("Remove blank pages on Finalize")
        self.enable_blank_rm.setChecked(self.config.get("enable_blank_removal", True))
        qc_right.addRow("", self.enable_blank_rm)

        self.qc_mirror_spin = QSpinBox()
        self.qc_mirror_spin.setRange(1, 50)
        self.qc_mirror_spin.setValue(self.config.get("qc_mirror_threshold", 15))
        self.qc_mirror_spin.setFixedWidth(100)
        qc_right.addRow("Mirror Delta Threshold %:", self.qc_mirror_spin)

        qc_cols.addLayout(qc_left)
        qc_cols.addLayout(qc_right)
        qc_outer.addLayout(qc_cols)

        layout.addWidget(qc_group)

        enhance_group = QGroupBox("Image Enhancement")
        enhance_outer = QVBoxLayout(enhance_group)
        enhance_outer.setSpacing(8)

        self.enhance_enable_check = QCheckBox("Enable Auto Enhancement (right-click on page)")
        self.enhance_enable_check.setChecked(self.config.get("enhance_enabled", True))
        enhance_outer.addWidget(self.enhance_enable_check)

        enhance_cols = QHBoxLayout()
        enhance_cols.setSpacing(30)

        enhance_left = QFormLayout()
        enhance_left.setSpacing(6)

        self.enhance_dpi_spin = QSpinBox()
        self.enhance_dpi_spin.setRange(0, 600)
        self.enhance_dpi_spin.setSpecialValueText("Use pipeline default")
        self.enhance_dpi_spin.setValue(self.config.get("enhance_dpi", 0))
        self.enhance_dpi_spin.setFixedWidth(100)
        enhance_left.addRow("DPI Override (0=default):", self.enhance_dpi_spin)

        self.enhance_denoise_spin = QSpinBox()
        self.enhance_denoise_spin.setRange(0, 20)
        self.enhance_denoise_spin.setValue(self.config.get("enhance_denoise_strength", 5))
        self.enhance_denoise_spin.setFixedWidth(100)
        enhance_left.addRow("Denoise Strength (0-20):", self.enhance_denoise_spin)

        enhance_right = QFormLayout()
        enhance_right.setSpacing(6)

        self.enhance_sharpen_spin = QDoubleSpinBox()
        self.enhance_sharpen_spin.setRange(0.0, 2.0)
        self.enhance_sharpen_spin.setSingleStep(0.1)
        self.enhance_sharpen_spin.setDecimals(1)
        self.enhance_sharpen_spin.setValue(self.config.get("enhance_sharpen_amount", 0.5))
        self.enhance_sharpen_spin.setFixedWidth(100)
        enhance_right.addRow("Sharpen Amount (0.0-2.0):", self.enhance_sharpen_spin)

        enhance_cols.addLayout(enhance_left)
        enhance_cols.addLayout(enhance_right)
        enhance_outer.addLayout(enhance_cols)

        layout.addWidget(enhance_group)

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

    def _toggle_gpu_fields(self, text: str):
        is_remote = text == "Remote GPU Server"
        self.remote_url_input.setEnabled(is_remote)
        self.detect_gpu_btn.setEnabled(text != "Remote GPU Server")

    def _detect_cuda(self):
        try:
            import paddle
            has_cuda = paddle.device.is_compiled_with_cuda()
            if has_cuda:
                self.gpu_status_label.setText("CUDA available")
                self.gpu_status_label.setStyleSheet("color: #44aa44;")
            else:
                self.gpu_status_label.setText("CUDA not available - CPU only")
                self.gpu_status_label.setStyleSheet("color: #cc8844;")
        except ImportError:
            self.gpu_status_label.setText("PaddlePaddle not installed")
            self.gpu_status_label.setStyleSheet("color: #cc4444;")

    def save_settings(self):
        self.config["confidence_threshold"] = self.conf_spin.value()
        self.config["page_index"] = self.page_spin.value()
        self.config["earliest_year"] = self.year_spin.value()
        self.config["ocr_engine"] = self.ocr_combo.currentText()
        self.config["render_dpi"] = self.dpi_spin.value()
        self.config["max_workers"] = self.workers_spin.value()
        self.config["enable_qc"] = self.enable_qc_check.isChecked()
        self.config["enable_docsep_removal"] = self.enable_docsep.isChecked()
        self.config["enable_blank_removal"] = self.enable_blank_rm.isChecked()
        self.config["qc_blank_threshold"] = self.qc_blank_spin.value()
        self.config["qc_rotation_threshold"] = self.qc_rotation_spin.value()
        self.config["qc_mirror_threshold"] = self.qc_mirror_spin.value()
        self.config["enhance_enabled"] = self.enhance_enable_check.isChecked()
        self.config["enhance_dpi"] = self.enhance_dpi_spin.value()
        self.config["enhance_denoise_strength"] = self.enhance_denoise_spin.value()
        self.config["enhance_sharpen_amount"] = self.enhance_sharpen_spin.value()

        gpu_text = self.gpu_combo.currentText()
        gpu_save_map = {"CPU Only": "cpu", "Local GPU (CUDA)": "local_gpu", "Remote GPU Server": "remote"}
        self.config["gpu_mode"] = gpu_save_map.get(gpu_text, "cpu")
        self.config["remote_gpu_url"] = self.remote_url_input.text().strip()

        config_file = BASE_DIR / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        QMessageBox.information(self, "Saved", "Settings saved successfully")
