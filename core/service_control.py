"""服务运行时状态 + Web/桌面控制通道。

三段式设计
----------
1. data/runtime.json —— 运行时状态（谁在跑、端口、PID、保活开关、时间戳）。
   守护进程每秒写心跳（"guardian"），app 启动时写一次（"app"）。
2. data/control.json —— 控制指令（start / stop / restart / keepalive_on /
   keepalive_off）。Web 控制台、桌面面板只负责「写指令」，不直接杀进程。
3. 守护进程（core/guardian.py）每秒消费 control.json 并执行。

这样 Web 页面永远只需要写一个小 JSON，不依赖 os.kill 权限，
也不会因为把请求线程杀掉而导致响应丢失。

独立运行（python main.py --web，没有守护进程）时：
- stop 仍可用：app 写 service.enabled=false 后自行退出；
- start / restart 不可用：会返回明确提示（没有守护就没人负责拉起）。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from core.config import (CONTROL_FILE, RUNTIME_FILE, clear_config_cache,
                         set_config_path)
from core.netutil import find_pid_on_port
from core.procutil import pid_alive
from core.store import load_json, save_json

_lock = threading.RLock()

ACTIONS: tuple[str, ...] = (
    "start", "stop", "restart", "keepalive_on", "keepalive_off",
)

ACTION_LABELS: dict[str, str] = {
    "start": "启动服务",
    "stop": "停止服务",
    "restart": "重启服务",
    "keepalive_on": "开启保活",
    "keepalive_off": "关闭保活",
}

# 心跳超过这个秒数视为守护进程已死
HEARTBEAT_TIMEOUT = 8


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _merge_write(path: Path, patch: dict) -> dict:
    """读-合并-原子写，进程内加锁。"""
    with _lock:
        current = load_json(path, {}) or {}
        if not isinstance(current, dict):
            current = {}
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
        current["updated_at"] = _now()
        save_json(path, current)
        return current


# --------------------------------------------------------------------------- #
# 运行时状态
# --------------------------------------------------------------------------- #
def read_runtime() -> dict:
    data = load_json(RUNTIME_FILE, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("state", "unknown")
    data.setdefault("app", {})
    data.setdefault("guardian", {})
    return data


def write_app_state(pid: int | None = None, port: int | None = None,
                    state: str = "running", mode: str | None = None) -> dict:
    app = {
        "pid": int(pid if pid is not None else os.getpid()),
        "port": int(port) if port else None,
        "started_at": _now() if state == "running" else None,
    }
    if mode:
        app["mode"] = mode
    return _merge_write(RUNTIME_FILE, {"app": app, "state": state})


def clear_app_state(reason: str = "stopped") -> dict:
    with _lock:
        data = read_runtime()
        data["app"] = {"pid": None, "port": data.get("app", {}).get("port"),
                       "started_at": None}
        data["state"] = reason
        data["updated_at"] = _now()
        save_json(RUNTIME_FILE, data)
        return data


def write_guardian_state(pid: int | None = None, keepalive: bool = True,
                         state: str | None = None, port: int | None = None,
                         managed_pid: int | None = None) -> dict:
    guardian = {
        "pid": int(pid if pid is not None else os.getpid()),
        "keepalive": bool(keepalive),
        "port": int(port) if port else None,
        "managed_pid": managed_pid,
        "updated_at": _now(),
    }
    patch: dict = {"guardian": guardian}
    if state:
        patch["state"] = state
    return _merge_write(RUNTIME_FILE, patch)


def mark_guardian_offline() -> dict:
    """守护进程退出时清掉心跳时间，让 Web 立刻显示"无守护进程"。"""
    with _lock:
        data = read_runtime()
        guardian = data.get("guardian") or {}
        guardian["updated_at"] = None
        guardian["managed_pid"] = None
        data["guardian"] = guardian
        data["state"] = "stopped"
        data["updated_at"] = _now()
        save_json(RUNTIME_FILE, data)
        return data


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(iso)).total_seconds())
    except (TypeError, ValueError):
        return None


def guardian_alive() -> bool:
    """守护进程是否在线（心跳新 + PID 存活）。"""
    g = read_runtime().get("guardian") or {}
    age = _age_seconds(g.get("updated_at"))
    if age is None or age > HEARTBEAT_TIMEOUT:
        return False
    pid = g.get("pid")
    if pid and pid != os.getpid():
        return pid_alive(int(pid))
    return True


def app_alive() -> bool:
    a = read_runtime().get("app") or {}
    age = _age_seconds(a.get("started_at"))
    if age is None:
        return False
    return True


def mode() -> str:
    """guardian = 有守护进程；standalone = 独立运行（只有 app）。"""
    return "guardian" if guardian_alive() else "standalone"


def snapshot() -> dict:
    """给 Web / 桌面面板用的完整状态快照。"""
    rt = read_runtime()
    app = rt.get("app") or {}
    guardian = rt.get("guardian") or {}
    keepalive = bool(guardian.get("keepalive", True)) if guardian else \
        bool((load_json(CONTROL_FILE, {}) or {}).get("keepalive", True))
    started = app.get("started_at")
    uptime = int(_age_seconds(started) or 0) if started else 0
    port = app.get("port") or guardian.get("port")
    pids = find_pid_on_port(int(port)) if port else []
    return {
        "state": rt.get("state", "unknown"),
        "mode": mode(),
        "port": port,
        "app_pid": app.get("pid"),
        "app_started_at": started,
        "uptime_seconds": uptime,
        "guardian_pid": guardian.get("pid"),
        "guardian_alive": guardian_alive(),
        "keepalive": keepalive,
        "port_pids": pids,
        "updated_at": rt.get("updated_at"),
        "now": _now(),
    }


# --------------------------------------------------------------------------- #
# 控制通道
# --------------------------------------------------------------------------- #
def request_control(action: str, requested_by: str = "web", **extra) -> str:
    """写一条控制指令，返回 task_id。"""
    if action not in ACTIONS:
        raise ValueError(f"未知操作: {action}")
    task_id = uuid.uuid4().hex
    payload = {
        "task_id": task_id,
        "action": action,
        "label": ACTION_LABELS.get(action, action),
        "requested_by": requested_by,
        "time": _now(),
    }
    payload.update(extra)
    save_json(CONTROL_FILE, payload)
    return task_id


def take_control() -> dict | None:
    """守护进程/独立模式消费一条控制指令（读后即删）。"""
    with _lock:
        if not CONTROL_FILE.exists():
            return None
        req = load_json(CONTROL_FILE, {}) or {}
        try:
            CONTROL_FILE.unlink()
        except OSError:
            pass
    if not isinstance(req, dict) or not req.get("action"):
        return None
    return req


def peek_control() -> dict | None:
    req = load_json(CONTROL_FILE, {}) or {}
    return req if isinstance(req, dict) and req.get("action") else None


def set_keepalive(value: bool) -> None:
    """保活开关同时写进 config.json（重启后仍生效）。"""
    set_config_path("service.keepalive", bool(value))
    clear_config_cache()
    write_guardian_state(keepalive=bool(value))


def record_task(task_id: str, action: str, status: str = "done") -> dict:
    """记录一次控制任务的完成状态，供 /system/health?task_id= 查询。

    插件安装走 restart.flag → last_restart.json；
    服务控制（启动/停止/重启）走这里 → runtime.json 的 last_task。
    """
    return _merge_write(RUNTIME_FILE, {
        "last_task": {
            "id": task_id,
            "action": action,
            "status": status,
            "time": _now(),
        },
    })


def last_task() -> dict:
    task = read_runtime().get("last_task") or {}
    return task if isinstance(task, dict) else {}


def last_restart_info() -> dict:
    from core.config import LAST_RESTART_FILE

    info = load_json(LAST_RESTART_FILE, {}) or {}
    return info if isinstance(info, dict) else {}


def wait_task_done(task_id: str, timeout: float = 60.0) -> bool:
    """服务端轮询用：等某个 task_id 完成后（app 重启并消费 restart.flag）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if last_restart_info().get("task_id") == task_id:
            return True
        time.sleep(0.5)
    return False
