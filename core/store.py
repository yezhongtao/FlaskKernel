"""JSON 存储工具：读 / 原子写 / 深合并。"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: str | Path, default: Any = None) -> Any:
    """读取 JSON。文件不存在 / 损坏 / 无权限时返回 default。"""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return copy.deepcopy(default)


def save_json(path: str | Path, data: Any) -> None:
    """原子写 JSON：先写临时文件再 replace。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def deep_merge(base: dict, override: dict | None) -> dict:
    """递归合并，override 优先；不修改传入的 dict。"""
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result