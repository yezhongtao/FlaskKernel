"""插件打包工具入口（PySide6 桌面版）。

自包含：只依赖同目录的 packager_*.py 与 theme.py，**不 import 项目里的任何模块**。
运行方式（任选其一）：
    1. 双击工具目录里的「插件打包工具.exe」
    2. 双击「启动.pyw」
    3. 命令行：runtime\\Scripts\\pythonw.exe app\\main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from PySide6.QtWidgets import QApplication  # noqa: E402

from packager_ui import build_stylesheet  # noqa: E402
from theme import STYLE_SHEET  # noqa: E402


def main() -> None:
    from packager_window import MainWindow, make_icon

    app = QApplication(sys.argv)
    app.setApplicationName("插件打包工具")
    app.setApplicationDisplayName("插件打包工具")
    app.setWindowIcon(make_icon())

    # 勾选框补一个白色对勾（主题里只有纯色块）
    check_icon = APP_DIR / "assets" / "check.svg"
    app.setStyleSheet(build_stylesheet(
        STYLE_SHEET, check_icon.as_posix() if check_icon.is_file() else None,
    ))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
