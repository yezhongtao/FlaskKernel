"""日志读取（Web 日志页 + 桌面面板共用）。

- 应用日志：data/logs/app.log（按天切分，app.log.2026-10-16）
- 控制台日志：data/logs/console.log（app 子进程的 stdout/stderr）
- 访问日志：data/logs/access-YYYY-MM-DD.log（JSON Lines）
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from core.config import LOGS_DIR

TAIL_LIMIT = 2000          # 单次最多返回行数
_SMALL_FILE = 512 * 1024   # 小于 512KB 直接整体读


def list_log_files() -> dict:
    """返回日志文件清单，供页面下拉选择。"""
    logs_dir = Path(LOGS_DIR)
    access_files = sorted(logs_dir.glob("access-*.log"), reverse=True)
    app_files = sorted(logs_dir.glob("app.log*"), reverse=True)
    return {
        "app": app_files,
        "access": access_files,
        "console": logs_dir / "console.log",
        "dir": logs_dir,
    }


def _read_tail(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    lines = max(1, min(int(lines), TAIL_LIMIT))
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size <= _SMALL_FILE:
                raw = f.read()
            else:
                f.seek(max(0, size - 256 * 1024))
                raw = f.read()
    except OSError:
        return []

    text = raw.decode("utf-8", errors="replace")
    rows = text.splitlines()
    return rows[-lines:]


def read_app_log(lines: int = 300, name: str = "app.log") -> dict:
    """读应用日志尾部。name 可指定 app.log / app.log.2026-10-16 / console.log。"""
    safe = Path(name or "app.log").name
    path = Path(LOGS_DIR) / safe
    if not path.is_file():
        return {"ok": False, "error": f"日志不存在: {safe}", "lines": [], "file": safe}
    rows = _read_tail(path, lines)
    try:
        size = path.stat().st_size
        mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        size, mtime = 0, None
    return {
        "ok": True, "file": safe, "lines": rows,
        "size": size, "mtime": mtime, "count": len(rows),
    }


def read_access_log(day: str | None = None, lines: int = 300,
                    kind: str = "all", query: str = "") -> dict:
    """读访问日志尾部，可按类型（http/plugin_call）和关键字过滤。"""
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            day = None
    if not day:
        day = date.today().isoformat()

    path = Path(LOGS_DIR) / f"access-{day}.log"
    if not path.is_file():
        return {"ok": True, "file": path.name, "rows": [], "day": day,
                "count": 0, "note": "当天还没有访问记录"}

    rows: list[dict] = []
    for line in reversed(_read_tail(path, TAIL_LIMIT)):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if kind in ("http", "plugin_call") and rec.get("type") != kind:
            continue
        if query:
            blob = json.dumps(rec, ensure_ascii=False).lower()
            if query.lower() not in blob:
                continue
        rows.append(rec)
        if len(rows) >= max(1, min(int(lines), TAIL_LIMIT)):
            break

    return {
        "ok": True, "file": path.name, "rows": rows, "day": day,
        "count": len(rows),
    }


def access_days() -> list[str]:
    """有访问日志的日期列表（新 → 旧）。"""
    days: list[str] = []
    for f in Path(LOGS_DIR).glob("access-*.log"):
        stem = f.stem[len("access-"):]
        try:
            date.fromisoformat(stem)
        except ValueError:
            continue
        days.append(stem)
    return sorted(days, reverse=True)


def recent_activity(limit: int = 8) -> list[dict]:
    """首页"最近动态"用：取当天最后的若干条访问记录。"""
    data = read_access_log(lines=limit)
    return data.get("rows", [])
