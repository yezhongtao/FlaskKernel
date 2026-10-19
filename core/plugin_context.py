"""PluginContext：暴露给插件的全部能力。"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import render_template, url_for


class PluginContext:
    def __init__(self, plugin_id: str, name: str, label: str, version: str,
                 base_dir: Path, data_dir: Path, app, manager,
                 url_prefix: str, provides: dict, dependencies: dict):
        self._id = plugin_id
        self._name = name
        self._label = label or name
        self._version = version
        self._base_dir = Path(base_dir)
        self._data_dir = Path(data_dir)
        self._app = app
        self._manager = manager
        self._url_prefix = url_prefix
        self._provides_spec = provides or {}
        self._dependencies = dependencies or {}
        self._logger = logging.getLogger(f"plugin.{plugin_id}")

    # ---------------- 属性 ----------------
    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def label(self) -> str:
        return self._label

    @property
    def version(self) -> str:
        return self._version

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def app(self):
        return self._app

    @property
    def url_prefix(self) -> str:
        return self._url_prefix

    # ---------------- 注册 ----------------
    def register_blueprint(self, bp) -> None:
        if not hasattr(bp, "name"):
            raise TypeError("register_blueprint 需要一个 Flask Blueprint 实例")
        # 关键：把蓝图 name 改写成 full_id（Flask 构造函数禁止 name 带点，
        # 我们在注册前手动赋值绕过校验），保证 url_for / request.endpoint 正确。
        bp.name = self._id
        # 每次注册都用本次分配的地址覆盖：蓝图对象是模块级单例，
        # 改了 URL 映射后重启如果不强制覆盖，仍会沿用上一次的 bp.url_prefix
        bp.url_prefix = self._url_prefix
        self._app.register_blueprint(bp)
        self._manager._on_blueprint_registered(self._id, bp)

    def register_menus(self, menus: list) -> None:
        self._manager._register_menus(self._id, menus or [])

    def provide(self, key: str, obj) -> None:
        spec = self._provides_spec.get(key, {})
        self._manager._register_provider(self._id, key, obj, spec)

    def on(self, event: str, handler) -> None:
        self._manager._register_event(self._id, event, handler)

    # ---------------- 获取 ----------------
    def require(self, key: str):
        return self._manager._require(self._id, key, self._dependencies)

    def get(self, key: str, default=None):
        try:
            return self.require(key)
        except Exception:
            return default

    # ---------------- 渲染 ----------------
    def render(self, name: str, **ctx):
        template_name = name if name.startswith(self._id + "/") else f"{self._id}/{name}"
        ctx.setdefault("static_url", self.static_url)
        ctx.setdefault("plugin_id", self._id)
        ctx.setdefault("plugin_label", self._label)
        return render_template(template_name, **ctx)

    def static_url(self, filename: str) -> str:
        return url_for("core.plugin_static", full_id=self._id, filename=filename)

    # ---------------- 日志 ----------------
    def log(self) -> logging.Logger:
        return self._logger

    def __repr__(self) -> str:
        return f"<PluginContext {self._id}>"