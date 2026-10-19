"""内核路由视图函数。

URL 由 config.json 的 kernel 段决定，在 app.py 里用 add_url_rule 动态注册。

    /                     首页仪表盘（登录后）
    /system/health        健康检查（前端轮询重启完成用）
    /plugin-static/...    插件静态资源
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request, send_from_directory
from flask_login import current_user, login_required

from core import logview, service_control
from core.config import PLUGINS_DIR, LAST_RESTART_FILE, load_config
from core.models import Menu, Plugin, PluginPermission, Route
from core.store import load_json
from user.model import User


def _plugin_stats() -> dict:
    rows = Plugin.query.all()
    return {
        "total": len(rows),
        "loaded": sum(1 for r in rows if r.status == "loaded"),
        "error": sum(1 for r in rows if r.status == "error"),
        "blocked": sum(1 for r in rows if r.status == "blocked"),
        "disabled": sum(1 for r in rows if not r.enabled),
    }


@login_required
def view_index():
    cfg = load_config()
    status = service_control.snapshot()
    user_stats = {
        "total": User.query.count(),
        "active": User.query.filter_by(active=True).count(),
    }
    return render_template(
        "core/index.html",
        cfg=cfg,
        status=status,
        plugin_stats=_plugin_stats(),
        user_stats=user_stats,
        route_count=Route.query.count(),
        permission_count=PluginPermission.query.count(),
        menu_count=Menu.query.count(),
        activity=logview.recent_activity(8),
        last_restart=load_json(LAST_RESTART_FILE, {}) or {},
        is_admin=bool(getattr(current_user, "is_superadmin", False)),
    )


def view_health():
    runtime = service_control.read_runtime()
    payload = {
        "ok": True,
        "time": datetime.now().isoformat(timespec="seconds"),
        "state": runtime.get("state", "unknown"),
        "pid": runtime.get("app", {}).get("pid"),
    }
    task_id = request.args.get("task_id")
    if task_id:
        payload["task"] = _task_payload(task_id, runtime)
    return jsonify(payload)


def _task_payload(task_id: str, runtime: dict) -> dict:
    """任务完成状态：先看 restart.flag 结果，再看服务控制任务记录。"""
    info = load_json(LAST_RESTART_FILE, {}) or {}
    if info.get("task_id") == task_id:
        return {
            "id": task_id, "status": "done",
            "action": info.get("action"), "at": info.get("time"),
        }

    task = (runtime.get("last_task") or {})
    if task.get("id") == task_id:
        return {
            "id": task_id,
            "status": task.get("status", "done"),
            "action": task.get("action"),
            "at": task.get("time"),
        }

    return {"id": task_id, "status": "pending"}


def view_plugin_static(full_id: str, filename: str):
    static_dir = Path(PLUGINS_DIR) / full_id / "static"
    if not static_dir.is_dir():
        return jsonify({"ok": False, "error": "插件静态目录不存在"}), 404
    response = send_from_directory(str(static_dir), filename)
    response.headers["Cache-Control"] = "public, max-age=600"
    return response
