"""文件预览插件入口。

提供给其它插件的接口：`preview.api`
    roots() / list_dir(path) / preview(path) / kind_of(path) / get_settings()
"""
from __future__ import annotations
from flask import Blueprint, render_template

from core.permission import require_perm

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import preview_config as cfg
import preview_files as files
import preview_views as views

PLUGIN_ID = "yezhongtao.tools.preview"


class PreviewAPI:
    def __init__(self, vault: files.Vault, store, log=None):
        self.vault = vault
        self.store = store
        self.log = log or (lambda msg: None)

    def roots(self) -> list:
        return [str(root) for root in self.vault.roots()]

    def list_dir(self, path: str = "") -> dict:
        return self.vault.list_dir(path)

    def preview(self, path: str) -> dict:
        return self.vault.preview(path)

    def kind_of(self, path: str) -> str:
        return files.kind_of(path)

    def get_settings(self) -> dict:
        return self.store.load_settings()


def register(ctx) -> None:
    log = ctx.log()
    store = _Store(ctx.data_dir)
    settings = store.load_settings()
    project_root = ctx.base_dir.parent.parent

    def make_vault(current: dict) -> files.Vault:
        return files.Vault(project_root, current)

    def save_settings(current: dict) -> None:
        store.save_settings(current)

    api = PreviewAPI(make_vault(settings), store, log=log.info)

    views.configure(
        vault=make_vault(settings),
        settings=settings,
        save_settings=save_settings,
        make_vault=make_vault,
    )

    ctx.register_blueprint(views.bp)
    prefix = ctx.url_prefix
    ctx.register_menus([
        {"name": "preview-home", "label": "文件预览", "endpoint": "page",
         "url": f"{prefix}/", "perm": "view", "order": 40, "icon": "eye"},
        {"name": "preview-settings", "label": "预览设置", "endpoint": "settings_page",
         "url": f"{prefix}/settings", "perm": "settings", "order": 41, "icon": "gear"},
    ])
    ctx.provide("preview.api", api)
    log.info("文件预览插件就绪：允许目录 %s，根目录（项目相对）%s",
             ", ".join(cfg.normalize_roots(settings.get("roots")) or ["(项目根)"]),
             ".")


class _Store:
    """复用 ERP 插件那套 settings.json 读写（本插件只需要设置）。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"

    def load_settings(self) -> dict:
        return cfg.load(self.settings_path)

    def save_settings(self, settings: dict) -> None:
        cfg.save(self.settings_path, settings)
