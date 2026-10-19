"""用户与权限管理接口（内核）。

页面：  GET  /admin/users          用户管理（列表 + 新建 + 编辑 + 权限矩阵）
        GET  /admin/permissions     权限总览
接口：  POST /admin/users/create
        POST /admin/users/<id>/update
        POST /admin/users/<id>/reset-password
        POST /admin/users/<id>/delete
        GET  /admin/users.json

权限：kernel.users.manage；涉及超管身份、删除账号的操作仅超管可做。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from core import user_service
from core.log import get_logger
from core.permission import (KERNEL_PERM_USERS, require_kernel_perm)
from core.models import PluginPermission

logger = get_logger(__name__)

users_bp = Blueprint("admin_users", __name__)


def _payload() -> dict:
    """兼容 JSON body 与表单提交。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict(flat=True)


def _bool(value, default=None):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "on", "yes")


def _perms(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _is_superadmin() -> bool:
    return bool(getattr(current_user, "is_superadmin", False))


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@users_bp.route("/users")
@require_kernel_perm(KERNEL_PERM_USERS)
def users_page():
    users = user_service.list_users()
    return render_template(
        "admin/users.html",
        users=users,
        stats=user_service.stats(),
        catalog=user_service.permission_catalog(),
        password_min=user_service.password_min_length(),
        is_superadmin=_is_superadmin(),
        current_user_id=current_user.id,
    )


@users_bp.route("/permissions")
@require_kernel_perm(KERNEL_PERM_USERS)
def permissions_page():
    users = user_service.list_users()
    catalog = user_service.permission_catalog()

    # 每个权限码被哪些用户显式持有
    holders: dict[str, list[str]] = {}
    for u in users:
        for code in (u.permissions or []):
            holders.setdefault(code, []).append(u.username)

    # 给「眼睛」弹窗用的详情：权限全码、说明、来源、持有人
    detail: dict[str, dict] = {}
    for group in catalog:
        for item in group["items"]:
            detail[item["code"]] = {
                "code": item["code"],
                "label": item.get("label") or item["code"],
                "group": group.get("title") or "",
                "kind": group.get("kind") or "kernel",
                "holders": holders.get(item["code"], []),
            }

    return render_template(
        "admin/permissions.html",
        catalog=catalog,
        holders=holders,
        detail=detail,
        superadmins=[u.username for u in users if u.is_superadmin],
        users=users,
        total_codes=sum(len(g["items"]) for g in catalog),
        plugin_perm_count=PluginPermission.query.count(),
    )


@users_bp.route("/users.json")
@require_kernel_perm(KERNEL_PERM_USERS)
def users_json():
    return jsonify({
        "ok": True,
        "data": [u.to_safe_dict() for u in user_service.list_users()],
        "stats": user_service.stats(),
    })


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@users_bp.route("/users/create", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_USERS)
def create_user():
    data = _payload()
    want_super = _bool(data.get("is_superadmin"), False)
    if want_super and not _is_superadmin():
        return jsonify({"ok": False, "error": "只有超级管理员可以创建超级管理员"}), 403

    try:
        user = user_service.create_user(
            data.get("username") or "",
            data.get("password") or "",
            is_superadmin=want_super,
            active=_bool(data.get("active"), True),
            permissions=_perms(data.get("permissions")),
            actor=current_user.username,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({
        "ok": True, "id": user.id, "username": user.username,
        "message": f"用户 {user.username} 已创建",
    })


@users_bp.route("/users/<int:user_id>/update", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_USERS)
def update_user(user_id: int):
    data = _payload()
    want_super = _bool(data.get("is_superadmin"))
    if want_super is not None and want_super and not _is_superadmin():
        return jsonify({"ok": False, "error": "只有超级管理员可以授予超管权限"}), 403

    try:
        user = user_service.update_user(
            user_id,
            username=data.get("username") or None,
            is_superadmin=want_super,
            active=_bool(data.get("active")),
            permissions=_perms(data["permissions"]) if "permissions" in data else None,
            actor=current_user.username,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "用户参数不合法"}), 400

    return jsonify({"ok": True, "message": f"用户 {user.username} 已更新"})


@users_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_USERS)
def reset_password(user_id: int):
    if not _is_superadmin():
        return jsonify({"ok": False, "error": "只有超级管理员可以重置密码"}), 403

    data = _payload()
    try:
        user = user_service.reset_password(
            user_id, data.get("password") or "", actor=current_user.username,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "message": f"已重置 {user.username} 的密码"})


@users_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@require_kernel_perm(KERNEL_PERM_USERS)
def delete_user(user_id: int):
    if not _is_superadmin():
        return jsonify({"ok": False, "error": "只有超级管理员可以删除用户"}), 403

    try:
        name = user_service.delete_user(
            user_id, actor_id=current_user.id, actor=current_user.username,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "message": f"用户 {name} 已删除"})
