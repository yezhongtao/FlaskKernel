"""进程工具：跨平台杀进程树、按端口清理残留进程。

main.py（UI 进程）和 core/guardian.py（守护进程）都要用，
所以单独成模块，避免循环 import。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from core.netutil import find_pid_on_port
from core.log import get_logger

logger = get_logger(__name__)


def kill_process_tree(proc: subprocess.Popen | None, log=None) -> None:
    """跨平台杀掉一个进程及其所有子进程。"""
    if proc is None or proc.poll() is not None:
        return
    log = log or logger

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except Exception:
        log.exception("杀进程树失败，回退到 proc.kill()")
        try:
            proc.kill()
        except Exception:
            pass


def kill_pid(pid: int, log=None) -> None:
    """按 PID 杀进程（Windows 用 taskkill /T /F）。"""
    if not pid or pid == os.getpid():
        return
    log = log or logger
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.kill(int(pid), signal.SIGKILL)
    except Exception:
        log.exception("杀进程 %s 失败", pid)


def kill_by_port(port: int, log=None) -> None:
    """兜底：杀掉占用指定端口的进程（排除自身）。"""
    log = log or logger
    try:
        pids = find_pid_on_port(port)
        for pid in pids:
            if pid != os.getpid():
                log.warning("端口 %s 被 PID %s 占用，强制清理", port, pid)
                kill_pid(pid, log)
    except Exception:
        log.exception("按端口清理进程失败")


def pid_alive(pid: int) -> bool:
    """判断 PID 是否还活着（跨平台，不依赖第三方库）。"""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            ).stdout
            return str(int(pid)) in out
        os.kill(int(pid), 0)
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
