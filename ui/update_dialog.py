import os
import subprocess
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QMessageBox, QApplication
)
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtCore import Qt

from paths import BASE_DIR
from updater import UpdateDownloadWorker


class UpdateDialog(QDialog):
    """Dialog notifying the user of an available update and providing one-click install & restart."""

    def __init__(self, new_version: str, release_notes: str, download_url: str, parent=None):
        super().__init__(parent)
        self.new_version = new_version
        self.release_notes = release_notes
        self.download_url = download_url
        self._download_worker = None

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Update Available")
        self.setMinimumSize(500, 380)
        self.resize(540, 420)

        icon_path = BASE_DIR / "Lumeed Logo.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Header Title
        title_label = QLabel(f"Lumeed QScan {self.new_version} is available!")
        title_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        layout.addWidget(title_label)

        sub_label = QLabel("A new version is ready to install. Review the release notes below:")
        sub_label.setFont(QFont("Segoe UI", 9))
        sub_label.setStyleSheet("color: #888;")
        layout.addWidget(sub_label)

        # Release notes text
        self.notes_edit = QTextEdit()
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setPlainText(self.release_notes or "Bug fixes and performance improvements.")
        self.notes_edit.setFont(QFont("Segoe UI", 9))
        self.notes_edit.setStyleSheet("border-radius: 4px; padding: 6px;")
        layout.addWidget(self.notes_edit, 1)

        # Progress Section (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Segoe UI", 9))
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # Buttons
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(10)

        self.btn_layout.addStretch()

        self.cancel_btn = QPushButton("Remind Me Later")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.btn_layout.addWidget(self.cancel_btn)

        self.update_btn = QPushButton("Update Now")
        self.update_btn.setFixedHeight(32)
        self.update_btn.setStyleSheet("""
            QPushButton {
                background-color: #26c6da;
                color: #0a0f1a;
                font-weight: bold;
                border-radius: 4px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: #00b4d8;
            }
        """)
        self.update_btn.clicked.connect(self._start_download)
        self.btn_layout.addWidget(self.update_btn)

        layout.addLayout(self.btn_layout)

    def _start_download(self):
        self.update_btn.setEnabled(False)
        self.update_btn.hide()
        self.cancel_btn.setText("Cancel Download")
        self.progress_bar.show()
        self.status_label.show()
        self.status_label.setText("Starting download...")

        self._download_worker = UpdateDownloadWorker(self.download_url, self.new_version, parent=self)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.start()

    def _on_download_progress(self, percent: int, downloaded_mb: float, total_mb: float):
        self.progress_bar.setValue(percent)
        if total_mb > 0:
            self.status_label.setText(f"Downloading... {downloaded_mb:.1f} MB / {total_mb:.1f} MB ({percent}%)")
        else:
            self.status_label.setText(f"Downloading... {downloaded_mb:.1f} MB")

    def _on_download_finished(self, installer_path: str):
        self.status_label.setText("Download complete! Launching installer and restarting...")
        self.progress_bar.setValue(100)
        self.cancel_btn.setEnabled(False)

        # Launch Inno Setup with silent update and application restart flags
        try:
            # Arguments:
            # /SILENT : runs wizard without asking unnecessary questions
            # /CLOSEAPPLICATIONS : cleanly signals LumeedQScan to close
            # /RESTARTAPPLICATIONS : automatically re-opens LumeedQScan once done
            subprocess.Popen([
                installer_path,
                "/SILENT",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS"
            ])
            # Quit our running instance cleanly
            QApplication.instance().quit()
        except Exception as e:
            QMessageBox.critical(self, "Update Error", f"Could not launch installer:\n{e}")
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setText("Close")

    def _on_download_failed(self, error_msg: str):
        QMessageBox.critical(self, "Download Failed", f"Failed to download update:\n{error_msg}")
        self.status_label.setText("Download failed.")
        self.update_btn.show()
        self.update_btn.setEnabled(True)
        self.cancel_btn.setText("Close")

    def _on_cancel(self):
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.cancel()
        self.reject()

    def closeEvent(self, event):
        self._on_cancel()
        super().closeEvent(event)
