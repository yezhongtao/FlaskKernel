"""内核级访问日志：HTTP 访问 + 插件调用。"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import g, request
from flask_login import current_user

from core.log import get_logger

logger = get_logger(__name__)

_lock = threading.RLock()
_last_cleanup_date: date | None = None
_log_dir: Path | None = None
_keep_days: int = 30


def init_access(log_dir: str | Path, keep_days: int = 30) -> None:
    global _log_dir, _keep_days, _last_cleanup_date
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)
    _keep_days = max(int(keep_days), 1)
    _last_cleanup_date = None


def _cleanup_locked() -> None:
    """跨天检查 + 双重检查锁清理旧文件。"""
    global _last_cleanup_date
    today = date.today()
    if _last_cleanup_date == today:
        return
    with _lock:
        if _last_cleanup_date == today:
            return
        _last_cleanup_date = today
        if _log_dir is None:
            return
        cutoff = today - timedelta(days=_keep_days)
        for f in _log_dir.glob("access-*.log"):
            try:
                d = date.fromisoformat(f.stem[len("access-"):])
            except ValueError:
                continue
            if d < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass


def _write(record: dict[str, Any]) -> None:
    if _log_dir is None:
        return
    _cleanup_locked()
    line = json.dumps(record, ensure_ascii=False)
    path = _log_dir / f"access-{date.today().isoformat()}.log"
    with _lock:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            logger.warning("写访问日志失败: %s", exc)


def record_http(ip: str, user: str, method: str, path: str,
                status: int, duration_ms: float) -> None:
    _write({
        "type": "http",
        "time": datetime.now().isoformat(timespec="seconds"),
        "ip": ip,
        "user": user,
        "method": method,
        "path": path,
        "status": int(status),
        "duration_ms": round(float(duration_ms), 2),
    })


def record_plugin_call(caller: str, provider: str, service: str,
                       method: str, duration_ms: float, ok: bool) -> None:
    _write({
        "type": "plugin_call",
        "time": datetime.now().isoformat(timespec="seconds"),
        "caller": caller,
        "provider": provider,
        "service": service,
        "method": method,
        "duration_ms": round(float(duration_ms), 2),
        "ok": bool(ok),
    })


def register_http_logging(app) -> None:
    @app.before_request
    def _access_before():
        g._access_t0 = time.perf_counter()

    @app.after_request
    def _access_after(response):
        t0 = getattr(g, "_access_t0", None)
        duration = (time.perf_counter() - t0) * 1000 if t0 else 0.0
        try:
            user = current_user.username if current_user.is_authenticated else "-"
        except Exception:
            user = "-"
        try:
            record_http(
                ip=request.remote_addr or "-",
                user=user,
                method=request.method,
                path=request.path,
                status=response.status_code,
                duration_ms=duration,
            )
        except Exception:
            pass
        return response