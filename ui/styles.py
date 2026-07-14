DARK_THEME = """
QMainWindow, QWidget {
    background-color: #13142b;
    color: #e0e0f0;
    font-family: 'Segoe UI', sans-serif;
    font-size: 9pt;
}

QTabWidget::pane {
    border: 1px solid #2d2e45;
    border-radius: 6px;
    background-color: #13142b;
    top: -1px;
}

QTabBar::tab {
    background-color: #1e1f38;
    color: #7a7a9a;
    padding: 8px 20px;
    margin-right: 1px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: bold;
    font-size: 9pt;
    min-width: 100px;
}

QTabBar::tab:selected {
    background-color: #2a2b48;
    color: #ffffff;
    border-bottom: 2px solid #6a8fc5;
}

QTabBar::tab:hover:!selected {
    background-color: #282945;
    color: #bbbbdd;
}

QGroupBox {
    border: 1px solid #2a2b45;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
    font-size: 9pt;
    color: #7a7a9a;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}

QLineEdit, QSpinBox, QComboBox {
    background-color: #1a1b30;
    border: 1px solid #333455;
    border-radius: 4px;
    padding: 6px 10px;
    color: #e0e0f0;
    selection-background-color: #4a6fa5;
    min-height: 18px;
    font-size: 9pt;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #5a7fb5;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #7a7a9a;
    margin-right: 6px;
}

QSpinBox::up-button, QSpinBox::down-button {
    border: 1px solid #333455;
    background-color: #1a1b30;
    border-radius: 3px;
    width: 18px;
    subcontrol-origin: border;
}

QSpinBox::up-button {
    subcontrol-position: top right;
}

QSpinBox::down-button {
    subcontrol-position: bottom right;
}

QSpinBox::up-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #7a7a9a;
    margin-bottom: 2px;
}

QSpinBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #7a7a9a;
    margin-top: 2px;
}

QComboBox QAbstractItemView {
    background-color: #1a1b30;
    border: 1px solid #333455;
    selection-background-color: #2a3a5a;
    color: #e0e0f0;
}

QPushButton {
    background-color: #2d3a6d;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 9pt;
    min-height: 18px;
}

QPushButton:hover {
    background-color: #3d4a7d;
}

QPushButton:pressed {
    background-color: #1d2a5d;
}

QPushButton:disabled {
    background-color: #1e1f38;
    color: #555570;
}

QPushButton#accent {
    background-color: #4a6fa5;
}

QPushButton#accent:hover {
    background-color: #5a7fb5;
}

QPushButton#success {
    background-color: #2d6a3f;
}

QPushButton#success:hover {
    background-color: #3d7a4f;
}

QPushButton#danger {
    background-color: #6a2d2d;
}

QPushButton#danger:hover {
    background-color: #7a3d3d;
}

QProgressBar {
    border: 1px solid #333455;
    border-radius: 4px;
    text-align: center;
    color: #ffffff;
    background-color: #1a1b30;
    min-height: 18px;
    font-size: 8pt;
}

QProgressBar::chunk {
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4a6fa5, stop:1 #6a8fc5);
}

QTextEdit {
    background-color: #0d0e1a;
    border: 1px solid #2a2b45;
    border-radius: 4px;
    color: #a0ff90;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 8pt;
    padding: 6px;
}

QScrollArea {
    border: none;
    background-color: transparent;
}

QScrollBar:vertical {
    background-color: #13142b;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background-color: #333455;
    border-radius: 4px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background-color: #444566;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #13142b;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background-color: #333455;
    border-radius: 4px;
    min-width: 24px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QLabel {
    color: #d0d0e8;
    font-size: 9pt;
}

QListWidget {
    background-color: #13142b;
    border: 1px solid #2a2b45;
    border-radius: 4px;
    outline: none;
    padding: 2px;
}

QListWidget::item {
    border-radius: 6px;
    padding: 0px;
    margin: 1px 2px;
}

QListWidget::item:selected {
    background-color: transparent;
    border: none;
}

QListWidget::item:hover:!selected {
    background-color: transparent;
}

QStatusBar {
    background-color: #0d0e1a;
    color: #6666aa;
    border-top: 1px solid #2a2b45;
    font-size: 8pt;
}

QTableWidget {
    background-color: #0d0e1a;
    color: #c0c0d0;
    border: 1px solid #2a2b45;
    border-radius: 4px;
    gridline-color: #1a1b30;
    font-size: 8pt;
}

QTableWidget::item {
    padding: 3px 6px;
    border: none;
    min-height: 22px;
}

QTableWidget::item:selected {
    background-color: #2a3a5a;
}

QHeaderView::section {
    background-color: #111224;
    color: #7778a0;
    border: none;
    border-bottom: 1px solid #2a2b45;
    padding: 4px 6px;
    font-weight: bold;
    font-size: 7pt;
    min-height: 20px;
}
"""
