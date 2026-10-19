"""PySide6 主窗口：控制 / 日志 / 访问记录 + 系统托盘。

窗口行为：
  · 点右上角关闭 → 直接退出程序（守护进程与 Flask 子进程由 main.shutdown 收尾）
  · 点最小化 → 收回托盘后台运行（托盘菜单可再弹出窗口 / 退出）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QLinearGradient,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMenu,
                               QSystemTrayIcon, QTabWidget)

from core.config import RESOURCE_DIR, load_config
from ui.access_panel import AccessPanel
from ui.control_panel import ControlPanel
from ui.log_panel import LogPanel
from ui.settings_dialog import open_settings
from ui.style import STYLE_SHEET


def app_icon() -> QIcon:
    """项目图标：优先用生成的「微」字 ico，缺失时程序化画一个。"""
    ico = RESOURCE_DIR / "static" / "img" / "favicon.ico"
    if ico.is_file():
        icon = QIcon(str(ico))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, 128, 128)
    gradient.setColorAt(0, QColor("#6366f1"))
    gradient.setColorAt(1, QColor("#a855f7"))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, 128, 128, 30, 30)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Microsoft YaHei", 56, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "微")
    painter.end()
    return QIcon(pixmap)


def _make_icon() -> QIcon:
    """兼容旧调用名。"""
    return app_icon()


class MainWindow(QMainWindow):
    def __init__(self, port: int):
        super().__init__()
        self.port = int(port)
        self.cfg = load_config()
        app_name = self.cfg.get("app", {}).get("name", "Flask微内核")

        self.setWindowTitle(f"{app_name} 控制面板 · 端口 {self.port}")
        self.setWindowIcon(_make_icon())
        self.resize(1080, 720)
        self.setMinimumSize(860, 600)
        self.setStyleSheet(STYLE_SHEET)

        self.control = ControlPanel(self.port, self)
        self.log_panel = LogPanel(self)
        self.access_panel = AccessPanel(self)

        tabs = QTabWidget(self)
        tabs.addTab(self.control, "控制")
        tabs.addTab(self.log_panel, "应用日志")
        tabs.addTab(self.access_panel, "访问记录")
        self.setCentralWidget(tabs)

        self.status_label = QLabel("● 读取中")
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().showMessage("配置文件 config.json · 数据目录 data/ · 插件目录 plugins/")

        self.control.status_changed.connect(self._on_status)
        self._build_tray()
        self._tray_hint_shown = False

    # ------------------------------------------------------------------ 托盘
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_icon(), self)
        self.tray.setToolTip(f"{self.cfg.get('app', {}).get('name', 'Flask微内核')} 控制面板")

        menu = QMenu(self)
        action_show = QAction("显示窗口", self)
        action_show.triggered.connect(self._show_window)
        action_start = QAction("启动服务", self)
        action_start.triggered.connect(lambda: self.control._control("start"))
        action_stop = QAction("停止服务", self)
        action_stop.triggered.connect(lambda: self.control._control("stop"))
        action_restart = QAction("重启服务", self)
        action_restart.triggered.connect(lambda: self.control._control("restart"))
        action_browser = QAction("打开浏览器", self)
        action_browser.triggered.connect(self.control._open_browser)
        action_settings = QAction("项目设置…", self)
        action_settings.triggered.connect(lambda: open_settings(self))
        action_quit = QAction("退出", self)
        action_quit.triggered.connect(QApplication.quit)

        menu.addAction(action_show)
        menu.addSeparator()
        menu.addAction(action_start)
        menu.addAction(action_stop)
        menu.addAction(action_restart)
        menu.addAction(action_browser)
        menu.addAction(action_settings)
        menu.addSeparator()
        menu.addAction(action_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ 状态
    def _on_status(self, snap: dict) -> None:
        state = snap.get("state", "unknown")
        labels = {"running": "运行中", "stopped": "已停止", "pending": "启动中"}
        text = labels.get(state, state)
        color = {"running": "#059669", "stopped": "#dc2626"}.get(state, "#d97706")
        self.status_label.setText(f"● 服务{text}")
        self.status_label.setStyleSheet(f"color:{color};font-weight:700;padding-right:8px;")
        self.tray.setToolTip(
            f"Flask微内核服务：{text} · 端口 {snap.get('port') or self.port}"
        )

    # ------------------------------------------------------------------ 窗口行为
    def changeEvent(self, event) -> None:
        """点最小化 → 收回托盘后台运行。"""
        super().changeEvent(event)
        if event.type() != event.Type.WindowStateChange:
            return
        minimize_to_tray = bool((self.cfg.get("ui") or {}).get("minimize_to_tray", True))
        if not (self.isMinimized() and minimize_to_tray and self.tray.isVisible()):
            return
        # 先恢复正常状态再隐藏，否则下次 showNormal 会被系统当成"仍然最小化"
        QTimer.singleShot(0, self._hide_to_tray)

    def _hide_to_tray(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.hide()
        if not self._tray_hint_shown:
            self.tray.showMessage(
                "Flask 微内核已在后台运行",
                "窗口已收到托盘，右键托盘图标可以显示窗口、启动/停止服务或退出。",
                QSystemTrayIcon.Information, 4000,
            )
            self._tray_hint_shown = True

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:
        """点关闭 → 直接退出程序（退出前由 main.shutdown 停掉守护与子进程）。"""
        event.accept()
        self.tray.hide()
        QApplication.quit()
