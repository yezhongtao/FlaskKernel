"""用户业务逻辑：注册 / 建号 / 改密 / 重置 / 启停 / 删号 / 权限。

视图层（core/auth.py、core/admin_users.py）只负责收表单、调这里、给提示，
所有校验规则集中在本模块，方便复用（Web 页面、桌面面板、将来的 API）。

约定：出错抛 ValueError，消息直接给用户看；成功返回 User 对象。
"""
from __future__ import annotations

import re
from datetime import datetime

from core.db import db
from core.log import get_logger
from core.models import PluginPermission
from core.permission import KERNEL_PERMS
from user.model import User

logger = get_logger(__name__)

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$")
RESERVED_NAMES = {"admin", "root", "system", "anonymous", "guest"}
MAX_USERS_UNLIMITED = 0


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def _user_cfg() -> dict:
    from core.config import get_config

    return (get_config() or {}).get("user") or {}


def password_min_length() -> int:
    try:
        return max(4, int(_user_cfg().get("password_min_length") or 6))
    except (TypeError, ValueError):
        return 6


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not name:
        raise ValueError("用户名不能为空")
    if not USERNAME_RE.match(name):
        raise ValueError("用户名需 3-32 位，以字母开头，可含字母、数字、下划线、点、短横线")
    if name.lower() in RESERVED_NAMES:
        raise ValueError(f"用户名 {name} 为系统保留名")
    return name


def validate_password(password: str) -> str:
    pwd = password or ""
    min_len = password_min_length()
    if len(pwd) < min_len:
        raise ValueError(f"密码至少 {min_len} 位")
    if len(pwd) > 128:
        raise ValueError("密码不能超过 128 位")
    if pwd.strip() != pwd:
        raise ValueError("密码首尾不能有空格")
    return pwd


def _ensure_unique(username: str, exclude_id: int | None = None) -> None:
    query = User.query.filter(db.func.lower(User.username) == username.lower())
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    if query.first() is not None:
        raise ValueError(f"用户名 {username} 已存在")


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_users() -> list[User]:
    return User.query.order_by(User.id).all()


def get_user(user_id: int) -> User:
    user = db.session.get(User, int(user_id))
    if user is None:
        raise ValueError("用户不存在")
    return user


def superadmin_count() -> int:
    return User.query.filter_by(is_superadmin=True, active=True).count()


# --------------------------------------------------------------------------- #
# 创建 / 注册
# --------------------------------------------------------------------------- #
def create_user(username: str, password: str, *, is_superadmin: bool = False,
                active: bool = True, permissions: list[str] | None = None,
                actor: str = "-") -> User:
    name = validate_username(username)
    pwd = validate_password(password)
    _ensure_unique(name)

    user = User(
        username=name,
        is_superadmin=bool(is_superadmin),
        active=bool(active),
        permissions=sorted({p for p in (permissions or []) if p}),
        ext={},
    )
    user.set_password(pwd)
    db.session.add(user)
    db.session.commit()
    logger.info("创建用户 %s（操作人 %s，超管=%s）", name, actor, is_superadmin)
    return user


def register(username: str, password: str, password2: str = "") -> User:
    """自助注册。是否开放由 config.json 的 user.allow_register 决定。"""
    if not bool(_user_cfg().get("allow_register", False)):
        raise ValueError("系统未开放自助注册，请联系管理员开通账号")

    if password2 and password != password2:
        raise ValueError("两次输入的密码不一致")

    default_perms = list(_user_cfg().get("default_permissions") or [])
    user = create_user(
        username, password, is_superadmin=False, active=True,
        permissions=default_perms, actor="register",
    )
    logger.info("新用户注册：%s", user.username)
    return user


# --------------------------------------------------------------------------- #
# 修改 / 重置密码
# --------------------------------------------------------------------------- #
def change_password(user: User, old_password: str, new_password: str,
                    new_password2: str = "") -> None:
    if not user.check_password(old_password or ""):
        raise ValueError("原密码不正确")
    if new_password2 and new_password != new_password2:
        raise ValueError("两次输入的新密码不一致")
    if old_password and new_password == old_password:
        raise ValueError("新密码不能与原密码相同")

    user.set_password(validate_password(new_password))
    db.session.commit()
    logger.info("用户 %s 修改了自己的密码", user.username)


def reset_password(user_id: int, new_password: str, actor: str = "-") -> User:
    user = get_user(user_id)
    user.set_password(validate_password(new_password))
    db.session.commit()
    logger.warning("管理员 %s 重置了用户 %s 的密码", actor, user.username)
    return user


# --------------------------------------------------------------------------- #
# 更新 / 删除
# --------------------------------------------------------------------------- #
def update_user(user_id: int, *, username: str | None = None,
                is_superadmin: bool | None = None,
                active: bool | None = None,
                permissions: list[str] | None = None,
                actor: str = "-") -> User:
    user = get_user(user_id)

    if username is not None and username.strip() and username.strip() != user.username:
        name = validate_username(username)
        _ensure_unique(name, exclude_id=user.id)
        logger.info("用户 %s 改名 -> %s（操作人 %s）", user.username, name, actor)
        user.username = name

    if is_superadmin is not None and bool(is_superadmin) != bool(user.is_superadmin):
        if user.is_superadmin and not is_superadmin and superadmin_count() <= 1:
            raise ValueError("至少要保留一个启用中的超级管理员")
        user.is_superadmin = bool(is_superadmin)

    if active is not None and bool(active) != bool(user.active):
        if user.is_superadmin and not active and superadmin_count() <= 1:
            raise ValueError("不能停用最后一个超级管理员")
        user.active = bool(active)

    if permissions is not None:
        user.permissions = sorted({p for p in permissions if p})

    db.session.commit()
    logger.info("更新用户 %s（操作人 %s）", user.username, actor)
    return user


def delete_user(user_id: int, *, actor_id: int | None = None,
                actor: str = "-") -> str:
    user = get_user(user_id)
    if actor_id is not None and int(actor_id) == int(user.id):
        raise ValueError("不能删除当前登录的账号")
    if user.is_superadmin and superadmin_count() <= 1:
        raise ValueError("不能删除最后一个超级管理员")

    name = user.username
    db.session.delete(user)
    db.session.commit()
    logger.warning("删除用户 %s（操作人 %s）", name, actor)
    return name


def touch_login(user: User) -> None:
    user.last_login = datetime.utcnow()
    db.session.commit()


# --------------------------------------------------------------------------- #
# 权限目录
# --------------------------------------------------------------------------- #
def permission_catalog() -> list[dict]:
    """返回可分配的权限目录，供用户管理页渲染。

    结构：
        [{"title": "内核 · 插件", "kind": "kernel", "items": [
              {"code": "...", "label": "...", "source": "内核"}]}]
    """
    groups: list[dict] = [{
        "title": "内核权限",
        "kind": "kernel",
        "items": [
            {"code": code, "label": label, "source": "内核"}
            for code, label in KERNEL_PERMS.items()
        ],
    }]

    plugin_groups: dict[str, list[dict]] = {}
    for perm in PluginPermission.query.order_by(
        PluginPermission.plugin_id, PluginPermission.code
    ).all():
        plugin_groups.setdefault(perm.plugin_id, []).append({
            "code": perm.full_code,
            "label": perm.label or perm.code,
            "source": perm.plugin_id,
        })

    for plugin_id, items in plugin_groups.items():
        groups.append({
            "title": plugin_id,
            "kind": "plugin",
            "items": items,
        })
    return groups


def all_permission_codes() -> list[str]:
    codes = list(KERNEL_PERMS.keys())
    codes.extend(p.full_code for p in PluginPermission.query.all())
    return sorted(set(codes))


def stats() -> dict:
    total = User.query.count()
    active = User.query.filter_by(active=True).count()
    admins = User.query.filter_by(is_superadmin=True).count()
    return {"total": total, "active": active, "admins": admins}
