"""内核路径常量 + 配置加载 + 配置读写。

路径分两类：
- 可写数据根 BASE_DIR：EXE 所在目录 / 开发时项目根
  （data/、plugins/、staging/、config.json）
- 只读资源根 RESOURCE_DIR：打包后在 _internal；开发时项目根
  （templates/、static/）

URL 分两类：
- 内核 URL：全部由 config.json 的 kernel 段控制（KernelURLs）
- 插件 URL：由插件在 plugin.json 声明 url_prefix，内核分配 actual_prefix

配置分七段（全部落在 config.json，可被
「管理 → 系统设置」页 / 桌面控制面板 / 守护进程修改）：
    app      系统名、端口、密钥
    kernel   内核 URL 前缀
    log      日志级别与保留天数
    ui       桌面面板外观
    plugin   插件自动加载 / 安全模式
    service  服务控制（保活、自动启动、失败窗口、Web 控制台）
    user     用户注册策略
    alert    告警推送（webhook）

注意：KernelURLs 里存的是"命名空间前缀"，不是可直接访问的页面。
想访问具体页面，请用 sample_url() 拿示例路径。
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

from core.store import deep_merge, load_json, save_json


def _runtime_dir() -> Path:
    """可写数据根：EXE 所在目录 / 开发时项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resource_dir() -> Path:
    """只读资源根：打包后在 _internal；开发时项目根。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
    return Path(__file__).resolve().parent.parent


# 可写数据根（EXE 同级）
BASE_DIR: Path = _runtime_dir()

# 只读资源根（模板 / 静态资源）
RESOURCE_DIR: Path = _resource_dir()

DATA_DIR: Path = BASE_DIR / "data"
PLUGINS_DIR: Path = BASE_DIR / "plugins"
LOGS_DIR: Path = DATA_DIR / "logs"
STAGING_DIR: Path = BASE_DIR / "staging"
CONFIG_FILE: Path = BASE_DIR / "config.json"
DB_FILE: Path = DATA_DIR / "core.db"
RESTART_FLAG: Path = BASE_DIR / "restart.flag"

# 运行时状态与 Web 控制台指令通道
RUNTIME_FILE: Path = DATA_DIR / "runtime.json"
CONTROL_FILE: Path = DATA_DIR / "control.json"
SAFE_MODE_FILE: Path = DATA_DIR / "safe_mode.json"
LAST_RESTART_FILE: Path = DATA_DIR / "last_restart.json"

TEMPLATES_DIR: Path = RESOURCE_DIR / "templates"
STATIC_DIR: Path = RESOURCE_DIR / "static"

HOST = "0.0.0.0"  # 写死，不可配

# 内核版本（显示在侧边栏 / 关于信息里）
KERNEL_VERSION = "1.0.0"

# 端口被占用时依次尝试的备用端口
PORT_FALLBACKS: tuple[int, ...] = (8080, 8000, 5000, 8888, 9000)


# --------------------------------------------------------------------------- #
# 默认配置
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG: dict = {
    "app": {
        "name": "Flask微内核",
        "port": 80,
        "secret_key": "",
        "back_secret_key": "",
        # admin_url 保留兼容；新配置请用 kernel.home_url
        "admin_url": "/",
    },
    "kernel": {
        # 内核命名空间前缀（全部可配，改完重启生效）
        "home_url": "/",                    # 首页（根路径，也是完整 URL）
        "auth_prefix": "/auth",             # 登录模块前缀 → /auth/login、/auth/logout
        "admin_prefix": "/admin",           # 管理模块前缀 → /admin/plugins、/admin/routes
        "system_prefix": "/system",         # 系统模块前缀 → /system/health
        "static_prefix": "/static",         # 内核静态资源前缀
        "plugin_static_prefix": "/plugin-static",  # 插件静态资源前缀
        "plugin_fallback_prefix": "/plugin",       # 插件 URL 冲突时的兜底前缀
    },
    "log": {
        "level": "INFO",
        "keep_days": 30,
        "access_keep_days": 30,
    },
    "ui": {
        "access_rows": 500,
        "minimize_to_tray": True,
        "refresh_ms": 2000,
    },
    "plugin": {
        "auto_load": True,
        "safe_mode": False,
        # 插件 URL 映射覆盖：{"插件id": "/新路径"}，由「URL 映射」页写入，重启后仍生效
        "path_overrides": {},
    },
    "service": {
        "keepalive": True,       # 保活：app 退出后自动拉起
        "auto_start": True,      # 内核启动时是否自动拉起 app
        "watch_window": 60,      # 失败统计滑动窗口（秒）
        "watch_threshold": 5,    # 窗口内失败次数阈值 → 安全模式
        "restart_delay": 3,      # 重启前延迟（秒），防 CPU 烧
        "web_control": True,     # 是否允许 Web 控制台控制服务
        "status_page": True,     # app 停止时守护在相同端口提供引导页
    },
    "user": {
        "allow_register": False,      # 是否允许自助注册
        "password_min_length": 6,     # 密码最小长度
        "default_permissions": [],    # 新注册用户的默认权限码
    },
    "alert": {
        "webhook": "",       # 告警 webhook 地址（留空不推送）
        "timeout": 5,        # 推送超时（秒）
    },
}


# --------------------------------------------------------------------------- #
# 内核 URL 配置对象
# --------------------------------------------------------------------------- #
def _norm_path(p: str) -> str:
    """规范化 URL 前缀：以 / 开头，无尾斜杠（根路径除外）。"""
    if not p:
        p = "/"
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    return p or "/"


class KernelURLs:
    """内核 URL 集合，从 config.json 的 kernel 段读取。

    这些是"命名空间前缀"，不是页面。例如 auth_prefix="/auth"，
    具体页面是 /auth/login、/auth/logout。
    """

    __slots__ = (
        "home_url", "auth_prefix", "admin_prefix", "system_prefix",
        "static_prefix", "plugin_static_prefix", "plugin_fallback_prefix",
    )

    def __init__(self, cfg: dict):
        k = cfg.get("kernel") or {}
        app_cfg = cfg.get("app") or {}

        # 兼容旧字段 app.admin_url
        home = k.get("home_url") or app_cfg.get("admin_url") or "/"

        self.home_url = _norm_path(home)
        self.auth_prefix = _norm_path(k.get("auth_prefix", "/auth"))
        self.admin_prefix = _norm_path(k.get("admin_prefix", "/admin"))
        self.system_prefix = _norm_path(k.get("system_prefix", "/system"))
        self.static_prefix = _norm_path(k.get("static_prefix", "/static"))
        self.plugin_static_prefix = _norm_path(
            k.get("plugin_static_prefix", "/plugin-static")
        )
        self.plugin_fallback_prefix = _norm_path(
            k.get("plugin_fallback_prefix", "/plugin")
        )

    def reserved(self) -> set[str]:
        """内核保留的所有前缀，用于插件 URL 冲突检测。"""
        return {
            self.home_url,
            self.auth_prefix,
            self.admin_prefix,
            self.system_prefix,
            self.static_prefix,
            self.plugin_static_prefix,
            self.plugin_fallback_prefix,
        }

    def as_route_rows(self) -> list[tuple[str, str]]:
        """返回 [(config_key, path), ...]，用于写入 Route 表做展示/编辑。"""
        return [
            ("home_url", self.home_url),
            ("auth_prefix", self.auth_prefix),
            ("admin_prefix", self.admin_prefix),
            ("system_prefix", self.system_prefix),
            ("static_prefix", self.static_prefix),
            ("plugin_static_prefix", self.plugin_static_prefix),
            ("plugin_fallback_prefix", self.plugin_fallback_prefix),
        ]

    def sample_url(self, key: str) -> str | None:
        """返回该前缀下真实可访问的示例路径；无则返回 None。

        用于映射页展示，避免用户点了前缀本身 404。
        """
        return {
            "home_url": self.home_url,
            "auth_prefix": f"{self.auth_prefix}/login",
            "admin_prefix": f"{self.admin_prefix}/plugins",
            "system_prefix": f"{self.system_prefix}/health",
            "static_prefix": None,           # 需要具体文件名
            "plugin_static_prefix": None,    # 需要具体插件
            "plugin_fallback_prefix": None,  # 需要具体插件
        }.get(key)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self) -> str:
        return f"<KernelURLs {self.to_dict()}>"


# --------------------------------------------------------------------------- #
# 目录 / 配置
# --------------------------------------------------------------------------- #
def ensure_dirs() -> None:
    """只创建可写目录；只读资源由打包时提供，不在这里建。"""
    for d in (DATA_DIR, PLUGINS_DIR, LOGS_DIR, STAGING_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """读 config.json -> 与默认配置深合并 -> 补齐密钥 -> 有变化写回。"""
    ensure_dirs()

    raw = load_json(CONFIG_FILE, {}) or {}
    if not isinstance(raw, dict):
        raw = {}

    cfg = deep_merge(DEFAULT_CONFIG, raw)
    changed = False

    if not cfg["app"].get("secret_key"):
        cfg["app"]["secret_key"] = secrets.token_hex(32)
        changed = True
    if not cfg["app"].get("back_secret_key"):
        cfg["app"]["back_secret_key"] = secrets.token_hex(32)
        changed = True

    if changed or cfg != raw:
        save_json(CONFIG_FILE, cfg)

    return cfg


_config_cache: dict | None = None


def get_config(reload: bool = False) -> dict:
    global _config_cache
    if _config_cache is None or reload:
        _config_cache = load_config()
    return _config_cache


def clear_config_cache() -> None:
    """配置被外部改动后清缓存，下次 get_config 重读。"""
    global _config_cache
    _config_cache = None


def update_config(patch: dict) -> dict:
    """把 patch 深合并进 config.json 并返回新配置。"""
    raw = load_json(CONFIG_FILE, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = deep_merge(DEFAULT_CONFIG, raw)
    merged = deep_merge(cfg, patch or {})
    save_json(CONFIG_FILE, merged)
    clear_config_cache()
    return merged


def set_config_path(dotted: str, value) -> dict:
    """按点号路径写单个配置项，如 set_config_path("service.keepalive", False)。"""
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        raise ValueError("配置路径不能为空")
    patch: dict = {}
    cursor = patch
    for key in parts[:-1]:
        cursor[key] = {}
        cursor = cursor[key]
    cursor[parts[-1]] = value
    return update_config(patch)


def update_kernel_url(key: str, value: str) -> None:
    """写回 config.json 的 kernel.<key>。"""
    set_config_path(f"kernel.{key}", value)


def set_plugin_path_override(plugin_id: str, path: str) -> None:
    """写插件的 URL 映射覆盖。

    插件 id 里带点（yezhongtao.erp.bridge），不能走 set_config_path 的点号路径，
    所以这里直接改 plugin.path_overrides 这个字典。
    """
    raw = load_json(CONFIG_FILE, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    plugin = raw.get("plugin")
    if not isinstance(plugin, dict):
        plugin = {}
        raw["plugin"] = plugin
    current = plugin.get("path_overrides")
    overrides = {}
    if isinstance(current, dict):
        overrides = {key: value for key, value in current.items()
                     if isinstance(value, str)}
    overrides[str(plugin_id)] = str(path)
    plugin["path_overrides"] = overrides
    save_json(CONFIG_FILE, raw)
    clear_config_cache()


def plugin_path_overrides() -> dict:
    """读取 plugin.path_overrides（只保留字符串值）。"""
    overrides = ((get_config() or {}).get("plugin") or {}).get("path_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    return {key: value for key, value in overrides.items() if isinstance(value, str)}


def get_section(name: str) -> dict:
    """取一个配置段（不存在返回空 dict）。"""
    sec = (get_config() or {}).get(name)
    return sec if isinstance(sec, dict) else {}
