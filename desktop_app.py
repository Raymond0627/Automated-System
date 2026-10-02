import sys
import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent))

from paths import BASE_DIR, DATA_DIR, configure_tesseract
from ui.styles import get_theme_stylesheet, get_theme_palette, THEME_LIGHT
from ui.main_window import MainWindow
import json


def _get_initial_theme() -> str:
    config_file = DATA_DIR / "config.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("theme", THEME_LIGHT)
        except Exception:
            pass
    return THEME_LIGHT


def main():
    configure_tesseract()
    app = QApplication(sys.argv)
    theme = _get_initial_theme()
    app.setStyleSheet(get_theme_stylesheet(theme))
    app.setPalette(get_theme_palette(theme))
    app.setFont(QFont("Segoe UI", 10))
 
    icon_path = BASE_DIR / "Lumeed Logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
