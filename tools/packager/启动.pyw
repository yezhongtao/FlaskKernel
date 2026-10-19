"""备用启动器：双击运行（需要系统把 .pyw 关联到 Python）。

它只负责用工具目录里的 runtime 环境启动 app/main.py，
不需要安装任何第三方库（只用标准库）。
"""
from __future__ import annotations

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(BASE, "runtime", "Scripts", "pythonw.exe")
PYTHON = os.path.join(BASE, "runtime", "Scripts", "python.exe")
SCRIPT = os.path.join(BASE, "app", "main.py")

console = "--console" in sys.argv
exe = PYTHON if console else PYTHONW

if not os.path.isfile(exe):
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "插件打包工具",
            "找不到工具自带的 Python 环境：\n" + exe +
            "\n\n请确认 runtime 目录和本工具放在同一个文件夹里。",
        )
        root.destroy()
    except Exception:
        pass
    sys.exit(2)

flags = 0
if os.name == "nt" and not console:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

subprocess.Popen([exe, SCRIPT], cwd=BASE,
                 creationflags=flags, close_fds=True)
