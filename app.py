"""Flask 应用工厂。"""
from __future__ import annotations

from datetime import datetime

from flask import Flask, jsonify, render_template, request, url_for
from flask_login import current_user

from core import access
from core.admin import admin_bp
from core.admin_service import service_bp
from core.admin_users import users_bp
from core.auth import auth_bp, ensure_default_admin, login_manager
from core.config import (DB_FILE, KERNEL_VERSION, LAST_RESTART_FILE, LOGS_DIR,
                         RESTART_FLAG, STATIC_DIR, TEMPLATES_DIR, KernelURLs,
                         load_config)
from core.db import db, migrate_schema
from core.log import get_logger, setup_logging
from core.paths import mask_paths, rel_path
from core.models import Menu, Plugin, Route
from core.permission import (KERNEL_PERM_LOGS, KERNEL_PERM_PLUGINS,
                             KERNEL_PERM_ROUTES, KERNEL_PERM_SERVICE,
                             KERNEL_PERM_SETTINGS, KERNEL_PERM_USERS,
                             user_has_kernel_perm)
from core.plugin_manager import PluginManager
from core.routes import view_health, view_index, view_plugin_static
from core.store import load_json, save_json

# 让 db.create_all() 能发现全部表
from core import models as _core_models   # noqa: F401
from user import model as _user_model     # noqa: F401

logger = get_logger(__name__)

CORE_OWNER = "__core__"

# 内核导航（顺序即显示顺序）
KERNEL_NAV: list[dict] = [
    {"group": "概览", "label": "仪表盘", "endpoint": "core.index",
     "icon": "dashboard", "perm": None},
    {"group": "插件", "label": "插件管理", "endpoint": "admin.plugins_page",
     "icon": "plugin", "perm": KERNEL_PERM_PLUGINS},
    {"group": "插件", "label": "URL 映射", "endpoint": "admin.routes_page",
     "icon": "route", "perm": KERNEL_PERM_ROUTES},
    {"group": "系统", "label": "服务控制", "endpoint": "admin_service.service_page",
     "icon": "power", "perm": KERNEL_PERM_SERVICE},
    {"group": "系统", "label": "系统设置", "endpoint": "admin_service.settings_page",
     "icon": "gear", "perm": KERNEL_PERM_SETTINGS},
    {"group": "系统", "label": "日志", "endpoint": "admin_service.logs_page",
     "icon": "list", "perm": KERNEL_PERM_LOGS},
    {"group": "账户", "label": "用户管理", "endpoint": "admin_users.users_page",
     "icon": "users", "perm": KERNEL_PERM_USERS},
    {"group": "账户", "label": "权限总览", "endpoint": "admin_users.permissions_page",
     "icon": "key", "perm": KERNEL_PERM_USERS},
    {"group": "账户", "label": "个人中心", "endpoint": "auth.profile",
     "icon": "user", "perm": None},
]

# 顶栏标题 / 副标题（按 endpoint 匹配）
PAGE_META: dict[str, tuple[str, str]] = {
    "core.index": ("仪表盘", "内核总览、服务状态与最近动态"),
    "admin.plugins_page": ("插件管理", "上传安装、启停、卸载，冷启动重启后生效"),
    "admin.routes_page": ("URL 映射", "内核前缀与插件路径分配，改完重启生效"),
    "admin_service.service_page": ("服务控制台", "启动 / 停止 / 重启 Flask 服务，管理保活"),
    "admin_service.settings_page": ("系统设置", "config.json 全部配置项的可视化编辑"),
    "admin_service.logs_page": ("日志", "应用日志与访问日志在线查看"),
    "admin_users.users_page": ("用户管理", "新建用户、分配权限、重置密码"),
    "admin_users.permissions_page": ("权限总览", "内核权限与插件权限的持有情况"),
    "auth.profile": ("个人中心", "账号信息与密码安全"),
    "auth.login": ("登录", ""),
    "auth.register": ("注册", ""),
}


def _consume_restart_flag() -> None:
    if not RESTART_FLAG.exists():
        return
    info = load_json(RESTART_FLAG, {}) or {}
    info["time"] = datetime.now().isoformat(timespec="seconds")
    save_json(LAST_RESTART_FILE, info)
    try:
        RESTART_FLAG.unlink()
    except OSError:
        pass
    logger.info("检测到重启标志: %s", info)


def _register_kernel_routes_to_table(kernel_urls: KernelURLs) -> None:
    """把内核 URL 写入 Route 表（只展示用；改这些行会写回 config.json）。"""
    db.session.query(Route).filter_by(owner_id=CORE_OWNER).delete()
    for key, path in kernel_urls.as_route_rows():
        db.session.add(Route(
            path=path,
            owner_id=CORE_OWNER,
            owner_kind="core",
            parent_id=None,
            is_override=False,
            logical_path=key,
        ))
    db.session.commit()


# --------------------------------------------------------------------------- #
# 模板上下文
# --------------------------------------------------------------------------- #
def _nav_items() -> list[dict]:
    """内核导航 + 插件注册的菜单，已按当前用户权限过滤。"""
    items: list[dict] = []
    for entry in KERNEL_NAV:
        perm = entry.get("perm")
        if perm and not user_has_kernel_perm(current_user, perm):
            continue
        items.append({
            "group": entry["group"],
            "label": entry["label"],
            "url": url_for(entry["endpoint"]),
            "icon": entry["icon"],
            "active": request.endpoint == entry["endpoint"],
        })

    plugin_menus = Menu.query.order_by(Menu.order, Menu.label).all()
    prefixes: dict[str, str] = {}

    def _plugin_prefix(plugin_id: str) -> str:
        if plugin_id in prefixes:
            return prefixes[plugin_id]
        row = db.session.get(Plugin, plugin_id)
        value = ""
        if row is not None:
            value = (row.actual_url_prefix or row.url_prefix or "")
        prefixes[plugin_id] = value
        return value

    for menu in plugin_menus:
        plugin = getattr(menu, "plugin_id", None)
        perm_code = f"{plugin}.{menu.perm}" if (plugin and menu.perm) else None
        if perm_code and not user_has_kernel_perm(current_user, perm_code):
            continue

        # 菜单地址：优先用接口名现算（改 URL 映射自动跟随），
        # 其次用注册时归一化的 url（{prefix} 占位符），最后兜底旧数据里的写死地址
        url = ""
        if getattr(menu, "endpoint", None):
            try:
                url = url_for(menu.endpoint)
            except Exception:  # noqa: BLE001 - 插件被停用/改名时退回 url
                url = ""
        if not url:
            url = menu.url or ""
            if "{prefix}" in url:
                prefix = _plugin_prefix(plugin).rstrip("/") if plugin else ""
                if prefix:
                    url = url.replace("{prefix}", prefix)
            elif plugin:
                row = db.session.get(Plugin, plugin)
                declared = ((row.url_prefix or "").rstrip("/") if row else "")
                actual = ((row.actual_url_prefix or "").rstrip("/") if row else "")
                if declared and actual and declared != actual and \
                        (url == declared or url.startswith(declared + "/")):
                    url = actual + url[len(declared):]
        if not url:
            continue

        items.append({
            "group": "插件",
            "label": menu.label or menu.name,
            "url": url,
            "icon": menu.icon or "plugin",
            "active": request.path == url or
                      (url != "/" and request.path.startswith(url)),
        })
    return items


def _inject_globals(app: Flask, kernel_urls: KernelURLs) -> None:
    @app.context_processor
    def _globals():  # noqa: D401
        cfg = app.config.get("OA_CONFIG") or {}
        app_cfg = cfg.get("app") or {}
        plugin_cfg = cfg.get("plugin") or {}
        safe_mode = bool(plugin_cfg.get("safe_mode"))
        title, sub = PAGE_META.get(request.endpoint, ("", ""))
        return {
            "app_name": app_cfg.get("name") or "Flask微内核",
            "kernel_version": KERNEL_VERSION,
            "kernel_urls": kernel_urls,
            "nav_items": _nav_items(),
            "nav_groups": _group_nav(),
            "page_title": title or (app_cfg.get("name") or "Flask微内核"),
            "page_sub": sub,
            "safe_mode": safe_mode,
            "can_kernel": lambda *codes: user_has_kernel_perm(current_user, *codes),
            "is_superadmin": bool(getattr(current_user, "is_superadmin", False)),
            "now": datetime.now(),
            "active_endpoint": request.endpoint,
            "active_path": request.path,
            # 前端展示路径一律用项目相对路径（插件模板也能直接用）
            "rel_path": rel_path,
        }

    @app.template_filter("mask_paths")
    def _mask_paths(value):
        """日志等文本里出现的项目绝对路径 → 相对路径展示。"""
        return mask_paths(str(value or ""))

    @app.template_filter("dt")
    def _dt(value):
        """格式化时间：datetime / ISO 字符串 → 2026-10-17 10:30。"""
        if not value:
            return "-"
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return value
        return value.strftime("%Y-%m-%d %H:%M")

    @app.template_filter("uptime")
    def _uptime(seconds):
        """秒 → 2天3小时4分。"""
        try:
            seconds = int(seconds or 0)
        except (TypeError, ValueError):
            return "-"
        if seconds <= 0:
            return "-"
        days, rest = divmod(seconds, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, _ = divmod(rest, 60)
        parts = []
        if days:
            parts.append(f"{days} 天")
        if hours:
            parts.append(f"{hours} 小时")
        if minutes and not days:
            parts.append(f"{minutes} 分")
        return " ".join(parts) or "刚刚"

    @app.template_filter("filesize")
    def _filesize(size):
        try:
            size = float(size or 0)
        except (TypeError, ValueError):
            return "-"
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}GB"


_NAV_GROUP_ORDER = ["概览", "插件", "系统", "账户"]


def _group_nav() -> list[dict]:
    """把 nav_items 按 group 分组，方便侧边栏渲染小标题。"""
    items = _nav_items()
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["group"], []).append(item)
    ordered = []
    for name in _NAV_GROUP_ORDER:
        if name in groups:
            ordered.append({"name": name, "items": groups[name]})
    for name, group_items in groups.items():
        if name not in _NAV_GROUP_ORDER:
            ordered.append({"name": name, "items": group_items})
    return ordered


# --------------------------------------------------------------------------- #
# 应用工厂
# --------------------------------------------------------------------------- #
def create_app(config: dict | None = None) -> Flask:
    # 某些 Windows 机器 getfqdn 会卡 10 秒以上，先打补丁再建服务
    from core.netutil import patch_fast_getfqdn

    patch_fast_getfqdn()

    cfg = config or load_config()
    kernel_urls = KernelURLs(cfg)

    setup_logging(LOGS_DIR, cfg["log"]["level"], cfg["log"]["keep_days"])
    access.init_access(LOGS_DIR, cfg["log"]["access_keep_days"])

    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path=kernel_urls.static_prefix,
    )

    app.config["SECRET_KEY"] = cfg["app"]["secret_key"]
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_FILE.as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["PLUGIN_AUTO_LOAD"] = bool(cfg["plugin"]["auto_load"])
    app.config["SAFE_MODE"] = bool(cfg["plugin"]["safe_mode"])
    app.config["OA_CONFIG"] = cfg
    app.config["KERNEL_URLS"] = kernel_urls.to_dict()
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    # 让其它模块（admin / plugin_manager）通过 extensions 拿到
    app.extensions["kernel_urls"] = kernel_urls

    db.init_app(app)
    login_manager.init_app(app)

    # ---- 注册蓝图（前缀来自 kernel 配置） ----
    app.register_blueprint(auth_bp, url_prefix=kernel_urls.auth_prefix)
    app.register_blueprint(admin_bp, url_prefix=kernel_urls.admin_prefix)
    app.register_blueprint(users_bp, url_prefix=kernel_urls.admin_prefix)
    app.register_blueprint(service_bp, url_prefix=kernel_urls.admin_prefix)

    # ---- 注册内核独立路由 ----
    app.add_url_rule(kernel_urls.home_url, "core.index", view_index)
    app.add_url_rule(f"{kernel_urls.system_prefix}/health", "core.health", view_health)
    app.add_url_rule(
        f"{kernel_urls.plugin_static_prefix}/<full_id>/<path:filename>",
        "core.plugin_static",
        view_plugin_static,
    )

    with app.app_context():
        db.create_all()
        migrate_schema()          # 老数据库补 menus.endpoint 字段
        ensure_default_admin()
        _register_kernel_routes_to_table(kernel_urls)

    access.register_http_logging(app)
    _inject_globals(app, kernel_urls)
    _register_error_handlers(app)
    _register_headers(app)
    _consume_restart_flag()

    if app.config["SAFE_MODE"]:
        logger.warning("=== 安全模式：跳过所有插件加载 ===")
    else:
        manager = PluginManager(app)
        app.extensions["plugin_manager"] = manager
        with app.app_context():
            manager.load_all()

    logger.info("内核初始化完成，端口=%s，插件自动加载=%s，安全模式=%s",
                cfg["app"].get("port"), app.config["PLUGIN_AUTO_LOAD"],
                app.config["SAFE_MODE"])
    return app


# --------------------------------------------------------------------------- #
# 错误页 / 响应头
# --------------------------------------------------------------------------- #
def _wants_json() -> bool:
    if request.path.endswith(".json"):
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best
    return best == "application/json" and \
        request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]


def _register_error_handlers(app: Flask) -> None:
    def _render(code: int, title: str, message: str):
        if _wants_json():
            return jsonify({"ok": False, "error": message, "code": code}), code
        return render_template(
            "errors/error.html", title=title, message=message, code=code,
        ), code

    @app.errorhandler(403)
    def _403(err):
        return _render(403, "没有权限", getattr(err, "description", "缺少权限"))

    @app.errorhandler(404)
    def _404(err):
        return _render(404, "页面不存在", "你访问的地址不存在或已被移动")

    @app.errorhandler(500)
    def _500(err):
        logger.exception("服务器内部错误: %s", err)
        return _render(500, "服务器错误", "服务器内部错误，请查看应用日志")

    @app.errorhandler(413)
    def _413(err):
        return _render(413, "文件太大", "上传的文件超过大小限制")


def _register_headers(app: Flask) -> None:
    @app.after_request
    def _headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # 管理页面不缓存，避免改了配置看到旧页面
        if "/static/" not in request.path:
            response.headers.setdefault("Cache-Control", "no-store")
        return response
