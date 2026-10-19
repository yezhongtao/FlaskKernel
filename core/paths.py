"""路径展示工具。

前端（网页 / 桌面 UI / 插件页面）一律显示**项目相对路径**，
不把 `G:\\File\\FlaskKernel\\data\\...` 这种绝对目录暴露出去。

    >>> rel_path(BASE_DIR / "data" / "logs" / "app.log")
    'data/logs/app.log'
    >>> rel_path("data")          # 已经是相对路径，原样返回
    'data'
"""
from __future__ import annotations

from pathlib import Path

from core.config import BASE_DIR


def project_root() -> Path:
    return Path(BASE_DIR).resolve()


def rel_path(path) -> str:
    """把任意路径转成相对项目根目录的 posix 字符串。"""
    if path is None:
        return ""
    text = str(path).strip()
    if not text:
        return ""
    try:
        target = Path(text)
        if not target.is_absolute():
            return target.as_posix().lstrip("./") or "."
        resolved = target.resolve()
        root = project_root()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            # 不在项目里（比如 Python 安装目录）→ 去掉盘符，只给最后两级
            parts = resolved.parts
            return "/".join(parts[-2:]) if len(parts) >= 2 else resolved.as_posix()
        return relative.as_posix() or "."
    except (OSError, ValueError):
        return text


def join_rel(*parts) -> str:
    """拼一个相对路径（用于展示）。"""
    return "/".join(str(part).strip("/\\") for part in parts if str(part).strip("/\\"))


def mask_paths(text: str) -> str:
    """把一段文本里出现的项目绝对路径换成相对写法（日志展示用）。

    `G:\\File\\FlaskKernel\\data\\logs\\app.log` → `data/logs/app.log`
    """
    if not text:
        return text or ""
    root = str(project_root())
    variants = {root, root.replace("\\", "/"), root.replace("/", "\\")}
    for variant in sorted(variants, key=len, reverse=True):
        for sep in ("\\", "/"):
            text = text.replace(variant + sep, "")
        text = text.replace(variant, ".")
    return text
