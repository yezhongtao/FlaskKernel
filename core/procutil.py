"""进程工具：跨平台杀进程树、按端口清理残留进程、判断进程是否存活。

main.py（UI 进程）和 core/guardian.py（守护进程）都要用，
所以单独成模块，避免循环 import。

**一律静默**：内核打包成无控制台的 GUI exe 后，`tasklist` / `taskkill`
每次调用都会让系统新建一个控制台窗口一闪而过；而 pid_alive() 被状态快照
每 2 秒调用一次，窗口就会一直闪。所以：
  · pid_alive   → 走 kernel32 的 OpenProcess + GetExitCodeProcess，零子进程；
  · taskkill    → core.subproc.run_hidden（CREATE_NO_WINDOW + SW_HIDE）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from core.netutil import find_pid_on_port
from core.log import get_logger
from core.subproc import run_hidden

logger = get_logger(__name__)

_k32 = None            # 缓存 kernel32 句柄
_k32_ready = None


def _kernel32():
    """加载 kernel32 并声明用到的签名（只做一次）。"""
    global _k32, _k32_ready
    if _k32_ready is not None:
        return _k32

    _k32_ready = False
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        lib.GetExitCodeProcess.restype = ctypes.c_int
        lib.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        lib.CloseHandle.restype = ctypes.c_int
        lib.CloseHandle.argtypes = [ctypes.c_void_p]
        _k32 = lib
        _k32_ready = True
    except (OSError, AttributeError, ImportError):
        _k32 = None
        _k32_ready = False
    return _k32


def _pid_alive_api(pid: int) -> bool | None:
    """API 判断进程是否存活；不可用时返回 None。"""
    import ctypes

    lib = _kernel32()
    if lib is None:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    handle = lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        # 打不开：拒绝访问说明进程还在（受保护），其它错误当作已结束
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong(0)
        if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    except Exception:
        return False
    finally:
        try:
            lib.CloseHandle(handle)
        except Exception:
            pass


def kill_process_tree(proc: subprocess.Popen | None, log=None) -> None:
    """跨平台杀掉一个进程及其所有子进程。"""
    if proc is None or proc.poll() is not None:
        return
    log = log or logger

    try:
        if sys.platform == "win32":
            run_hidden(["taskkill", "/F", "/T", "/PID", str(proc.pid)], timeout=10)
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
            run_hidden(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=10)
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
    """判断 PID 是否还活着（跨平台、静默、不依赖第三方库）。

    Windows 走 kernel32（零子进程）；只有 API 不可用时才退回 tasklist，
    而且退回时也是静默执行。
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        alive = _pid_alive_api(int(pid))
        if alive is not None:
            return alive
        try:
            out = run_hidden(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             timeout=5).stdout or ""
        except Exception:
            return False
        return str(int(pid)) in out

    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False
