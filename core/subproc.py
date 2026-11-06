"""静默执行外部命令：Windows 上绝不弹控制台窗口。

为什么需要这个模块
------------------
内核打包成 GUI 版 exe（无控制台）后，任何 `subprocess.run(["netstat", ...])`
都会让系统为子进程**新建一个控制台窗口**（任务管理器里表现为
「TCP/IP Netstat 命令」+「控制台窗口主机 / conhost.exe」一闪而过）。

控制面板每 2 秒刷新一次状态，每次都会查「谁占着端口」，于是窗口就一直在闪。
只加 `capture_output=True` 是不够的：它只重定向输出，**不阻止系统创建控制台**。

必须同时给：
  · `creationflags = CREATE_NO_WINDOW (0x08000000)`
  · `startupinfo.dwFlags |= STARTF_USESHOWWINDOW` + `wShowWindow = SW_HIDE`

两者一起用才在所有 Windows 版本上都不闪（少数旧系统只认 startupinfo）。
"""
from __future__ import annotations

import subprocess
import sys

__all__ = ["no_window_kwargs", "run_hidden", "CREATE_NO_WINDOW"]

CREATE_NO_WINDOW = 0x08000000
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)
_IS_WIN = sys.platform == "win32"


def _startupinfo():
    """STARTF_USESHOWWINDOW + SW_HIDE，兼容没有 CREATE_NO_WINDOW 的场景。"""
    if not _IS_WIN:
        return None
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0          # SW_HIDE
        return info
    except (AttributeError, ValueError):
        return None


def no_window_kwargs() -> dict:
    """给 subprocess.run / Popen 的关键字参数（非 Windows 返回空字典）。"""
    if not _IS_WIN:
        return {}
    kwargs = {"creationflags": _CREATE_NO_WINDOW}
    info = _startupinfo()
    if info is not None:
        kwargs["startupinfo"] = info
    return kwargs


def run_hidden(cmd, timeout: float = 10, text: bool = True,
               encoding: str = "utf-8", errors: str = "replace"):
    """静默跑一条命令并收集输出。

    用法与原 `subprocess.run(cmd, capture_output=True, text=True)` 一致，
    只是保证不弹窗。异常（OSError / TimeoutExpired / CalledProcessError）原样抛出，
    由调用方决定怎么兜底。
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=text,
        timeout=timeout,
        encoding=encoding if text else None,
        errors=errors if text else None,
        **no_window_kwargs(),
    )
