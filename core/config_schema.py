"""配置项的「可编辑清单」：把 config.json 的每个字段描述成表单。

作用
----
1. 系统设置页按这张表自动渲染表单（分组 + 类型 + 说明 + 是否需重启）；
2. 提交时按这张表逐项做类型/范围校验，避免把非法值写进 config.json；
3. 「高级」模式允许直接粘贴任意 JSON 片段，实现真正的"所有配置都能改"
   （包括将来新增、这张表还没收录的字段）。

字段说明
--------
key     配置键（相对 section）
label   中文标签
type    text / secret / int / bool / select / list / path / json
help    字段说明（页面展示）
restart 改完是否需要重启服务才生效
hot     改完立即生效（例如保活开关、注册开关）
"""
from __future__ import annotations

import json
from typing import Any

from core.config import DEFAULT_CONFIG, _norm_path, get_config

# --------------------------------------------------------------------------- #
# 可编辑配置
# --------------------------------------------------------------------------- #
CONFIG_SCHEMA: list[dict] = [
    {
        "section": "app",
        "title": "应用",
        "desc": "系统名称、监听端口、会话密钥。端口改完需要重启服务。",
        "fields": [
            {"key": "name", "label": "系统名称", "type": "text",
             "help": "显示在登录页、导航栏和浏览器标题里"},
            {"key": "port", "label": "监听端口", "type": "int", "min": 1, "max": 65535,
             "restart": True,
             "help": "默认 80（Windows 上 80 常被系统组件占用，建议 8080）。"
                     "保存后可在服务控制台一键重启生效，端口被占用时守护会自动换用备用端口。"},
            {"key": "secret_key", "label": "Flask 密钥", "type": "secret", "restart": True,
             "help": "会话签名密钥，留空下次启动会自动生成"},
            {"key": "back_secret_key", "label": "备用密钥", "type": "secret", "restart": True,
             "help": "预留：多实例/滚动重启时使用"},
            {"key": "admin_url", "label": "旧版首页地址", "type": "path",
             "help": "兼容字段，等价于 kernel.home_url，建议只改后面那个"},
        ],
    },
    {
        "section": "kernel",
        "title": "内核 URL 前缀",
        "desc": "内核各模块的命名空间前缀，改完需要重启。插件 URL 不在这里配置。",
        "fields": [
            {"key": "home_url", "label": "首页", "type": "path", "restart": True,
             "help": "根路径，例如 /"},
            {"key": "auth_prefix", "label": "登录模块", "type": "path", "restart": True,
             "help": "具体页面：<前缀>/login、<前缀>/register、<前缀>/profile"},
            {"key": "admin_prefix", "label": "管理模块", "type": "path", "restart": True,
             "help": "具体页面：<前缀>/plugins、/routes、/users、/service、/settings、/logs"},
            {"key": "system_prefix", "label": "系统接口", "type": "path", "restart": True,
             "help": "健康检查：<前缀>/health"},
            {"key": "static_prefix", "label": "内核静态资源", "type": "path", "restart": True,
             "help": "CSS/JS/图片目录"},
            {"key": "plugin_static_prefix", "label": "插件静态资源", "type": "path",
             "restart": True, "help": "插件静态文件：<前缀>/<full_id>/<文件>"},
            {"key": "plugin_fallback_prefix", "label": "插件兜底前缀", "type": "path",
             "restart": True, "help": "插件 URL 与内核冲突时自动落到 <前缀>/<full_id>"},
        ],
    },
    {
        "section": "log",
        "title": "日志",
        "desc": "日志级别与保留天数，改完需要重启。",
        "fields": [
            {"key": "level", "label": "日志级别", "type": "select", "restart": True,
             "options": ["DEBUG", "INFO", "WARNING", "ERROR"]},
            {"key": "keep_days", "label": "应用日志保留天数", "type": "int",
             "min": 1, "max": 3650, "restart": True},
            {"key": "access_keep_days", "label": "访问日志保留天数", "type": "int",
             "min": 1, "max": 3650, "restart": True},
        ],
    },
    {
        "section": "ui",
        "title": "桌面面板",
        "desc": "PySide6 控制面板的显示参数。",
        "fields": [
            {"key": "access_rows", "label": "访问记录显示条数", "type": "int",
             "min": 50, "max": 5000, "restart": True},
            {"key": "minimize_to_tray", "label": "最小化到托盘", "type": "bool"},
            {"key": "refresh_ms", "label": "状态刷新间隔（毫秒）", "type": "int",
             "min": 500, "max": 60000, "restart": True},
        ],
    },
    {
        "section": "plugin",
        "title": "插件",
        "desc": "插件加载开关与安全模式。",
        "fields": [
            {"key": "auto_load", "label": "启动时自动加载插件", "type": "bool", "restart": True},
            {"key": "safe_mode", "label": "安全模式", "type": "bool", "restart": True,
             "help": "开启后重启会跳过所有插件加载；插件反复崩溃时守护进程会自动打开"},
        ],
    },
    {
        "section": "service",
        "title": "服务控制",
        "desc": "守护进程（保活）行为。保活、Web 控制台开关立即生效。",
        "fields": [
            {"key": "keepalive", "label": "保活（崩溃自动拉起）", "type": "bool", "hot": True,
             "help": "关闭后 app 崩溃不会被自动拉起，但仍可手动启动"},
            {"key": "auto_start", "label": "内核启动时自动拉起服务", "type": "bool", "restart": True},
            {"key": "watch_window", "label": "失败统计窗口（秒）", "type": "int",
             "min": 10, "max": 3600},
            {"key": "watch_threshold", "label": "窗口内失败次数阈值", "type": "int",
             "min": 1, "max": 100, "help": "达到阈值自动进入安全模式"},
            {"key": "restart_delay", "label": "重启前延迟（秒）", "type": "int",
             "min": 0, "max": 600, "help": "防止崩溃循环把 CPU 打满"},
            {"key": "web_control", "label": "允许 Web 控制台控制服务", "type": "bool",
             "hot": True, "help": "关闭后 Web 上的启动/停止/重启按钮会被拒绝"},
            {"key": "status_page", "label": "服务停止时保留引导页", "type": "bool",
             "help": "停止服务后，守护进程在同一端口显示「已停止 + 启动按钮」"},
        ],
    },
    {
        "section": "user",
        "title": "用户",
        "desc": "注册策略与密码强度。注册开关立即生效。",
        "fields": [
            {"key": "allow_register", "label": "开放自助注册", "type": "bool", "hot": True,
             "help": "开启后登录页出现「注册」入口"},
            {"key": "password_min_length", "label": "密码最小长度", "type": "int",
             "min": 4, "max": 64},
            {"key": "default_permissions", "label": "新用户默认权限码",
             "type": "list", "help": "逗号分隔，例如 kernel.logs.view"},
        ],
    },
    {
        "section": "alert",
        "title": "告警",
        "desc": "进入安全模式 / 服务异常时推送 webhook。",
        "fields": [
            {"key": "webhook", "label": "Webhook 地址", "type": "text",
             "help": "留空则不推送（企业微信/钉钉机器人地址均可）"},
            {"key": "timeout", "label": "推送超时（秒）", "type": "int",
             "min": 1, "max": 60},
        ],
    },
]


# --------------------------------------------------------------------------- #
# 读取 / 写回
# --------------------------------------------------------------------------- #
def iter_fields():
    """遍历 (section, field) 二元组。"""
    for group in CONFIG_SCHEMA:
        for field in group["fields"]:
            yield group["section"], field


def all_paths() -> list[str]:
    return [f"{sec}.{f['key']}" for sec, f in iter_fields()]


def get_value(cfg: dict, section: str, key: str) -> Any:
    section_cfg = cfg.get(section) or {}
    if key in section_cfg:
        return section_cfg[key]
    return (DEFAULT_CONFIG.get(section) or {}).get(key)


def display_value(cfg: dict, section: str, key: str, ftype: str) -> str:
    """把配置值转成表单里显示的样子。"""
    value = get_value(cfg, section, key)
    if value is None:
        return ""
    if ftype == "list":
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)
    if ftype == "json":
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value)


# --------------------------------------------------------------------------- #
# 校验 + 生成 patch
# --------------------------------------------------------------------------- #
def _coerce(field: dict, raw: str) -> Any:
    ftype = field["type"]
    label = field.get("label") or field["key"]

    if ftype == "bool":
        return str(raw).lower() in ("1", "true", "on", "yes")

    if ftype == "int":
        text = (raw or "").strip()
        if text == "":
            raise ValueError(f"{label} 不能为空")
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{label} 必须是整数") from None
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and value < lo:
            raise ValueError(f"{label} 不能小于 {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"{label} 不能大于 {hi}")
        return value

    if ftype == "select":
        text = (raw or "").strip()
        options = field.get("options") or []
        if options and text not in options:
            raise ValueError(f"{label} 只能取 {options}")
        return text

    if ftype == "path":
        text = (raw or "").strip()
        if not text.startswith("/"):
            raise ValueError(f"{label} 必须以 / 开头")
        return _norm_path(text)

    if ftype == "list":
        text = (raw or "").strip()
        if not text:
            return []
        return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]

    if ftype == "json":
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} 不是合法 JSON：{exc}") from None

    # text / secret
    return (raw or "").strip()


def apply_form(form, cfg: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """把表单转换成 patch。

    返回 (patch, errors, restart_paths)。
    只处理"表单里出现过"的字段（bool 靠隐藏域 __bool__<path> 判断提交过），
    这样分模块提交也不会把没提交的段清空。
    """
    cfg = cfg or get_config()
    patch: dict = {}
    errors: list[str] = []
    restart_paths: list[str] = []

    for section, field in iter_fields():
        key = field["key"]
        path = f"{section}.{key}"
        ftype = field["type"]

        if ftype == "bool":
            if f"__bool__{path}" not in form:
                continue
            raw = "1" if form.get(path) in ("on", "1", "true") else "0"
        else:
            if path not in form:
                continue
            raw = form.get(path) or ""

        try:
            value = _coerce(field, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        old = get_value(cfg, section, key)
        if value != old:
            patch.setdefault(section, {})[key] = value
            if field.get("restart"):
                restart_paths.append(path)

    return patch, errors, restart_paths


def validate_patch(patch: dict, cfg: dict | None = None) -> list[str]:
    """校验「高级 JSON」提交的片段：必须能深合并，且已知字段要通过类型校验。"""
    cfg = cfg or get_config()
    errors: list[str] = []
    if not isinstance(patch, dict):
        return ["JSON 顶层必须是对象，例如 {\"app\": {\"port\": 8080}}"]

    known = {(sec, f["key"]): f for sec, f in iter_fields()}
    for section, values in patch.items():
        if not isinstance(values, dict):
            errors.append(f"配置段 {section} 必须是对象")
            continue
        for key, value in values.items():
            field = known.get((section, key))
            if field is None:
                continue  # 表里没有的键：允许写入（面向未来扩展）
            ftype = field["type"]
            try:
                if ftype == "int" and not isinstance(value, int):
                    raise ValueError(f"{section}.{key} 必须是整数")
                if ftype == "bool" and not isinstance(value, bool):
                    raise ValueError(f"{section}.{key} 必须是布尔值")
                if ftype in ("text", "secret", "select", "path") and \
                        not isinstance(value, str):
                    raise ValueError(f"{section}.{key} 必须是字符串")
                if ftype == "list" and not isinstance(value, list):
                    raise ValueError(f"{section}.{key} 必须是数组")
                if ftype == "int":
                    lo, hi = field.get("min"), field.get("max")
                    if lo is not None and value < lo:
                        raise ValueError(f"{section}.{key} 不能小于 {lo}")
                    if hi is not None and value > hi:
                        raise ValueError(f"{section}.{key} 不能大于 {hi}")
            except ValueError as exc:
                errors.append(str(exc))
    return errors


def restart_paths_for(patch: dict) -> list[str]:
    """patch 里哪些字段需要重启才生效。"""
    mapping = {f"{sec}.{f['key']}": bool(f.get("restart")) for sec, f in iter_fields()}
    paths: list[str] = []
    for section, values in (patch or {}).items():
        if not isinstance(values, dict):
            continue
        for key in values:
            path = f"{section}.{key}"
            if mapping.get(path):
                paths.append(path)
    return paths
