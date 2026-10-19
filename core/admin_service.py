"""服务控制台 + 系统设置 + 日志查看（内核管理页）。

页面：
    GET  /admin/service        服务控制台（状态、启动/停止/重启、保活开关）
    GET  /admin/settings       系统设置（config.json 全量可视编辑）
    GET  /admin/logs           日志查看（应用日志 / 访问日志）

接口：
    GET  /admin/service/status.json        状态快照（轮询用）
    POST /admin/service/action             启动 / 停止 / 重启 / 保活开关
    POST /admin/service/safe-mode          进入 / 退出安全模式
    POST /admin/settings                   保存表单配置
    POST /admin/settings/advanced          高级：直接提交 JSON 片段
    POST /admin/settings/secret            重新生成密钥
    GET  /admin/logs/data.json             日志内容
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from core import config_schema, logview, service_control
from core.config import (CONFIG_FILE, load_config, set_config_path,
                         update_config)
from core.log import get_logger
from core.models import Plugin, Route
from core.paths import mask_paths
from core.permission import (KERNEL_PERM_LOGS, KERNEL_PERM_SERVICE,
                             KERNEL_PERM_SETTINGS, require_kernel_perm)
from core.store import load_json
from user.model import User

logger = get_logger(__name__)

service_bp = Blueprint("admin_service", __name__)


# --------------------------------------------------------------------------- #
# 公共数据
# --------------------------------------------------------------------------- #
def _counts() -> dict:
    return {
        "plugins": Plugin.query.count(),
        "plugins_loaded": Plugin.query.filter_by(status="loaded").count(),
        "plugins_error": Plugin.query.filter_by(status="error").count(),
        "users": User.query.count(),
        "routes": Route.query.count(),
    }


def _status_payload() -> dict:
    snap = service_control.snapshot()
    cfg = load_config()
    app_cfg = cfg.get("app") or {}
    svc_cfg = cfg.get("service") or {}
    snap.update({
        "name": app_cfg.get("name") or "Flask微内核",
        "config_port": app_cfg.get("port"),
        "web_control": bool(svc_cfg.get("web_control", True)),
        "safe_mode": bool((cfg.get("plugin") or {}).get("safe_mode")),
        "counts": _counts(),
        "now": datetime.now().isoformat(timespec="seconds"),
    })
    return snap


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict(flat=True)


# --------------------------------------------------------------------------- #
# 服务控制台
# --------------------------------------------------------------------------- #
@service_bp.route("/service")
@require_kernel_perm(KERNEL_PERM_SERVICE)
def service_page():
    status = _status_payload()
    app_log = logview.read_app_log(lines=60)
    return render_template(
        "admin/service.html",
        status=status,
        app_log=app_log,
        config=load_config(),
        mode=status.get("mode"),
    )


@service_bp.route("/service/status.json")
@require_kernel_perm(KERNEL_PERM_SERVICE)
def service_status():
    return jsonify({"ok": True, "status": _status_payload()})


@service_bp.route("/service/action", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_SERVICE)
def service_action():
    data = _json_body()
    action = (data.get("action") or "").strip()
    if action not in service_control.ACTIONS:
        return jsonify({"ok": False, "error": f"未知操作: {action}"}), 400

    cfg = load_config()
    svc_cfg = cfg.get("service") or {}
    if not bool(svc_cfg.get("web_control", True)):
        return jsonify({
            "ok": False,
            "error": "Web 控制台已被禁用（config.json 的 service.web_control=false）",
        }), 403

    guardian = service_control.guardian_alive()

    # 保活开关：写 config.json，守护进程 2 秒内自动同步
    if action in ("keepalive_on", "keepalive_off"):
        enabled = action == "keepalive_on"
        service_control.set_keepalive(enabled)
        if guardian:
            service_control.request_control(
                action, requested_by=current_user.username,
            )
        logger.info("用户 %s %s保活", current_user.username, "开启" if enabled else "关闭")
        return jsonify({
            "ok": True, "action": action, "guardian": guardian,
            "message": ("保活已开启" if enabled else "保活已关闭：服务崩溃后不再自动拉起"),
        })

    # 启动 / 停止 / 重启
    if not guardian:
        if action == "stop":
            service_control.request_control(action, requested_by=current_user.username)
            _schedule_standalone_exit()
            return jsonify({
                "ok": True, "action": action, "guardian": False,
                "message": "独立运行模式：服务正在退出，刷新页面将无法连接。"
                           "请用桌面控制面板或 restart.bat 重新启动。",
            })
        return jsonify({
            "ok": False, "guardian": False,
            "error": "当前为独立运行模式（没有守护进程），无法启动/重启服务。"
                     "请通过 main.py（桌面控制面板）或 restart.bat 启动，"
                     "这样守护进程才能在 Web 上执行启动/重启。",
        }), 409

    task_id = service_control.request_control(
        action, requested_by=current_user.username,
        reload_port=bool(data.get("reload_port")),
    )
    logger.info("用户 %s 请求 %s（task=%s）", current_user.username, action, task_id)
    return jsonify({
        "ok": True, "action": action, "task_id": task_id, "guardian": True,
        "message": service_control.ACTION_LABELS.get(action, action) + "指令已下发",
    })


def _schedule_standalone_exit(delay: float = 0.8) -> None:
    """独立模式下"停止服务"：先让 HTTP 响应发出去，再退出进程。"""
    def _exit():
        try:
            service_control.clear_app_state("stopped")
        except Exception:
            pass
        logger.warning("独立运行模式：按 Web 指令停止服务")
        try:
            logging_shutdown()
        except Exception:
            pass
        os._exit(0)

    threading.Timer(delay, _exit).start()


def logging_shutdown() -> None:
    import logging

    logging.shutdown()


@service_bp.route("/service/safe-mode", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_SERVICE)
def safe_mode():
    data = _json_body()
    enabled = str(data.get("enabled", "")).lower() in ("1", "true", "on", "yes")
    set_config_path("plugin.safe_mode", enabled)
    logger.warning(
        "用户 %s %s安全模式", current_user.username, "进入" if enabled else "退出",
    )
    return jsonify({
        "ok": True, "safe_mode": enabled,
        "message": ("已进入安全模式，重启后跳过所有插件" if enabled
                    else "已退出安全模式，重启后正常加载插件"),
    })


# --------------------------------------------------------------------------- #
# 系统设置
# --------------------------------------------------------------------------- #
@service_bp.route("/settings")
@require_kernel_perm(KERNEL_PERM_SETTINGS)
def settings_page():
    cfg = load_config()
    values: dict[str, str] = {}
    for section, field in config_schema.iter_fields():
        values[f"{section}.{field['key']}"] = config_schema.display_value(
            cfg, section, field["key"], field["type"],
        )

    raw = load_json(CONFIG_FILE, {}) or {}
    return render_template(
        "admin/settings.html",
        schema=config_schema.CONFIG_SCHEMA,
        values=values,
        cfg=cfg,
        raw_json=json.dumps(raw, ensure_ascii=False, indent=2),
        status=_status_payload(),
    )


@service_bp.route("/settings", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_SETTINGS)
def settings_save():
    cfg = load_config()
    before_port = (cfg.get("app") or {}).get("port")

    patch, errors, restart_paths = config_schema.apply_form(request.form, cfg)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("admin_service.settings_page"))

    if not patch:
        flash("没有需要保存的改动", "info")
        return redirect(url_for("admin_service.settings_page"))

    try:
        update_config(patch)
    except OSError as exc:
        flash(f"写入 config.json 失败：{exc}", "error")
        return redirect(url_for("admin_service.settings_page"))

    changed = [f"{sec}.{key}" for sec, values in patch.items() for key in values]
    logger.warning("用户 %s 修改配置: %s", current_user.username, ", ".join(changed))

    after_port = (load_config().get("app") or {}).get("port")
    port_changed = before_port != after_port
    auto_restart = request.form.get("auto_restart") == "on"

    if (port_changed or restart_paths) and auto_restart:
        if service_control.guardian_alive():
            service_control.request_control(
                "restart", requested_by=current_user.username,
                reload_port=True, reason="settings",
            )
            logger.warning(
                "用户 %s 保存配置，服务将重启（端口 %s -> %s）",
                current_user.username, before_port, after_port,
            )
            return _render_restarting(after_port, port_changed)

        flash(
            "配置已保存。当前是独立运行模式，需手动重启服务后生效。"
            + (f"端口将在重启后变为 {after_port}。" if port_changed else ""),
            "warning",
        )
        return redirect(url_for("admin_service.settings_page"))

    if restart_paths:
        flash(
            "配置已保存。以下字段需要重启服务才生效："
            + "、".join(restart_paths),
            "warning",
        )
    else:
        flash("配置已保存并立即生效", "success")
    return redirect(url_for("admin_service.settings_page"))


@service_bp.route("/settings/advanced", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_SETTINGS)
def settings_advanced():
    text = (request.form.get("json") or "").strip()
    if not text:
        flash("JSON 内容为空", "error")
        return redirect(url_for("admin_service.settings_page"))

    try:
        patch = json.loads(text)
    except json.JSONDecodeError as exc:
        flash(f"JSON 解析失败：{exc}", "error")
        return redirect(url_for("admin_service.settings_page"))

    errors = config_schema.validate_patch(patch)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("admin_service.settings_page"))

    try:
        update_config(patch)
    except OSError as exc:
        flash(f"写入 config.json 失败：{exc}", "error")
        return redirect(url_for("admin_service.settings_page"))

    restart_paths = config_schema.restart_paths_for(patch)
    logger.warning(
        "用户 %s 用高级模式写入配置: %s",
        current_user.username, json.dumps(patch, ensure_ascii=False),
    )
    if restart_paths:
        flash("JSON 配置已写入，以下字段需重启生效：" + "、".join(restart_paths),
              "warning")
    else:
        flash("JSON 配置已写入并立即生效", "success")
    return redirect(url_for("admin_service.settings_page"))


@service_bp.route("/settings/secret", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_SETTINGS)
def regenerate_secret():
    import secrets as _secrets

    which = request.form.get("which") or "app.secret_key"
    if which not in ("app.secret_key", "app.back_secret_key"):
        flash("未知的密钥字段", "error")
        return redirect(url_for("admin_service.settings_page"))

    set_config_path(which, _secrets.token_hex(32))
    logger.warning("用户 %s 重新生成了 %s（重启后所有会话失效）",
                   current_user.username, which)
    flash("密钥已重新生成，重启服务后生效（所有登录会话会失效）", "warning")
    return redirect(url_for("admin_service.settings_page"))


def _host() -> str:
    host = (request.host or "").split(":")[0]
    return host or "127.0.0.1"


def _render_restarting(new_port, port_changed: bool):
    """配置保存后需要重启：渲染"重启中"页，由前端轮询到新服务后再跳转。"""
    scheme = request.scheme or "http"
    host_header = request.host or "127.0.0.1"

    if port_changed and new_port:
        base = f"{scheme}://{_host()}:{new_port}"
    else:
        base = f"{scheme}://{host_header}"

    target = base + url_for("admin_service.settings_page")
    health = base + url_for("core.health")
    return render_template(
        "admin/restarting.html",
        target_url=target, health_url=health,
        port_changed=bool(port_changed), new_port=new_port,
    )


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
@service_bp.route("/logs")
@require_kernel_perm(KERNEL_PERM_LOGS)
def logs_page():
    files = logview.list_log_files()
    return render_template(
        "admin/logs.html",
        app_files=[p.name for p in files["app"]] + ["console.log"],
        access_days=logview.access_days(),
        today=datetime.now().date().isoformat(),
        config=load_config(),
    )


@service_bp.route("/logs/data.json")
@require_kernel_perm(KERNEL_PERM_LOGS)
def logs_data():
    kind = request.args.get("kind", "app")
    lines = int(request.args.get("lines") or 300)
    lines = max(20, min(lines, 2000))

    if kind == "access":
        data = logview.read_access_log(
            day=request.args.get("day"),
            lines=lines,
            kind=request.args.get("type") or "all",
            query=(request.args.get("q") or "").strip(),
        )
    else:
        data = logview.read_app_log(
            lines=lines, name=request.args.get("file") or "app.log",
        )
    data["kind"] = kind
    # 日志里的项目绝对路径统一换成相对路径再返回
    if isinstance(data.get("lines"), list):
        data["lines"] = [mask_paths(line) for line in data["lines"]]
    return jsonify(data)
