"""PySide6 全局样式（QSS）—— **复制自项目内核的 ui/style.py**。

工具是自包含的：不 import 项目里的任何模块，所以这里放一份副本。
以后想改主题就改这里（想和内核保持一致时，两边一起改）。
"""
from __future__ import annotations

BRAND = "#4f46e5"
BRAND_DARK = "#4338ca"
BG = "#f5f6fa"
SURFACE = "#ffffff"
BORDER = "#e6e9f0"
TEXT = "#0f172a"
MUTED = "#64748b"
SUCCESS = "#059669"
DANGER = "#dc2626"
WARN = "#d97706"

STYLE_SHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{ background: {BG}; }}

QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: 19px; font-weight: 700; }}
QLabel#Subtitle {{ color: {MUTED}; font-size: 12px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Value {{ font-size: 20px; font-weight: 700; }}
QLabel#StatusOk {{ color: {SUCCESS}; font-weight: 700; }}
QLabel#StatusBad {{ color: {DANGER}; font-weight: 700; }}
QLabel#StatusWarn {{ color: {WARN}; font-weight: 700; }}

QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#Header {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4f46e5, stop:1 #7c3aed);
    border-radius: 12px;
}}
QLabel#HeaderTitle {{ color: #ffffff; font-size: 17px; font-weight: 700; }}
QLabel#HeaderSub {{ color: #dbe3ff; font-size: 12px; }}

QPushButton {{
    background: {SURFACE};
    border: 1px solid #d3d9e4;
    border-radius: 9px;
    padding: 7px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {BRAND}; color: {BRAND}; }}
QPushButton:pressed {{ background: #eef2ff; }}
QPushButton:disabled {{ color: #9aa6b8; background: #f1f3f7; }}

QPushButton#Primary {{
    background: {BRAND}; border: 1px solid {BRAND}; color: #ffffff; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {BRAND_DARK}; border-color: {BRAND_DARK}; color: #fff; }}

QPushButton#Danger {{
    background: #fef2f2; border: 1px solid #fecaca; color: {DANGER}; font-weight: 600;
}}
QPushButton#Danger:hover {{ background: {DANGER}; color: #ffffff; border-color: {DANGER}; }}

QPushButton#Success {{
    background: #ecfdf5; border: 1px solid #a7f3d0; color: {SUCCESS}; font-weight: 600;
}}
QPushButton#Success:hover {{ background: {SUCCESS}; color: #ffffff; border-color: {SUCCESS}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 12px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 8px 16px; margin-right: 4px;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ background: {SURFACE}; color: {BRAND}; font-weight: 600; border: 1px solid {BORDER}; border-bottom: 1px solid {SURFACE}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QPlainTextEdit, QTextEdit {{
    background: #0b1220; color: #cbd5e1;
    border: 1px solid #1b2740; border-radius: 10px;
    font-family: "Cascadia Code", Consolas, monospace; font-size: 12px;
    selection-background-color: {BRAND};
}}

QCheckBox {{ padding: 4px 2px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1px solid #c3cbd9; background: #ffffff; }}
QCheckBox::indicator:checked {{ background: {BRAND}; border-color: {BRAND}; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ width: 9px; background: transparent; }}
QScrollBar::handle:vertical {{ background: #cbd3e1; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: #0f172a; color: #e2e8f0; border: 0; padding: 4px 8px; border-radius: 6px; }}
"""


def status_color(state: str) -> str:
    """按服务状态返回颜色（表格 / 徽章用）。"""
    return {
        "running": SUCCESS,
        "stopped": DANGER,
        "pending": WARN,
    }.get(state or "", MUTED)
