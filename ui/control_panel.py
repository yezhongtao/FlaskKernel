"""控制面板：服务状态 + 启动 / 停止 / 重启 / 保活 / 打开浏览器。

所有按钮都把指令写进 data/control.json，由守护进程执行 ——
桌面面板和 Web 控制台走同一条通道，行为完全一致。
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from core import service_control
from core.config import BASE_DIR, load_config
from ui.style import MUTED, status_color


class StatWidget(QFrame):
    """一个小统计块：标题 + 大号数值。"""

    def __init__(self, title: str, value: str = "-", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("Muted")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Value")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class ControlPanel(QWidget):
    """控制页。"""

    status_changed = Signal(dict)

    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self.port = int(port)
        self.cfg = load_config()
        self._build_ui()
        self._start_timer()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # 顶部状态卡
        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 14)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        app_name = self.cfg.get("app", {}).get("name", "Flask微内核")
        title = QLabel(app_name)
        title.setObjectName("HeaderTitle")
        self.header_sub = QLabel("正在读取服务状态…")
        self.header_sub.setObjectName("HeaderSub")
        text_box.addWidget(title)
        text_box.addWidget(self.header_sub)
        header_layout.addLayout(text_box)
        header_layout.addStretch(1)

        self.status_label = QLabel("● 读取中")
        self.status_label.setStyleSheet(
            "color:#ffffff;font-weight:700;font-size:14px;background:transparent;"
        )
        header_layout.addWidget(self.status_label)
        root.addWidget(header)

        # 统计块
        grid = QGridLayout()
        grid.setSpacing(10)
        self.stat_state = StatWidget("服务状态", "-")
        self.stat_port = StatWidget("监听端口", str(self.port))
        self.stat_uptime = StatWidget("运行时长", "-")
        self.stat_mode = StatWidget("运行模式", "-")
        for index, widget in enumerate(
            (self.stat_state, self.stat_port, self.stat_uptime, self.stat_mode)
        ):
            grid.addWidget(widget, 0, index)
        root.addLayout(grid)

        # 控制按钮
        buttons = QGridLayout()
        buttons.setSpacing(10)

        self.btn_start = QPushButton("启动服务")
        self.btn_start.setObjectName("Success")
        self.btn_stop = QPushButton("停止服务")
        self.btn_stop.setObjectName("Danger")
        self.btn_restart = QPushButton("重启服务")
        self.btn_restart.setObjectName("Primary")
        self.btn_browser = QPushButton("打开浏览器")
        self.btn_folder = QPushButton("数据目录")
        self.btn_refresh = QPushButton("刷新状态")

        for col, button in enumerate(
            (self.btn_start, self.btn_stop, self.btn_restart,
             self.btn_browser, self.btn_folder, self.btn_refresh)
        ):
            buttons.addWidget(button, 0, col)

        root.addLayout(buttons)

        # 保活开关
        options = QFrame()
        options.setObjectName("Card")
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(16, 12, 16, 12)

        self.keepalive_box = QCheckBox("保活（服务崩溃后自动拉起）")
        self.keepalive_box.setChecked(True)
        options_layout.addWidget(self.keepalive_box)
        options_layout.addSpacing(16)

        # 项目设置：和网页 /admin/settings 同一份 CONFIG_SCHEMA
        self.btn_settings = QPushButton("项目设置")
        self.btn_settings.setToolTip("编辑 config.json 的全部配置项（与网页端一致）")
        options_layout.addWidget(self.btn_settings)
        options_layout.addStretch(1)

        self.safe_mode_label = QLabel("安全模式：关闭")
        self.safe_mode_label.setObjectName("Muted")
        options_layout.addWidget(self.safe_mode_label)
        root.addWidget(options)

        # 说明
        tip = QLabel(
            "提示：按钮通过 data/control.json 下发指令，由守护进程执行；"
            "保活开关会写回 config.json 的 service.keepalive。"
        )
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        root.addWidget(tip)
        root.addStretch(1)

        # 事件
        self.btn_start.clicked.connect(lambda: self._control("start"))
        self.btn_stop.clicked.connect(lambda: self._control("stop"))
        self.btn_restart.clicked.connect(lambda: self._control("restart"))
        self.btn_browser.clicked.connect(self._open_browser)
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_refresh.clicked.connect(self.refresh_status)
        self.keepalive_box.toggled.connect(self._toggle_keepalive)
        self.btn_settings.clicked.connect(self._open_settings)

    def _open_settings(self) -> None:
        from ui.settings_dialog import open_settings

        open_settings(self)
        # 设置里可能改了刷新间隔 / 保活，回来立刻按新配置刷新一次
        self.cfg = load_config()
        self.refresh_status()

    def _start_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        interval = int(self.cfg.get("ui", {}).get("refresh_ms", 2000) or 2000)
        self.timer.start(max(800, interval))
        self.refresh_status()

    # ------------------------------------------------------------- 状态刷新
    def refresh_status(self) -> None:
        try:
            snap = service_control.snapshot()
        except Exception as exc:  # 读不到状态也不能崩
            self.header_sub.setText(f"读取状态失败：{exc}")
            return

        state = snap.get("state", "unknown")
        labels = {"running": "运行中", "stopped": "已停止",
                  "pending": "启动中", "starting": "启动中"}
        label = labels.get(state, state)
        color = status_color(state)

        self.status_label.setText(f"● {label}")
        self.status_label.setStyleSheet(
            f"color:#ffffff;font-weight:700;font-size:14px;"
            f"background:transparent;border-left:0;"
        )
        self.header_sub.setText(
            f"PID {snap.get('app_pid') or '-'} · 端口 {snap.get('port') or self.port}"
            f" · {'守护进程在线' if snap.get('mode') == 'guardian' else '独立运行（无守护）'}"
        )

        self.stat_state.set_value(label)
        self.stat_state.value_label.setStyleSheet(f"color:{color};font-size:18px;font-weight:700;")
        self.stat_port.set_value(str(snap.get("port") or self.port))

        uptime = int(snap.get("uptime_seconds") or 0)
        self.stat_uptime.set_value(self._format_uptime(uptime))
        self.stat_mode.set_value("守护" if snap.get("mode") == "guardian" else "独立")

        keepalive = bool(snap.get("keepalive", True))
        self.keepalive_box.blockSignals(True)
        self.keepalive_box.setChecked(keepalive)
        self.keepalive_box.blockSignals(False)

        self.cfg = load_config()
        safe_mode = bool((self.cfg.get("plugin") or {}).get("safe_mode"))
        self.safe_mode_label.setText(
            "安全模式：<b style='color:#d97706'>已开启</b>" if safe_mode
            else "安全模式：关闭"
        )
        self.safe_mode_label.setTextFormat(Qt.RichText)

        has_guardian = snap.get("mode") == "guardian"
        running = state == "running"
        self.btn_start.setEnabled(has_guardian and not running)
        self.btn_stop.setEnabled(running)
        self.btn_restart.setEnabled(has_guardian)
        self.keepalive_box.setEnabled(has_guardian)

        self.status_changed.emit(snap)

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        if seconds <= 0:
            return "-"
        days, rest = divmod(seconds, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        if days:
            return f"{days}天{hours}时"
        if hours:
            return f"{hours}时{minutes}分"
        return f"{minutes}分"

    # ----------------------------------------------------------------- 操作
    def _control(self, action: str) -> None:
        try:
            service_control.request_control(action, requested_by="desktop-ui")
        except Exception as exc:
            QMessageBox.warning(self, "下发失败", f"无法写入控制指令：{exc}")
            return

        messages = {
            "start": "已下发启动指令",
            "stop": "已下发停止指令",
            "restart": "已下发重启指令",
        }
        self.statusBarMessage(messages.get(action, "已下发指令"))
        QTimer.singleShot(1200, self.refresh_status)

    def statusBarMessage(self, text: str) -> None:
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(text, 4000)

    def _toggle_keepalive(self, enabled: bool) -> None:
        try:
            service_control.set_keepalive(bool(enabled))
            if service_control.guardian_alive():
                service_control.request_control(
                    "keepalive_on" if enabled else "keepalive_off",
                    requested_by="desktop-ui",
                )
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入配置：{exc}")
            return
        self.statusBarMessage("保活已" + ("开启" if enabled else "关闭"))

    def _open_browser(self) -> None:
        snap = service_control.snapshot()
        port = snap.get("port") or self.port
        home = (self.cfg.get("kernel") or {}).get("home_url") or "/"
        webbrowser.open(f"http://127.0.0.1:{port}{home}")

    def _open_folder(self) -> None:
        target = Path(BASE_DIR)
        webbrowser.open(target.as_uri())
