"""插件管理接口：安装 / 启停 / 卸载 / URL 映射。

权限：
- 插件相关页面需要 kernel.plugins.manage
- URL 映射页面需要 kernel.routes.manage
（超管默认拥有全部权限）
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, current_app, jsonify, render_template, request)

from core import service_control
from core.config import (PLUGINS_DIR, RESTART_FLAG, STAGING_DIR,
                         set_plugin_path_override, update_kernel_url)
from core.db import db
from core.log import get_logger
from core.models import (Menu, Plugin, PluginPermission, PluginUsesPermission,
                         Route)
from core.permission import (KERNEL_PERM_PLUGINS, KERNEL_PERM_ROUTES,
                             require_kernel_perm)
from core.plugin_manager import ID_RE, version_ok
from core.store import save_json
from core.web import payload

logger = get_logger(__name__)

# 不再硬编码 url_prefix，由 app.py 按 kernel.admin_prefix 注册
admin_bp = Blueprint("admin", __name__)

PLUGIN_ID_KEYS = ("author", "group", "name")
VERSION_RE = re.compile(r"^\d+(\.\d+)*$")
CORE_OWNER = "__core__"


# --------------------------------------------------------------------------- #
# 插件管理页面
# --------------------------------------------------------------------------- #
@admin_bp.route("/plugins")
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def plugins_page():
    rows = Plugin.query.order_by(Plugin.id).all()
    stats = {
        "total": len(rows),
        "loaded": sum(1 for r in rows if r.status == "loaded"),
        "error": sum(1 for r in rows if r.status == "error"),
        "blocked": sum(1 for r in rows if r.status == "blocked"),
        "disabled": sum(1 for r in rows if not r.enabled or r.status == "disabled"),
    }
    perm_counts = {
        pid: PluginPermission.query.filter_by(plugin_id=pid).count()
        for pid in {r.id for r in rows}
    }
    return render_template(
        "admin/plugins.html", plugins=rows, stats=stats,
        perm_counts=perm_counts, mode=service_control.mode(),
    )


@admin_bp.route("/plugins.json")
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def plugins_json():
    rows = Plugin.query.order_by(Plugin.id).all()
    return jsonify({"ok": True, "data": [r.to_dict() for r in rows]})


# --------------------------------------------------------------------------- #
# 上传安装
# --------------------------------------------------------------------------- #
@admin_bp.route("/plugins/upload", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def upload_plugin():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"ok": False, "error": "未选择文件"}), 400

    if not file.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "只支持 .zip 插件包"}), 400

    task_id = uuid.uuid4().hex
    staging = Path(STAGING_DIR) / task_id
    extracted = staging / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)

    zip_path = staging / "upload.zip"
    file.save(str(zip_path))

    try:
        _safe_extract(zip_path, extracted)
        src_root, manifest = _locate_manifest(extracted)
        _validate_manifest(manifest)
        full_id = f"{manifest['author']}.{manifest['group']}.{manifest['name']}"

        existing = db.session.get(Plugin, full_id)
        if existing is not None:
            old_ver = existing.version or "0"
            if not version_ok(manifest.get("version", "0"), old_ver):
                raise ValueError(
                    f"升级版本必须 >= 当前版本（当前 {old_ver}，上传 {manifest.get('version')}）"
                )

        _check_conflicts(manifest, full_id)
        _upsert_db(manifest, full_id, src_root)

        target = Path(PLUGINS_DIR) / full_id
        _atomic_replace(src_root, target)

        _trigger_restart(task_id, "install", plugin=full_id)

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "plugin": full_id,
            "mode": service_control.mode(),
            "message": ("安装成功，正在重启..." if service_control.guardian_alive()
                        else "安装成功，但当前是独立运行模式，请手动重启服务后生效"),
        })

    except Exception as exc:
        logger.exception("安装插件失败")
        shutil.rmtree(staging, ignore_errors=True)
        return jsonify({"ok": False, "error": str(exc)}), 400


# --------------------------------------------------------------------------- #
# 启用 / 停用 / 卸载
# --------------------------------------------------------------------------- #
@admin_bp.route("/plugins/<path:plugin_id>/enable", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def enable_plugin(plugin_id: str):
    p = db.session.get(Plugin, plugin_id)
    if p is None:
        return jsonify({"ok": False, "error": "插件不存在"}), 404

    p.enabled = True
    p.status = "disabled"
    p.last_error = None
    db.session.commit()

    task_id = uuid.uuid4().hex
    _trigger_restart(task_id, "enable", plugin=plugin_id)
    return jsonify({"ok": True, "task_id": task_id})


@admin_bp.route("/plugins/<path:plugin_id>/disable", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def disable_plugin(plugin_id: str):
    p = db.session.get(Plugin, plugin_id)
    if p is None:
        return jsonify({"ok": False, "error": "插件不存在"}), 404

    p.enabled = False
    p.status = "disabled"
    # 停用后侧栏不能再显示这个插件的菜单
    Menu.query.filter_by(plugin_id=plugin_id).delete()
    db.session.commit()

    task_id = uuid.uuid4().hex
    _trigger_restart(task_id, "disable", plugin=plugin_id)
    return jsonify({"ok": True, "task_id": task_id})


@admin_bp.route("/plugins/<path:plugin_id>/uninstall", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_PLUGINS)
def uninstall_plugin(plugin_id: str):
    p = db.session.get(Plugin, plugin_id)
    if p is None:
        return jsonify({"ok": False, "error": "插件不存在"}), 404

    children = Plugin.query.filter(
        (Plugin.parent == plugin_id) | (Plugin.dependencies.isnot(None))
    ).all()
    blockers = []
    for c in children:
        if c.id == plugin_id:
            continue
        if c.parent == plugin_id:
            blockers.append(f"{c.id}（子插件）")
        elif plugin_id in (c.dependencies or {}):
            blockers.append(f"{c.id}（依赖）")
    if blockers:
        return jsonify({
            "ok": False,
            "error": "插件被使用中: " + "，".join(blockers),
        }), 400

    PluginPermission.query.filter_by(plugin_id=plugin_id).delete()
    PluginUsesPermission.query.filter_by(plugin_id=plugin_id).delete()
    Menu.query.filter_by(plugin_id=plugin_id).delete()
    Route.query.filter_by(owner_id=plugin_id).delete()
    db.session.delete(p)
    db.session.commit()

    target = Path(PLUGINS_DIR) / plugin_id
    if target.exists():
        backup = Path(STAGING_DIR) / f"uninstalled-{plugin_id}-{uuid.uuid4().hex[:8]}"
        try:
            shutil.move(str(target), str(backup))
        except OSError as exc:
            logger.warning("卸载时移动目录失败: %s", exc)

    task_id = uuid.uuid4().hex
    _trigger_restart(task_id, "uninstall", plugin=plugin_id)
    return jsonify({"ok": True, "task_id": task_id})


# --------------------------------------------------------------------------- #
# URL 映射页面
# --------------------------------------------------------------------------- #
@admin_bp.route("/routes")
@require_kernel_perm(KERNEL_PERM_ROUTES)
def routes_page():
    rows = Route.query.order_by(Route.owner_kind, Route.path).all()

    kernel_urls = current_app.extensions.get("kernel_urls")
    fallback = kernel_urls.plugin_fallback_prefix if kernel_urls else "/plugin"

    # 给每条内核路由算一个"示例可访问路径"
    samples: dict[str, str | None] = {}
    if kernel_urls:
        for key, _path in kernel_urls.as_route_rows():
            samples[key] = kernel_urls.sample_url(key)

    return render_template(
        "admin/routes.html",
        routes=rows,
        core_owner=CORE_OWNER,
        fallback_prefix=fallback,
        samples=samples,
        mode=service_control.mode(),
    )


@admin_bp.route("/routes/<int:route_id>/remap", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_ROUTES)
def remap_route(route_id: int):
    row = db.session.get(Route, route_id)
    if row is None:
        return jsonify({"ok": False, "error": "路由不存在"}), 404

    # 前端 App.api 发的是 JSON，HTML 表单原生提交发的是表单数据，两种都要支持
    new_path = _norm_path(str(payload().get("path") or "").strip())
    if not new_path.startswith("/"):
        return jsonify({"ok": False, "error": "路径必须以 / 开头"}), 400

    # 冲突检查（排除自身）
    conflict = Route.query.filter(
        Route.path == new_path, Route.id != route_id
    ).first()
    if conflict is not None:
        return jsonify({
            "ok": False,
            "error": f"路径已被 {conflict.owner_id} 占用",
        }), 400

    # 内核路由 → 写回 config.json 的 kernel.<key>
    if row.owner_kind == "core":
        key = row.logical_path
        if not key or key not in _kernel_route_keys():
            return jsonify({"ok": False, "error": "内核路由缺少配置键"}), 400
        update_kernel_url(key, new_path)
        row.path = new_path
        db.session.commit()
        logger.info("内核路由 %s 改为 %s（重启生效）", key, new_path)
    else:
        row.path = new_path
        # 插件路由：写进 config.json 的 plugin.path_overrides，重启后仍然生效
        set_plugin_path_override(row.owner_id, new_path)
        db.session.commit()
        logger.info("插件路由 %s 改为 %s（已写入 config.json，重启生效）",
                    row.owner_id, new_path)

    task_id = uuid.uuid4().hex
    _trigger_restart(task_id, "remap", route=route_id, path=new_path)
    return jsonify({"ok": True, "task_id": task_id, "mode": service_control.mode()})


def _kernel_route_keys() -> set[str]:
    return {
        "home_url", "auth_prefix", "admin_prefix", "system_prefix",
        "static_prefix", "plugin_static_prefix", "plugin_fallback_prefix",
    }


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _norm_path(p: str) -> str:
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    return p or "/"


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            target = (dest / name).resolve()
            if target != dest and not str(target).startswith(str(dest) + os.sep):
                raise ValueError(f"非法压缩包路径: {info.filename}")
            if os.path.isabs(name) or ".." in Path(name).parts:
                raise ValueError(f"非法压缩包路径: {info.filename}")
        zf.extractall(dest)


def _locate_manifest(extracted: Path) -> tuple[Path, dict]:
    from core.store import load_json

    root = extracted
    manifest_path = root / "plugin.json"

    if not manifest_path.is_file():
        subs = [p for p in root.iterdir() if p.is_dir()]
        if len(subs) == 1 and (subs[0] / "plugin.json").is_file():
            root = subs[0]
            manifest_path = root / "plugin.json"
        else:
            raise ValueError("压缩包内未找到 plugin.json")

    manifest = load_json(manifest_path, None)
    if not isinstance(manifest, dict):
        raise ValueError("plugin.json 格式错误")

    if not (root / "hooks.py").is_file():
        raise ValueError("插件包缺少 hooks.py")

    return root, manifest


def _validate_manifest(m: dict) -> None:
    for key in PLUGIN_ID_KEYS:
        val = m.get(key)
        if not val or not isinstance(val, str):
            raise ValueError(f"plugin.json 缺少必填字段: {key}")
        if not ID_RE.match(val):
            raise ValueError(f"字段 {key} 格式非法（应为 ^[a-z][a-z0-9_]*$）: {val}")

    ver = m.get("version")
    if not ver or not isinstance(ver, str) or not VERSION_RE.match(ver):
        raise ValueError(f"version 格式非法（应为 x.y.z）: {ver}")

    for field in ("dependencies", "provides", "permissions", "uses_permissions"):
        val = m.get(field)
        if val is not None and not isinstance(val, dict):
            raise ValueError(f"字段 {field} 必须是对象")

    up = m.get("url_prefix")
    if up is not None:
        if not isinstance(up, str) or not up.startswith("/"):
            raise ValueError("url_prefix 必须以 / 开头")

    perms = m.get("permissions") or {}
    for short_code in perms:
        if not ID_RE.match(short_code):
            raise ValueError(f"权限短码格式非法: {short_code}")


def _check_conflicts(m: dict, full_id: str) -> None:
    """URL 冲突检查：和内核冲突自动兜底；和兄弟冲突中断。"""
    kernel_urls = current_app.extensions.get("kernel_urls")
    reserved = kernel_urls.reserved() if kernel_urls else set()

    prefix = (m.get("url_prefix") or "").strip().rstrip("/")
    if not prefix:
        return

    if prefix in reserved:
        return

    rows = Route.query.filter(Route.path == prefix).all()
    for r in rows:
        if r.owner_id == full_id:
            continue
        parent = m.get("parent")
        if parent and r.owner_id == parent:
            continue
        raise ValueError(
            f"URL 前缀 {prefix} 已被插件 {r.owner_id} 占用，"
            f"请修改 url_prefix 或在安装后手动映射"
        )


def _upsert_db(m: dict, full_id: str, src_root: Path) -> None:
    author = m["author"]
    group = m["group"]
    name = m["name"]

    row = db.session.get(Plugin, full_id)
    if row is None:
        row = Plugin(id=full_id)
        db.session.add(row)

    row.author = author
    row.group_name = group
    row.name = name
    row.label = m.get("label") or name
    row.description = m.get("description") or ""
    row.version = m.get("version")
    row.parent = m.get("parent")
    row.parent_min_version = m.get("parent_min_version")
    row.dependencies = m.get("dependencies") or {}
    row.provides = m.get("provides") or {}
    row.url_prefix = m.get("url_prefix")
    row.enabled = True
    row.status = "disabled"
    row.last_error = None
    if row.installed_at is None:
        row.installed_at = datetime.utcnow()

    PluginPermission.query.filter_by(plugin_id=full_id).delete()
    for short_code, label in (m.get("permissions") or {}).items():
        db.session.add(PluginPermission(
            plugin_id=full_id,
            code=short_code,
            full_code=f"{full_id}.{short_code}",
            label=str(label),
        ))

    PluginUsesPermission.query.filter_by(plugin_id=full_id).delete()
    for code, reason in (m.get("uses_permissions") or {}).items():
        db.session.add(PluginUsesPermission(
            plugin_id=full_id,
            permission_code=code,
            reason=str(reason),
        ))

    db.session.commit()


def _atomic_replace(src_root: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.new"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    shutil.copytree(src_root, tmp, dirs_exist_ok=False)

    if target.exists():
        old = target.parent / f".{target.name}.old"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        os.replace(str(target), str(old))
        try:
            os.replace(str(tmp), str(target))
        except OSError:
            os.replace(str(old), str(target))
            raise
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.replace(str(tmp), str(target))


def _trigger_restart(task_id: str, action: str, **extra) -> None:
    payload = {
        "task_id": task_id,
        "action": action,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    payload.update(extra)
    save_json(RESTART_FLAG, payload)
    logger.info("已写入重启标志: %s", payload)