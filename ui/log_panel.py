"""日志面板：增量读取 data/logs/app.log 尾部。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from core.config import LOGS_DIR, load_config


class LogPanel(QWidget):
    def __init__(self, parent=None, filename: str = "app.log",
                 max_blocks: int = 5000):
        super().__init__(parent)
        self.path = Path(LOGS_DIR) / filename
        self.max_blocks = max_blocks
        self._pos = 0
        self._paused = False

        cfg = load_config()
        interval = max(500, int((cfg.get("ui") or {}).get("refresh_ms", 2000) or 2000))

        # 工具条
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 12, 12, 0)
        self.header = QLabel(f"读取：{self.path.name}")
        self.header.setObjectName("Muted")

        self.btn_pause = QPushButton("暂停")
        self.btn_clear = QPushButton("清空显示")
        self.auto_scroll = QCheckBox("自动滚动")
        self.auto_scroll.setChecked(True)

        bar.addWidget(self.header)
        bar.addStretch(1)
        bar.addWidget(self.btn_pause)
        bar.addWidget(self.btn_clear)
        bar.addWidget(self.auto_scroll)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(max_blocks)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(bar)
        layout.addWidget(self.view)

        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_clear.clicked.connect(self.view.clear)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(interval)
        self.poll()

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def poll(self) -> None:
        if self._paused:
            return
        try:
            if not self.path.exists():
                return
            size = self.path.stat().st_size
            if size < self._pos:
                self._pos = 0
                self.view.clear()
            if size == self._pos:
                return
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                data = f.read()
                self._pos = f.tell()
            if data:
                self.view.appendPlainText(data.rstrip("\n"))
                if self.auto_scroll.isChecked():
                    self.view.verticalScrollBar().setValue(
                        self.view.verticalScrollBar().maximum()
                    )
        except OSError:
            pass
