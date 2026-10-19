"""打包工具的通用控件与后台任务。

复用内核 ui/style.py 的 QSS（同一套配色/圆角），这里只补少量表格样式。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

# 工具额外的 QSS（在 ui.style.STYLE_SHEET 之上叠加）
# 注意顺序：先用 transparent 覆盖内核的 QWidget 底色，再给各控件单独上色，
# 否则每个 QWidget 容器都会在白色卡片里留下一块灰色。
EXTRA_QSS = """
QWidget { background-color: transparent; }
QWidget#Root { background: #f5f6fa; }
QMainWindow { background: #f5f6fa; }

QLabel#SectionTitle { font-size: 14px; font-weight: 700; }
QLabel#SectionSub { color: #64748b; font-size: 11.5px; }
QLabel#BlockTitle { font-size: 12.5px; font-weight: 700; color: #1e293b; }
QLabel#Hint { color: #94a3b8; font-size: 11px; }
QLabel#Chip {
    background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 9px;
    padding: 3px 9px; color: #475569; font-size: 11px;
}
QLabel#ChipOk { background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 9px;
    padding: 3px 9px; color: #059669; font-size: 11px; }
QLabel#ChipWarn { background: #fffbeb; border: 1px solid #fde68a; border-radius: 9px;
    padding: 3px 9px; color: #b45309; font-size: 11px; }
QLabel#ChipBad { background: #fef2f2; border: 1px solid #fecaca; border-radius: 9px;
    padding: 3px 9px; color: #dc2626; font-size: 11px; }

QTableWidget {
    background: #ffffff; alternate-background-color: #f8fafc;
    border: 1px solid #e6e9f0; border-radius: 10px;
    gridline-color: #eef1f6; selection-background-color: #eef2ff;
    selection-color: #312e81;
}
QHeaderView::section {
    background: #f8fafc; border: 0; border-bottom: 1px solid #e6e9f0;
    padding: 6px 8px; color: #64748b; font-weight: 600; font-size: 11px;
}
QLineEdit {
    background: #ffffff; border: 1px solid #d3d9e4; border-radius: 8px;
    padding: 6px 9px;
}
QLineEdit:focus { border-color: #4f46e5; }
QLineEdit:read-only { background: #f8fafc; color: #64748b; }

/* 文本区默认深色（日志 / JSON 预览 / 文件清单）；输入型用 #InputText 反白 */
QPlainTextEdit {
    background: #0b1220; color: #cbd5e1;
    border: 1px solid #1b2740; border-radius: 10px;
    padding: 6px 8px;
}
QPlainTextEdit#InputText {
    background: #ffffff; color: #0f172a;
    border: 1px solid #d3d9e4; border-radius: 8px;
    padding: 6px 9px;
}
QPlainTextEdit#InputText:focus { border-color: #4f46e5; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QSplitter::handle { background: #e6e9f0; }
QTableWidget QLineEdit { border: 0; border-radius: 0; padding: 2px 4px; }
QCheckBox { font-size: 12.5px; }
"""


def build_stylesheet(style_sheet: str, check_icon: str | None = None) -> str:
    """内核 QSS + 工具 QSS（可选：勾选框的白色对勾图片）。"""
    extra = EXTRA_QSS
    if check_icon:
        path = check_icon.replace("\\", "/")
        extra += (
            f'\nQCheckBox::indicator:checked {{ image: url("{path}"); }}'
            '\nQCheckBox::indicator:checked:disabled { image: none; }\n'
        )
    return style_sheet + extra


# --------------------------------------------------------------------------- #
# 后台任务：分析 / 内置依赖 / 打包 都可能耗时（pip 尤其）
# --------------------------------------------------------------------------- #
class Task(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(result)


# --------------------------------------------------------------------------- #
# 小组件
# --------------------------------------------------------------------------- #
def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def chip(text: str, name: str = "Chip") -> QLabel:
    return label(text, name)


def field(title: str, widget: QWidget, hint: str = "") -> QWidget:
    """标题 + 控件 + 提示 的竖排组合。"""
    box = QWidget()
    box.setObjectName("FieldBox")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)

    title_label = QLabel(title)
    title_label.setStyleSheet("font-weight:600; font-size:12px; color:#334155;")
    layout.addWidget(title_label)
    layout.addWidget(widget)
    if hint:
        hint_label = label(hint, "Hint", wrap=True)
        layout.addWidget(hint_label)
    return box


def block_title(text: str) -> QLabel:
    """卡片内部的小节标题（比卡片标题小、比提示重）。"""
    return label(text, "BlockTitle")


def button(text: str, role: str = "", slot=None, tip: str = "") -> QPushButton:
    widget = QPushButton(text)
    if role:
        widget.setObjectName(role)     # Primary / Danger / Success
    if tip:
        widget.setToolTip(tip)
    if slot is not None:
        widget.clicked.connect(slot)
    return widget


def card(title: str, subtitle: str = "", tag: str = "") -> tuple[QFrame, QVBoxLayout]:
    """一个卡片（QFrame#Card），返回 (卡片, 内容布局)。"""
    frame = QFrame()
    frame.setObjectName("Card")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(10)

    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(label(title, "SectionTitle"))
    if tag:
        head.addWidget(chip(tag))
    head.addStretch(1)
    outer.addLayout(head)

    if subtitle:
        outer.addWidget(label(subtitle, "SectionSub", wrap=True))

    body = QVBoxLayout()
    body.setSpacing(8)
    outer.addLayout(body)
    return frame, body


def table(headers: list[str], height: int = 110,
          stretch_last: bool = True) -> QTableWidget:
    """统一风格的表格（可直接双击编辑）。"""
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().setVisible(False)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setSelectionMode(QTableWidget.SingleSelection)
    widget.setMaximumHeight(height)
    widget.setMinimumHeight(max(80, height // 2))
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    header = widget.horizontalHeader()
    header.setHighlightSections(False)
    if stretch_last:
        header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.Interactive)
    return widget


def item(text: str = "", mono: bool = False) -> QTableWidgetItem:
    widget = QTableWidgetItem(text)
    if mono:
        font = widget.font()
        font.setFamily("Consolas")
        widget.setFont(font)
    return widget
