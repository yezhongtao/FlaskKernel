"""文件预览插件的设置。

    roots         允许浏览的目录（相对项目根；空 = 只开放项目根）
    max_text_mb   文本/代码预览上限
    max_rows      表格预览最大行数
    max_cols      表格预览最大列数
    show_hidden   是否显示 . 开头的隐藏文件
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULTS: dict = {
    "roots": [],
    "max_text_mb": 2,
    "max_rows": 500,
    "max_cols": 40,
    "show_hidden": False,
}

SCHEMA: list[dict] = [
    {
        "section": "preview",
        "title": "浏览范围",
        "desc": "只允许预览这些目录里的文件；留空 = 项目根目录。每行一个，可用相对路径。",
        "fields": [
            {"key": "roots", "label": "可浏览目录", "type": "list",
             "help": "例如 data/logs、plugins/yezhongtao.oa.notice；留空表示整个项目根"},
            {"key": "show_hidden", "label": "显示隐藏文件", "type": "bool",
             "help": "以 . 开头的文件/目录默认不显示"},
        ],
    },
    {
        "section": "preview",
        "title": "预览上限",
        "desc": "防止一个超大文件把页面卡死。",
        "fields": [
            {"key": "max_text_mb", "label": "文本上限（MB）", "type": "int", "min": 1, "max": 50},
            {"key": "max_rows", "label": "表格最大行数", "type": "int", "min": 20, "max": 5000},
            {"key": "max_cols", "label": "表格最大列数", "type": "int", "min": 5, "max": 100},
        ],
    },
]

_BOOL_TRUE = {"1", "true", "on", "yes"}


def deep_merge(base: dict, override: dict | None) -> dict:
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load(path: str | Path) -> dict:
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = {}
    return deep_merge(DEFAULTS, data if isinstance(data, dict) else {})


def save(path: str | Path, settings: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def normalize_roots(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("，", ",").replace("\n", ",").split(",")]
    roots: list[str] = []
    for item in raw or []:
        text = str(item or "").strip().replace("\\", "/").strip("/")
        if text and text not in roots:
            roots.append(text)
    return roots


def _coerce(field: dict, raw):
    ftype = field["type"]
    if ftype == "bool":
        return str(raw).lower() in _BOOL_TRUE
    if ftype == "int":
        text = str(raw).strip()
        value = int(float(text))
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and value < lo:
            raise ValueError(f"{field['label']} 不能小于 {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"{field['label']} 不能大于 {hi}")
        return value
    if ftype == "list":
        return normalize_roots(str(raw or ""))
    return str(raw or "").strip()


def apply_form(form, settings: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    changed: list[str] = []
    for group in SCHEMA:
        for field in group["fields"]:
            key = field["key"]
            ftype = field["type"]
            if ftype == "bool":
                if f"__bool__{key}" not in form:
                    continue
                raw = "1" if form.get(key) in ("on", "1", "true") else "0"
            else:
                if key not in form:
                    continue
                raw = form.get(key)
            try:
                value = _coerce(field, raw)
            except (TypeError, ValueError) as exc:
                errors.append(f"{field['label']}：{exc}")
                continue
            if settings.get(key) != value:
                settings[key] = value
                changed.append(key)
    return errors, changed


def validate(settings: dict) -> list[str]:
    errors: list[str] = []
    if int(settings.get("max_text_mb") or 0) < 1:
        errors.append("文本上限至少 1 MB")
    if int(settings.get("max_rows") or 0) < 1:
        errors.append("表格最大行数至少 1")
    return errors
