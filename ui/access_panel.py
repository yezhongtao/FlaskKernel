"""访问记录面板：读取 data/logs/access-*.log（JSON Lines）。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from core.config import LOGS_DIR, load_config


class AccessPanel(QWidget):
    def __init__(self, parent=None, max_rows: int | None = None):
        super().__init__(parent)
        cfg = load_config()
        ui_cfg = cfg.get("ui") or {}
        self.max_rows = int(max_rows or ui_cfg.get("access_rows", 500) or 500)
        self._pos = 0
        self._path: Path | None = None
        self._paused = False

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 12, 12, 0)

        self.header = QLabel("等待访问日志…")
        self.header.setObjectName("Muted")

        self.btn_pause = QPushButton("暂停")
        self.btn_clear = QPushButton("清空显示")

        bar.addWidget(self.header)
        bar.addStretch(1)
        bar.addWidget(self.btn_pause)
        bar.addWidget(self.btn_clear)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.max_rows)
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
        self.timer.start(1500)
        self.poll()

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def _latest(self) -> Path | None:
        files = sorted(Path(LOGS_DIR).glob("access-*.log"))
        return files[-1] if files else None

    def poll(self) -> None:
        if self._paused:
            return
        latest = self._latest()
        if latest is None:
            self.header.setText("今天还没有访问日志")
            return
        if self._path != latest:
            self._path = latest
            self._pos = 0
            self.view.clear()
            self.header.setText(f"读取：{latest.name}（最多 {self.max_rows} 条）")

        try:
            size = latest.stat().st_size
            if size < self._pos:
                self._pos = 0
                self.view.clear()
            if size == self._pos:
                return
            with open(latest, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                lines = f.readlines()
                self._pos = f.tell()
        except OSError:
            return

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.view.appendPlainText(self._format(record))

        self.view.verticalScrollBar().setValue(
            self.view.verticalScrollBar().maximum()
        )

    @staticmethod
    def _format(record: dict) -> str:
        moment = record.get("time", "")
        if record.get("type") == "http":
            return (f"[{moment}] {record.get('ip', '-'):<15} "
                    f"{record.get('user', '-'):<12} "
                    f"{record.get('method', '-'):<6} {record.get('status', '-'):<4} "
                    f"{record.get('duration_ms', 0):>7.1f}ms  {record.get('path', '')}")
        if record.get("type") == "plugin_call":
            ok = "OK " if record.get("ok") else "ERR"
            return (f"[{moment}] {ok} {record.get('caller', '-'):<28} -> "
                    f"{record.get('provider', '-'):<28} "
                    f"{record.get('service', '')}.{record.get('method', '')} "
                    f"({record.get('duration_ms', 0):.1f}ms)")
        return json.dumps(record, ensure_ascii=False)
