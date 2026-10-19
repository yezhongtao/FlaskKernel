"""端口工具：占用检测、端口回退、占用进程查询。

为什么需要它
------------
Windows 上 Python 的 http.server 默认开启 SO_REUSEADDR，而 Windows 的
SO_REUSEADDR 语义与 Linux 不同：**端口已被别的进程占用时，绑定仍然"成功"**。
结果就是 Flask 打印了启动日志、守护进程以为一切正常，但浏览器请求被
另一个进程（或直接拒绝）截走，表现为"网页打不开"。

所以启动前必须用 is_port_free() 做一次**独占绑定**探测（不设置
SO_REUSEADDR），拿到真实占用情况，再决定用主端口还是备用端口。
"""
from __future__ import annotations

import socket
import subprocess
import sys

from core.config import PORT_FALLBACKS


def is_port_free(port: int, host: str = "0.0.0.0") -> bool:
    """独占探测端口是否可用（Windows 上等同于确认没被任何进程监听）。"""
    if port <= 0 or port > 65535:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 关键：不设置 SO_REUSEADDR，避免 Windows 上"假成功"
        sock.bind((host, int(port)))
        sock.listen(1)
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def find_pid_on_port(port: int) -> list[int]:
    """返回监听指定端口的进程 PID（仅 Windows 支持，失败返回空列表）。"""
    if sys.platform != "win32":
        return []
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=6,
            encoding="utf-8", errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        local, state, pid = fields[1], fields[3], fields[-1]
        if not local.endswith(f":{port}") or state.upper() != "LISTENING":
            continue
        if pid.isdigit():
            pids.add(int(pid))
    return sorted(pids)


def describe_port_owner(port: int) -> str:
    """给出一句人类可读的占用说明，用于日志 / 页面提示。"""
    pids = find_pid_on_port(port)
    if not pids:
        return f"端口 {port} 不可用（可能被系统服务或受保护进程占用）"
    names = []
    for pid in pids:
        name = _process_name(pid)
        names.append(f"PID {pid}" + (f" ({name})" if name else ""))
    return f"端口 {port} 已被占用：" + "、".join(names)


def _process_name(pid: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out or out.startswith("ERROR") or "No tasks" in out:
        return ""
    first = out.splitlines()[0]
    if first.startswith('"'):
        return first.split('","')[0].strip('"')
    return ""


def resolve_port(preferred: int | None, fallbacks: tuple[int, ...] = PORT_FALLBACKS,
                 host: str = "0.0.0.0") -> tuple[int, bool]:
    """选一个可用端口。

    返回 (port, fell_back)。优先用 preferred；不可用则按 fallbacks 顺序找；
    都不行时抛出 RuntimeError。
    """
    if preferred and is_port_free(preferred, host):
        return int(preferred), False

    for candidate in fallbacks:
        if candidate and candidate != preferred and is_port_free(candidate, host):
            return int(candidate), True

    raise RuntimeError(
        f"没有可用端口：首选 {preferred}，备用 {list(fallbacks)} 全部被占用。"
        f"请在 config.json 的 app.port 换一个端口。"
    )


# --------------------------------------------------------------------------- #
# getfqdn 卡顿修复
# --------------------------------------------------------------------------- #
_PATCHED = False


def patch_fast_getfqdn() -> None:
    """把 socket.getfqdn 换成本地快速实现（进程内生效，可重复调用）。

    为什么需要：
    Python 的 http.server.HTTPServer.server_bind() 会执行
        self.server_name = socket.getfqdn(host)
    getfqdn 会做一次反向 DNS 查询。某些 Windows 机器（DNS 后缀配置异常、
    或 DNS 服务器不可达）上这一句会卡 10 秒以上——表现为
    "程序启动了，但网页要等十几秒才打得开 / 甚至打不开"。

    这里 0.0.0.0 / :: / 127.0.0.1 / localhost 直接返回主机名，
    其它情况退回原函数，只影响 SERVER_NAME 这类展示用字段。
    """
    global _PATCHED
    if _PATCHED:
        return

    original = socket.getfqdn

    def _fast_getfqdn(name: str | None = "") -> str:
        if not name or name in ("0.0.0.0", "::", "::1", "127.0.0.1", "localhost"):
            try:
                return socket.gethostname() or "localhost"
            except OSError:
                return "localhost"
        return str(name) or "localhost"

    socket.getfqdn = _fast_getfqdn  # type: ignore[assignment]
    _PATCHED = True


def fqdn_is_slow(host: str = "0.0.0.0", threshold: float = 1.0) -> bool:
    """探测本机 getfqdn 是否异常慢（诊断页 / 日志用）。"""
    import time

    started = time.perf_counter()
    try:
        socket.getfqdn(host)
    except OSError:
        return True
    return (time.perf_counter() - started) > threshold
