import sys
import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette

sys.path.insert(0, str(Path(__file__).parent))

from ui.styles import DARK_THEME
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_THEME)
    app.setFont(QFont("Segoe UI", 10))

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1a1b2e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#12131f"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1a1b2e"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#252640"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e0e0f0"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0f0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2d3a6d"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#4a9eff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4a6fa5"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    icon_path = Path(__file__).parent / "Lumeed Logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
