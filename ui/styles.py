"""Application theme definitions and stylesheet generators.

Supports 'Dark Mode' (default) and 'Light Mode'.
"""

from PyQt6.QtGui import QPalette, QColor

THEME_DARK = "Dark Mode"
THEME_LIGHT = "Light Mode"

PALETTES = {
    THEME_DARK: {
        "name": THEME_DARK,
        "bg_window": "#13142b",
        "bg_surface": "#1a1b2e",
        "bg_card": "#252640",
        "bg_card_hover": "#2d3055",
        "bg_card_selected": "#2a4a7a",
        "bg_card_reviewed": "#1a3a2a",
        "bg_card_duplicate": "#1a3a4a",
        "bg_tab": "#1e1f38",
        "bg_tab_selected": "#2a2b48",
        "bg_tab_hover": "#282945",
        "bg_input": "#1a1b30",
        "bg_input_alt": "#12131f",
        "bg_button": "#2d3a6d",
        "bg_button_hover": "#3d4a7d",
        "bg_button_pressed": "#1d2a5d",
        "bg_preview_scroll": "#12131f",
        "bg_tree": "#1a1e2e",
        "bg_tree_selected": "#1a2a3a",
        "bg_tree_hover": "#1e2535",
        "text_main": "#f0f2ff",
        "text_secondary": "#a8b4cc",
        "text_faint": "#78849e",
        "text_placeholder": "#6b7794",
        "text_selected_tab": "#ffffff",
        "text_unselected_tab": "#98a2be",
        "text_accent": "#4fc3f7",
        "border": "#2d2e45",
        "border_subtle": "#2a2b45",
        "border_input": "#333455",
        "border_focus": "#5a7fb5",
        "accent_primary": "#4a6fa5",
        "accent_primary_hover": "#5a7fb5",
        "accent_success": "#2d6a3f",
        "accent_success_hover": "#3d7a4f",
        "accent_danger": "#6a2d2d",
        "accent_danger_hover": "#7a3d3d",
        "scrollbar_handle": "#333455",
        "scrollbar_handle_hover": "#444566",
        "status_done": "#4caf50",
        "status_todo": "#ff9800",
        "status_bad": "#ff4444",
        "status_warn": "#ffab00",
        "card_border_normal": "#3a3b55",
        "card_border_selected": "#4a6fa5",
        "card_border_reviewed": "#2d6a3f",
        "card_border_duplicate": "#00bcd4",
        "card_title_color": "#ffffff",
        "card_dup_title": "#00bcd4",
    },
    THEME_LIGHT: {
        "name": THEME_LIGHT,
        "bg_window": "#f0f2f7",
        "bg_surface": "#ffffff",
        "bg_card": "#ffffff",
        "bg_card_hover": "#f8f9fd",
        "bg_card_selected": "#d8e6fa",
        "bg_card_reviewed": "#def3e4",
        "bg_card_duplicate": "#d6f4f8",
        "bg_tab": "#e0e4ee",
        "bg_tab_selected": "#ffffff",
        "bg_tab_hover": "#eaedf5",
        "bg_input": "#ffffff",
        "bg_input_alt": "#ffffff",
        "bg_button": "#2b56a3",
        "bg_button_hover": "#3a68bc",
        "bg_button_pressed": "#1f4282",
        "bg_preview_scroll": "#e4e8f1",
        "bg_tree": "#ffffff",
        "bg_tree_selected": "#d8e6fa",
        "bg_tree_hover": "#f8f9fd",
        "text_main": "#0a0e1a",
        "text_secondary": "#1a243a",
        "text_faint": "#283550",
        "text_placeholder": "#485675",
        "text_selected_tab": "#0a0e1a",
        "text_unselected_tab": "#1a243a",
        "text_accent": "#0e52b8",
        "border": "#b4bed2",
        "border_subtle": "#cad2e2",
        "border_input": "#8e9bb5",
        "border_focus": "#2563eb",
        "accent_primary": "#2563eb",
        "accent_primary_hover": "#3b82f6",
        "accent_success": "#15803d",
        "accent_success_hover": "#16a34a",
        "accent_danger": "#b91c1c",
        "accent_danger_hover": "#dc2626",
        "scrollbar_handle": "#9aa6bf",
        "scrollbar_handle_hover": "#7d8ba8",
        "status_done": "#15803d",
        "status_todo": "#c2410c",
        "status_bad": "#b91c1c",
        "status_warn": "#b45309",
        "card_border_normal": "#b8c2d6",
        "card_border_selected": "#2563eb",
        "card_border_reviewed": "#15803d",
        "card_border_duplicate": "#0891b2",
        "card_title_color": "#0a0e1a",
        "card_dup_title": "#0e7490",
    }
}


def get_palette_dict(theme_name: str = THEME_DARK) -> dict:
    if theme_name in ("Light Mode", "Light", "light"):
        return PALETTES[THEME_LIGHT]
    return PALETTES[THEME_DARK]


def get_theme_stylesheet(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    return f"""
QMainWindow, QWidget {{
    background-color: {p['bg_window']};
    color: {p['text_main']};
    font-family: 'Segoe UI', sans-serif;
    font-size: 9pt;
}}

QTabWidget::pane {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    background-color: {p['bg_window']};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {p['bg_tab']};
    color: {p['text_unselected_tab']};
    padding: 8px 20px;
    margin-right: 1px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: bold;
    font-size: 9pt;
    min-width: 100px;
}}

QTabBar::tab:selected {{
    background-color: {p['bg_tab_selected']};
    color: {p['text_selected_tab']};
    border-bottom: 2px solid {p['accent_primary']};
}}

QTabBar::tab:hover:!selected {{
    background-color: {p['bg_tab_hover']};
    color: {p['text_main']};
}}

QGroupBox {{
    border: 1px solid {p['border']};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
    font-size: 9pt;
    color: {p['text_main']};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {p['text_main']};
    font-weight: bold;
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {p['bg_input']};
    border: 1px solid {p['border_input']};
    border-radius: 4px;
    padding: 6px 10px;
    color: {p['text_main']};
    selection-background-color: {p['accent_primary']};
    min-height: 18px;
    font-size: 9pt;
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p['border_focus']};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p['text_secondary']};
    margin-right: 6px;
}}

QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    border: 1px solid {p['border_input']};
    background-color: {p['bg_input']};
    border-radius: 3px;
    width: 18px;
    subcontrol-origin: border;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {p['text_secondary']};
    margin-bottom: 2px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p['text_secondary']};
    margin-top: 2px;
}}

QComboBox {{
    background-color: {p['bg_input']};
    border: 1px solid {p['border_input']};
    border-radius: 6px;
    padding: 5px 10px;
    color: {p['text_main']};
    min-height: 22px;
    font-size: 9pt;
}}

QComboBox:focus, QComboBox:hover {{
    border: 1px solid {p['border_focus']};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    background-color: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {p['text_secondary']};
    margin-right: 6px;
}}

QComboBox QAbstractItemView,
QComboBox QListView,
QComboBoxPrivateContainer {{
    background-color: {p['bg_surface']};
    background: {p['bg_surface']};
    color: {p['text_main']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    selection-background-color: {p['bg_card_selected']};
    selection-color: {p['text_main']};
    outline: none;
    padding: 4px;
}}

QComboBox QAbstractItemView::item {{
    background-color: transparent;
    color: {p['text_main']};
    min-height: 24px;
    padding: 4px 8px;
    border-radius: 4px;
}}

QComboBox QAbstractItemView::item:hover,
QComboBox QAbstractItemView::item:selected {{
    background-color: {p['bg_card_selected']};
    color: {p['text_main']};
}}

QPushButton {{
    background-color: {p['bg_button']};
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 9pt;
    min-height: 18px;
}}

QPushButton:hover {{
    background-color: {p['bg_button_hover']};
}}

QPushButton:pressed {{
    background-color: {p['bg_button_pressed']};
}}

QPushButton:checked {{
    background-color: {p['accent_primary']};
    color: #ffffff;
}}

QPushButton:disabled {{
    background-color: {p['bg_tab']};
    color: {p['text_placeholder']};
}}

QPushButton#accent {{
    background-color: {p['accent_primary']};
}}

QPushButton#accent:hover {{
    background-color: {p['accent_primary_hover']};
}}

QPushButton#success {{
    background-color: {p['accent_success']};
}}

QPushButton#success:hover {{
    background-color: {p['accent_success_hover']};
}}

QPushButton#danger {{
    background-color: {p['accent_danger']};
}}

QPushButton#danger:hover {{
    background-color: {p['accent_danger_hover']};
}}

QProgressBar {{
    border: 1px solid {p['border_input']};
    border-radius: 4px;
    text-align: center;
    color: {p['text_main']};
    background-color: {p['bg_input']};
    min-height: 18px;
    font-size: 8pt;
}}

QProgressBar::chunk {{
    border-radius: 3px;
    background: {p['accent_primary']};
}}

QTextEdit {{
    background-color: {p['bg_input_alt']};
    border: 1px solid {p['border']};
    border-radius: 4px;
    color: {p['text_main']};
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 8pt;
    padding: 6px;
}}

QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background-color: {p['scrollbar_handle']};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {p['scrollbar_handle_hover']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal {{
    background-color: {p['scrollbar_handle']};
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QLabel {{
    background-color: transparent;
    color: {p['text_main']};
    font-size: 9pt;
}}

QListWidget {{
    background-color: {p['bg_window']};
    border: 1px solid {p['border']};
    border-radius: 4px;
    outline: none;
    padding: 2px;
}}

QListWidget::item {{
    border-radius: 6px;
    padding: 0px;
    margin: 1px 2px;
}}

QListWidget::item:selected {{
    background-color: transparent;
    border: none;
}}

QListWidget::item:hover:!selected {{
    background-color: transparent;
}}

QStatusBar {{
    background-color: {p['bg_window']};
    color: {p['text_secondary']};
    border-top: 1px solid {p['border']};
    font-size: 8pt;
}}

QTableWidget {{
    background-color: {p['bg_surface']};
    alternate-background-color: {p['bg_card_hover']};
    color: {p['text_main']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    gridline-color: transparent;
    font-size: 8pt;
}}

QTableWidget::item {{
    padding: 3px 6px;
    border: none;
    min-height: 22px;
}}

QTableWidget::item:selected {{
    background-color: {p['bg_card_selected']};
}}

QHeaderView::section {{
    background-color: {p['bg_tab']};
    color: {p['text_secondary']};
    border: none;
    border-bottom: 1px solid {p['border']};
    padding: 4px 6px;
    font-weight: bold;
    font-size: 7pt;
    min-height: 20px;
}}
"""


def get_theme_palette(theme_name: str = THEME_DARK) -> QPalette:
    p = get_palette_dict(theme_name)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(p["bg_window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p["text_main"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(p["bg_surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p["bg_card_hover"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["bg_card"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p["text_main"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(p["text_main"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(p["bg_button"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(p["text_accent"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(p["accent_primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return palette


def get_stat_card_style(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    return f"""
        QFrame {{
            background-color: {p['bg_surface']};
            border: 1px solid {p['border']};
            border-radius: 10px;
        }}
        QFrame:hover {{
            border: 1px solid {p['border_focus']};
            background-color: {p['bg_card_hover']};
        }}
    """


def get_dashboard_card_style(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    return f"""
        QFrame#dashboard_card {{
            background-color: {p['bg_surface']};
            border: 1px solid {p['border']};
            border-radius: 10px;
        }}
    """


def get_badge_pill_style(color: str, bg_tint: str) -> str:
    return f"""
        QLabel {{
            background-color: {bg_tint};
            color: {color};
            border-radius: 9px;
            padding: 2px 10px;
            font-size: 7.5pt;
            font-weight: 700;
        }}
    """


def get_primary_btn_style(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    return f"""
        QPushButton {{
            background-color: {p['accent_primary']};
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 7px 20px;
            font-weight: 600;
            font-size: 9pt;
        }}
        QPushButton:hover {{
            background-color: {p['accent_primary_hover']};
        }}
        QPushButton:pressed {{
            background-color: {p['bg_button_pressed']};
        }}
    """


def get_secondary_btn_style(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    bg = "#ffffff" if theme_name in ("Light Mode", "Light", "light") else p["bg_input"]
    border = "#c4c2d7" if theme_name in ("Light Mode", "Light", "light") else p["border_input"]
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {p['text_main']};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 6px 14px;
            font-weight: bold;
            font-size: 9pt;
            min-height: 20px;
        }}
        QPushButton:hover {{
            background-color: {p['accent_primary']};
            border: 1px solid {p['accent_primary']};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {p['bg_button_pressed']};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: {p['bg_tab']};
            color: {p['text_placeholder']};
            border: 1px solid {p['border']};
        }}
    """


def get_combobox_popup_style(theme_name: str = THEME_DARK) -> str:
    p = get_palette_dict(theme_name)
    return f"""
        QListView, QAbstractItemView {{
            background-color: {p['bg_surface']};
            background: {p['bg_surface']};
            color: {p['text_main']};
            border: 1px solid {p['border']};
            border-radius: 6px;
            outline: none;
            padding: 4px;
            selection-background-color: {p['bg_card_selected']};
            selection-color: {p['text_main']};
        }}
        QListView::item, QAbstractItemView::item {{
            background-color: transparent;
            color: {p['text_main']};
            min-height: 26px;
            padding: 4px 10px;
            border-radius: 4px;
        }}
        QListView::item:hover, QAbstractItemView::item:hover,
        QListView::item:selected, QAbstractItemView::item:selected {{
            background-color: {p['bg_card_selected']};
            color: {p['text_main']};
        }}
    """


# Backward compatibility
DARK_THEME = get_theme_stylesheet(THEME_DARK)
