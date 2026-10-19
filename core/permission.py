"""权限装饰器 + 内核权限码。

两种权限，共用同一张 User.permissions 列表：
1. 插件权限（方案第六章）：完整码 <full_id>.<短码>，例如
   yezhongtao.oa.notice.view。插件视图用 @require_perm("view")，
   短码会自动拼当前蓝图（= full_id）前缀。
2. 内核权限：完整码 kernel.<模块>.<动作>，用于内核自己的管理页
   （插件管理、URL 映射、用户管理、服务控制、系统设置、日志）。
   内核页面不属于任何插件，所以用 @require_kernel_perm(...) 显式声明。

超管（is_superadmin=True）拥有全部权限，直接放行。
"""
from __future__ import annotations

from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user

# --------------------------------------------------------------------------- #
# 内核权限码
# --------------------------------------------------------------------------- #
KERNEL_PERM_PLUGINS = "kernel.plugins.manage"
KERNEL_PERM_ROUTES = "kernel.routes.manage"
KERNEL_PERM_USERS = "kernel.users.manage"
KERNEL_PERM_SERVICE = "kernel.service.manage"
KERNEL_PERM_SETTINGS = "kernel.settings.manage"
KERNEL_PERM_LOGS = "kernel.logs.view"

KERNEL_PERMS: dict[str, str] = {
    KERNEL_PERM_PLUGINS: "插件管理（安装 / 启停 / 卸载）",
    KERNEL_PERM_ROUTES: "URL 映射管理",
    KERNEL_PERM_USERS: "用户与权限管理",
    KERNEL_PERM_SERVICE: "服务控制台（启动 / 停止 / 重启 / 保活）",
    KERNEL_PERM_SETTINGS: "系统设置（修改全部配置）",
    KERNEL_PERM_LOGS: "日志查看（应用日志 / 访问日志）",
}

KERNEL_PERM_GROUPS: list[dict] = [
    {"title": "内核 · 插件", "code": KERNEL_PERM_PLUGINS},
    {"title": "内核 · 路由", "code": KERNEL_PERM_ROUTES},
    {"title": "内核 · 用户", "code": KERNEL_PERM_USERS},
    {"title": "内核 · 服务", "code": KERNEL_PERM_SERVICE},
    {"title": "内核 · 设置", "code": KERNEL_PERM_SETTINGS},
    {"title": "内核 · 日志", "code": KERNEL_PERM_LOGS},
]


# --------------------------------------------------------------------------- #
# 插件权限（方案 4.7 / 6.6）
# --------------------------------------------------------------------------- #
def current_plugin_id() -> str | None:
    """从 request.endpoint 反推当前插件 full_id。"""
    endpoint = request.endpoint
    if not endpoint or "." not in endpoint:
        return None
    return endpoint.rsplit(".", 1)[0]


def _resolve_full_code(code: str) -> str:
    if "." in code:
        return code
    plugin_id = current_plugin_id()
    if not plugin_id:
        raise RuntimeError(f"无法解析权限码（不在插件蓝图内）: {code}")
    return f"{plugin_id}.{code}"


def _user_has(full_code: str) -> bool:
    if not current_user.is_authenticated:
        return False
    user = current_user
    if getattr(user, "is_superadmin", False):
        return True
    return bool(user.has_perm(full_code))


def require_perm(code: str):
    """短码自动拼插件前缀；全码直接用。内置登录校验。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()

            full_code = _resolve_full_code(code)
            if not _user_has(full_code):
                abort(403, description=f"缺少权限: {full_code}")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# --------------------------------------------------------------------------- #
# 内核权限
# --------------------------------------------------------------------------- #
def user_has_kernel_perm(user, *codes: str) -> bool:
    """判断用户是否拥有任意一个内核权限码（超管恒真）。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superadmin", False):
        return True
    owned = set(getattr(user, "permissions", None) or [])
    return any(code in owned for code in codes)


def require_kernel_perm(*codes: str):
    """内核管理页权限校验：登录 + 拥有任一内核权限码。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not user_has_kernel_perm(current_user, *codes):
                abort(403, description="缺少权限: " + " / ".join(codes))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_superadmin(fn):
    """只允许超管（例如删除用户、重置密码）。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not getattr(current_user, "is_superadmin", False):
            abort(403, description="仅超级管理员可执行该操作")
        return fn(*args, **kwargs)

    return wrapper
