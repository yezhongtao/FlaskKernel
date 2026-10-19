#!/usr/bin/env python
"""插件打包工具（方案 12.1）。

用法：
    python tools/pack_plugin.py plugins/yezhongtao.oa.notice
    python tools/pack_plugin.py plugins/yezhongtao.oa.notice -o dist

打包规则：
    打进去                     不打进去
    plugin.json                data/
    hooks.py                   __pycache__/
    libs/                      *.pyc
    templates/                 .git/
    static/                    .venv/
    其他 .py

产物：dist/<author.group.name>.zip，zip 根目录直接是 plugin.json 等文件，
可以直接在「插件管理 → 安装插件」里上传。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_RE = re.compile(r"^\d+(\.\d+)*$")

EXCLUDE_DIRS = {"data", "__pycache__", ".git", ".venv", ".idea", "venv", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".log", ".tmp", ".bak"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}

REQUIRED_KEYS = ("author", "group", "name", "version")


def validate(plugin_dir: Path) -> dict:
    """校验 plugin.json / hooks.py，返回 manifest。"""
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        raise SystemExit(f"[错误] 缺少 plugin.json: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[错误] plugin.json 不是合法 JSON: {exc}") from None

    for key in REQUIRED_KEYS:
        value = manifest.get(key)
        if not value or not isinstance(value, str):
            raise SystemExit(f"[错误] plugin.json 缺少必填字段: {key}")
        if key != "version" and not ID_RE.match(value):
            raise SystemExit(
                f"[错误] 字段 {key} 格式非法（应为 ^[a-z][a-z0-9_]*$）: {value}"
            )

    if not VERSION_RE.match(manifest["version"]):
        raise SystemExit(f"[错误] version 格式非法（应为 x.y.z）: {manifest['version']}")

    if not (plugin_dir / "hooks.py").is_file():
        raise SystemExit(f"[错误] 缺少 hooks.py: {plugin_dir / 'hooks.py'}")

    full_id = f"{manifest['author']}.{manifest['group']}.{manifest['name']}"
    if plugin_dir.name != full_id:
        print(f"[警告] 目录名 {plugin_dir.name} 与 full_id {full_id} 不一致，"
              f"安装后以内核读取的 full_id 为准")

    for field in ("dependencies", "provides", "permissions", "uses_permissions"):
        value = manifest.get(field)
        if value is not None and not isinstance(value, dict):
            raise SystemExit(f"[错误] 字段 {field} 必须是对象")

    return manifest


def iter_files(plugin_dir: Path):
    """产出需要打进 zip 的文件（相对路径）。"""
    for path in sorted(plugin_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(plugin_dir)
        parts = set(rel.parts[:-1])
        if parts & EXCLUDE_DIRS:
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name in EXCLUDE_FILES:
            continue
        yield path, rel


def pack(plugin_dir: Path, out_dir: Path) -> Path:
    manifest = validate(plugin_dir)
    full_id = f"{manifest['author']}.{manifest['group']}.{manifest['name']}"

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{full_id}.zip"
    if zip_path.exists():
        zip_path.unlink()

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for absolute, relative in iter_files(plugin_dir):
            # zip 内用 / 分隔，根目录直接是文件
            arcname = "/".join(relative.parts)
            zf.write(absolute, arcname)
            count += 1

    return zip_path, count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把插件目录打包成可直接上传安装的 zip",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("plugin_dir", help="插件目录，例如 plugins/yezhongtao.oa.notice")
    parser.add_argument("-o", "--out", default="dist", help="输出目录")
    args = parser.parse_args()

    plugin_dir = Path(args.plugin_dir).resolve()
    if not plugin_dir.is_dir():
        print(f"[错误] 目录不存在: {plugin_dir}")
        return 2

    zip_path, count = pack(plugin_dir, Path(args.out).resolve())
    size_kb = zip_path.stat().st_size / 1024
    print(f"[完成] {zip_path}")
    print(f"       文件 {count} 个，{size_kb:.1f} KB")
    print("       打开「插件管理 → 安装插件」上传这个 zip 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
