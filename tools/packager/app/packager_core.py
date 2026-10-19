"""插件打包工具的核心逻辑（不依赖 Flask，可单独调用）。

职责
----
1. 扫描插件目录：读已有 plugin.json、列出文件、收集 import
2. 依赖分析：把 import 分成 标准库 / 内核提供 / 插件本地 / 第三方
3. 自动内置依赖：把第三方依赖从**当前 Python 环境**复制进插件 libs/，
   这样插件包自带运行所需代码，安装到别的机器也不用再 pip；
   本机没装的依赖才尝试 `pip install --target libs`。
4. 声明校验：author/group/name/version/权限/接口/URL 等
5. 打包 zip：根目录直接放 plugin.json，排除 data/、__pycache__ 等

设计约定（与内核 core/plugin_manager.py 保持一致）：
- 插件 ID：author.group.name，三段均为 ^[a-z][a-z0-9_]*$
- 版本：x.y.z（数字段）
- 内核已有库不能放进 libs/：flask / flask_login / flask_sqlalchemy /
  werkzeug / sqlalchemy / jinja2（含它们的发行版名）
"""
from __future__ import annotations

import ast
import importlib.metadata as md
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_RE = re.compile(r"^\d+(\.\d+)*$")
FULL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# 内核已提供、禁止打包的模块名
KERNEL_MODULES = {
    "flask", "flask_login", "flask_sqlalchemy", "werkzeug",
    "sqlalchemy", "jinja2", "core", "user",
}
# 内核已提供、禁止打包的发行版名（小写）
KERNEL_DISTS = {
    "flask", "flask-login", "flask-sqlalchemy", "werkzeug", "sqlalchemy",
    "jinja2", "markupsafe", "itsdangerous", "click", "blinker",
}
# 永远不该进插件包的包（工具链 / 构建期依赖）
ALWAYS_SKIP_DISTS = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib"}

EXCLUDE_DIRS = {
    "data", "__pycache__", ".git", ".venv", "venv", ".idea", "node_modules",
    ".mypy_cache", ".pytest_cache", "dist", "build",
}
# 扫描 import 时额外跳过的目录：libs/ 是已经内置好的第三方代码，
# 它的 import 不属于"插件自己的依赖"，扫描它会把依赖链越滚越大。
SCAN_SKIP_DIRS = EXCLUDE_DIRS | {"libs"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}

MAX_SCAN_FILES = 2000


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def full_id(manifest: dict) -> str:
    return "{author}.{group}.{name}".format(
        author=(manifest.get("author") or "").strip(),
        group=(manifest.get("group") or "").strip(),
        name=(manifest.get("name") or "").strip(),
    )


def norm_path(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = "/" + value
    value = value.rstrip("/")
    return value or "/"


def _human(size: float) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def resolve_plugin_dir(raw: str) -> Path:
    """把用户输入的路径变成绝对路径（支持带引号、相对路径）。"""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("请先选择插件目录")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


# --------------------------------------------------------------------------- #
# 扫描插件目录
# --------------------------------------------------------------------------- #
def scan_plugin_dir(raw_dir: str) -> dict:
    """读取目录现状：清单、文件、libs、hooks。"""
    path = resolve_plugin_dir(raw_dir)
    if not path.is_dir():
        raise ValueError(f"目录不存在：{path}")

    manifest_path = path / "plugin.json"
    manifest = None
    manifest_error = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                manifest, manifest_error = None, "plugin.json 顶层必须是对象"
        except (OSError, json.JSONDecodeError) as exc:
            manifest_error = f"plugin.json 解析失败：{exc}"

    py_files = sorted(
        str(p.relative_to(path)) for p in path.rglob("*.py")
        if not (set(p.relative_to(path).parts[:-1]) & SCAN_SKIP_DIRS)
    )
    templates = _count_files(path / "templates")
    static = _count_files(path / "static")
    libs_dir = path / "libs"
    libs = sorted(p.name for p in libs_dir.iterdir()) if libs_dir.is_dir() else []
    data_files = _count_files(path / "data")

    return {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "has_manifest": manifest is not None,
        "manifest": manifest or {},
        "manifest_error": manifest_error,
        "has_hooks": (path / "hooks.py").is_file(),
        "py_files": py_files,
        "py_count": len(py_files),
        "templates": templates,
        "static": static,
        "data_files": data_files,
        "libs": libs,
        "libs_count": len(libs),
        "size": _dir_size(path),
        "imports": scan_imports(path),
    }


def _count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    total = 0
    for _ in folder.rglob("*"):
        total += 1
        if total > MAX_SCAN_FILES:
            break
    return total


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------- #
# import 扫描
# --------------------------------------------------------------------------- #
def scan_imports(plugin_dir: Path) -> list[dict]:
    """扫描插件 .py 里的 import，返回 [{module, file, line}]。"""
    found: dict[str, dict] = {}
    for file in sorted(plugin_dir.rglob("*.py")):
        rel = file.relative_to(plugin_dir)
        if set(rel.parts[:-1]) & SCAN_SKIP_DIRS:
            continue
        try:
            source = file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:      # 相对导入，属于插件本地
                    continue
                if node.module:
                    mods = [node.module]
            for name in mods:
                top = name.split(".")[0]
                key = top
                if key not in found:
                    found[key] = {
                        "module": top,
                        "file": str(rel),
                        "line": getattr(node, "lineno", 0),
                    }
    return sorted(found.values(), key=lambda item: item["module"].lower())


def _local_modules(plugin_dir: Path) -> set[str]:
    """插件目录下的本地模块（.py 文件或包目录）。"""
    locals_: set[str] = set()
    for item in plugin_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").is_file():
            locals_.add(item.name)
        elif item.suffix == ".py" and item.name != "__init__.py":
            locals_.add(item.stem)
    libs = plugin_dir / "libs"
    if libs.is_dir():
        for item in libs.iterdir():
            locals_.add(item.stem if item.is_file() else item.name)
    return locals_


def _dist_for_module(module: str) -> list[str]:
    """模块 → 发行版名（可能多个）。"""
    try:
        mapping = md.packages_distributions()
    except Exception:
        return []
    return list(mapping.get(module.split(".")[0], []) or [])


def _dist_version(name: str) -> str:
    try:
        return md.version(name)
    except Exception:
        return ""


def _dist_requires(name: str) -> list[str]:
    """发行版的直接依赖（去掉 extras 与 marker）。"""
    try:
        dist = md.distribution(name)
    except Exception:
        return []
    result: list[str] = []
    for raw in (dist.requires or []):
        base = raw.split(";")[0].strip()
        if not base:
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)", base)
        if match:
            result.append(match.group(1).lower())
    return result


def _is_importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------- #
# 依赖分类
# --------------------------------------------------------------------------- #
def analyze_dependencies(raw_dir: str) -> dict:
    """把 import 分类，并给出每个第三方依赖的状态与建议动作。"""
    path = resolve_plugin_dir(raw_dir)
    if not path.is_dir():
        raise ValueError(f"目录不存在：{path}")

    imports = scan_imports(path)
    local = _local_modules(path)
    libs_dir = path / "libs"
    vendored = set()
    if libs_dir.is_dir():
        for item in libs_dir.iterdir():
            vendored.add(item.stem if item.is_file() else item.name)

    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    rows: list[dict] = []

    for item in imports:
        module = item["module"]
        dists = _dist_for_module(module)
        dist = dists[0] if dists else ""
        dist_l = dist.lower()

        if module in stdlib:
            kind, state = "stdlib", "skip"
        elif module in local and module not in vendored:
            kind, state = "local", "skip"
        elif module in KERNEL_MODULES or dist_l in KERNEL_DISTS:
            kind, state = "kernel", "skip"
        elif module in vendored:
            kind, state = "vendored", "ready"
        else:
            kind = "thirdparty"
            if dist_l in ALWAYS_SKIP_DISTS and dist_l:
                state = "skip"
                kind = "toolchain"
            elif _is_importable(module):
                state = "copy"          # 本机已安装 → 直接复制
            else:
                state = "pip"           # 本机没有 → 尝试 pip 安装
            if not dist and state in ("copy", "pip"):
                state = "unknown"       # 单文件模块，无发行版信息

        rows.append({
            "module": module,
            "file": item["file"],
            "line": item["line"],
            "kind": kind,
            "dist": dist,
            "version": _dist_version(dist) if dist else "",
            "state": state,
        })

    summary = {
        "total": len(rows),
        "thirdparty": sum(1 for r in rows if r["kind"] == "thirdparty"),
        "ready": sum(1 for r in rows if r["state"] == "ready"),
        "copy": sum(1 for r in rows if r["state"] == "copy"),
        "pip": sum(1 for r in rows if r["state"] == "pip"),
        "unknown": sum(1 for r in rows if r["state"] == "unknown"),
        "skip": sum(1 for r in rows if r["state"] == "skip"),
    }
    return {
        "ok": True,
        "path": str(path),
        "modules": rows,
        "summary": summary,
        "has_libs": libs_dir.is_dir(),
    }


# --------------------------------------------------------------------------- #
# 内置依赖
# --------------------------------------------------------------------------- #
def _copy_dist(dist_name: str, libs_dir: Path, include_metadata: bool,
               visited: set[str], results: list[dict]) -> None:
    """把一个发行版的文件复制进 libs/。"""
    key = dist_name.lower()
    if key in visited:
        return
    visited.add(key)

    if key in KERNEL_DISTS or key in ALWAYS_SKIP_DISTS:
        results.append({"dist": dist_name, "action": "skip",
                        "reason": "内核已有 / 构建工具，不打包"})
        return

    try:
        dist = md.distribution(dist_name)
    except Exception as exc:
        results.append({"dist": dist_name, "action": "fail",
                        "error": f"当前环境未安装：{exc}"})
        return

    files = list(dist.files or [])
    copied, skipped, total_bytes = 0, 0, 0
    for rel in files:
        rel_path = Path(str(rel))
        parts = rel_path.parts
        if not parts:
            continue
        if not include_metadata and any(p.endswith(".dist-info") or p.endswith(".egg-info")
                                        for p in parts):
            skipped += 1
            continue
        source = dist.locate_file(rel)
        target = libs_dir / rel_path
        try:
            if not source.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
            total_bytes += target.stat().st_size
        except OSError:
            skipped += 1

    results.append({
        "dist": dist_name,
        "action": "copy" if copied else "fail",
        "version": _dist_version(dist_name),
        "files": copied,
        "bytes": total_bytes,
        "skipped": skipped,
    })

    # 递归处理它的依赖
    for dep in _dist_requires(dist_name):
        _copy_dist(dep, libs_dir, include_metadata, visited, results)


def _copy_single_module(module: str, libs_dir: Path) -> dict:
    """没有发行版信息的单文件模块：按 spec 复制。"""
    try:
        spec = importlib.util.find_spec(module)
    except Exception as exc:
        return {"module": module, "action": "fail", "error": str(exc)}
    if spec is None:
        return {"module": module, "action": "fail", "error": "模块未找到"}

    libs_dir.mkdir(parents=True, exist_ok=True)
    copied, total = 0, 0
    try:
        if spec.submodule_search_locations:
            target = libs_dir / module
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(spec.origin and Path(spec.origin).parent
                            or list(spec.submodule_search_locations)[0],
                            target, dirs_exist_ok=True)
            for item in target.rglob("*"):
                if item.is_file():
                    copied += 1
                    total += item.stat().st_size
        elif spec.origin and Path(spec.origin).is_file():
            target = libs_dir / Path(spec.origin).name
            shutil.copy2(spec.origin, target)
            copied, total = 1, target.stat().st_size
        else:
            return {"module": module, "action": "fail", "error": "无法定位模块文件"}
    except OSError as exc:
        return {"module": module, "action": "fail", "error": str(exc)}

    return {"module": module, "action": "copy", "files": copied, "bytes": total}


def _pip_install(packages: list[str], libs_dir: Path) -> dict:
    """本机没装时，用当前 Python 的 pip 装进 libs/。需要网络。"""
    if not packages:
        return {"action": "skip"}
    libs_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-input",
           "--disable-pip-version-check", "--target", str(libs_dir), *packages]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"action": "fail", "error": str(exc)}
    ok = completed.returncode == 0
    return {
        "action": "pip" if ok else "fail",
        "packages": packages,
        "output": (completed.stdout or "")[-2000:] + (completed.stderr or "")[-2000:],
    }


def vendor_dependencies(raw_dir: str, modules: list[str] | None = None,
                        include_metadata: bool = False) -> dict:
    """把缺失的第三方依赖放进插件 libs/。"""
    path = resolve_plugin_dir(raw_dir)
    if not path.is_dir():
        raise ValueError(f"目录不存在：{path}")

    analysis = analyze_dependencies(str(path))
    targets = []
    for row in analysis["modules"]:
        if rows_state_wants_vendor(row) and (not modules or row["module"] in modules):
            targets.append(row)

    libs_dir = path / "libs"
    results: list[dict] = []
    visited: set[str] = set()
    pip_queue: list[str] = []

    for row in targets:
        dist = row.get("dist") or ""
        module = row["module"]
        if row["state"] == "ready":
            results.append({"module": module, "dist": dist, "action": "skip",
                            "reason": "libs/ 里已有"})
            continue
        if dist:
            _copy_dist(dist, libs_dir, include_metadata, visited, results)
        elif row["state"] == "copy":
            results.append(_copy_single_module(module, libs_dir))
        else:
            pip_queue.append(dist or module)

    if pip_queue:
        results.append(_pip_install(sorted(set(pip_queue)), libs_dir))

    # 复制完成后再看一次 libs 体积
    total = 0
    names: list[str] = []
    if libs_dir.is_dir():
        for item in libs_dir.iterdir():
            names.append(item.name)
            if item.is_dir():
                total += _dir_size(item)
            else:
                total += item.stat().st_size

    return {
        "ok": True,
        "path": str(path),
        "results": results,
        "libs": sorted(names),
        "libs_size": total,
        "libs_human": _human(total),
        "vendored": [r for r in results if r.get("action") in ("copy", "pip")],
        "failed": [r for r in results if r.get("action") == "fail"],
    }


def rows_state_wants_vendor(row: dict) -> bool:
    """哪些状态的依赖需要内置。"""
    return row.get("state") in ("copy", "pip", "unknown") or row.get("state") == "ready"


# --------------------------------------------------------------------------- #
# 声明校验
# --------------------------------------------------------------------------- #
def validate_manifest(manifest: dict, plugin_dir: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["清单必须是对象"], "warnings": []}

    # 必填三段
    for key in ("author", "group", "name"):
        value = (manifest.get(key) or "").strip() if isinstance(manifest.get(key), str) else ""
        if not value:
            errors.append(f"缺少必填字段：{key}")
        elif not ID_RE.match(value):
            errors.append(f"{key} 只能是「小写字母开头 + 小写字母/数字/下划线」：{value}")

    version = (manifest.get("version") or "").strip()
    if not version:
        errors.append("缺少必填字段：version")
    elif not VERSION_RE.match(version):
        errors.append(f"version 必须是 x.y.z 形式的数字段：{version}")

    # 可选字段
    label = (manifest.get("label") or "").strip() if isinstance(manifest.get("label"), str) else ""
    if not label:
        warnings.append("没有填 label，安装后侧栏会显示 name")

    prefix = (manifest.get("url_prefix") or "").strip()
    if prefix and not prefix.startswith("/"):
        errors.append("url_prefix 必须以 / 开头")

    parent = (manifest.get("parent") or "").strip()
    if parent and not FULL_ID_RE.match(parent):
        errors.append(f"parent 必须是 author.group.name 形式：{parent}")

    parent_min = (manifest.get("parent_min_version") or "").strip()
    if parent_min and not VERSION_RE.match(parent_min):
        errors.append(f"parent_min_version 格式不对：{parent_min}")
    if parent_min and not parent:
        warnings.append("填了 parent_min_version 但没有 parent，内核会忽略它")

    deps = manifest.get("dependencies") or {}
    if not isinstance(deps, dict):
        errors.append("dependencies 必须是对象 {\"插件id\": \"最低版本\"}")
        deps = {}
    for dep_id, dep_ver in deps.items():
        if not FULL_ID_RE.match(str(dep_id)):
            errors.append(f"依赖插件 id 格式错误：{dep_id}")
        if str(dep_ver) and not VERSION_RE.match(str(dep_ver)):
            errors.append(f"依赖 {dep_id} 的版本格式错误：{dep_ver}")

    perms = manifest.get("permissions") or {}
    if not isinstance(perms, dict):
        errors.append("permissions 必须是对象 {\"短码\": \"说明\"}")
        perms = {}
    for code, desc in perms.items():
        if not ID_RE.match(str(code)):
            errors.append(f"权限短码格式错误：{code}")
        if not str(desc or "").strip():
            warnings.append(f"权限 {code} 没有写说明")

    uses = manifest.get("uses_permissions") or {}
    if not isinstance(uses, dict):
        errors.append("uses_permissions 必须是对象 {\"完整权限码\": \"用途\"}")
        uses = {}
    for code, reason in uses.items():
        if "." not in str(code):
            errors.append(f"uses_permissions 必须写完整权限码（含插件 id）：{code}")
        if not str(reason or "").strip():
            warnings.append(f"权限使用声明 {code} 没有写用途")

    provides = manifest.get("provides") or {}
    if not isinstance(provides, dict):
        errors.append("provides 必须是对象")
        provides = {}
    for key, spec in provides.items():
        if "." not in str(key):
            warnings.append(f"接口 key 建议写成「模块.类型」，例如 notice.api：{key}")
        if not isinstance(spec, dict):
            errors.append(f"接口 {key} 的声明必须是对象")
            continue
        methods = spec.get("methods") or []
        if not isinstance(methods, list):
            errors.append(f"接口 {key} 的 methods 必须是数组")
            continue
        for method in methods:
            if not isinstance(method, dict) or not str(method.get("name") or "").strip():
                errors.append(f"接口 {key} 有方法缺少 name")
                continue
            perm = str(method.get("requires_perm") or "").strip()
            if perm and "." not in perm and not ID_RE.match(perm):
                errors.append(f"接口 {key}.{method['name']} 的 requires_perm 格式错误：{perm}")
            if perm and "." in perm and perm not in perms and \
                    not str(perm).startswith(full_id(manifest) + "."):
                warnings.append(
                    f"接口 {key}.{method['name']} 引用了外部权限 {perm}，"
                    f"需要插件自己也声明 uses_permissions"
                )

    # 目录相关检查
    if plugin_dir:
        path = resolve_plugin_dir(plugin_dir)
        if not (path / "hooks.py").is_file():
            errors.append("插件目录缺少 hooks.py（内核加载必需）")
        if not (path / "plugin.json").is_file():
            warnings.append("目录里还没有 plugin.json，打包时会自动生成")
        libs = path / "libs"
        if libs.is_dir():
            for item in libs.iterdir():
                name = (item.stem if item.is_file() else item.name).lower().replace("-", "_")
                if name in KERNEL_MODULES:
                    errors.append(f"libs/ 里不允许放内核已有的库：{item.name}")
        if (path / "data").is_dir() and any((path / "data").iterdir()):
            warnings.append("data/ 里有文件，默认不会打进插件包（运行时数据不入包）")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "full_id": full_id(manifest),
    }


# --------------------------------------------------------------------------- #
# 写 plugin.json
# --------------------------------------------------------------------------- #
MANIFEST_ORDER = [
    "author", "group", "name", "label", "version", "description",
    "parent", "parent_min_version", "dependencies", "permissions",
    "uses_permissions", "provides", "url_prefix",
]


def build_manifest(form: dict) -> dict:
    """把表单整理成规范的 plugin.json（顺序固定，空值不写）。"""
    manifest: dict = {}
    for key in MANIFEST_ORDER:
        value = form.get(key)
        if key in ("dependencies", "permissions", "uses_permissions", "provides"):
            value = value or {}
            if value:
                manifest[key] = value
            continue
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value in ("", None):
            continue
        manifest[key] = value

    # 兼容旧字段：url_prefix 归一化
    if manifest.get("url_prefix"):
        manifest["url_prefix"] = norm_path(manifest["url_prefix"])

    # 保留表单里没有但已存在的其它键
    for key, value in (form.get("_extras") or {}).items():
        if key not in manifest and key not in MANIFEST_ORDER:
            manifest[key] = value
    return manifest


def write_manifest(plugin_dir: Path, manifest: dict) -> Path:
    path = plugin_dir / "plugin.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 打包
# --------------------------------------------------------------------------- #
def iter_pack_files(plugin_dir: Path, include_data: bool = False):
    """产出要打进 zip 的 (绝对路径, 相对路径)。"""
    for item in sorted(plugin_dir.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(plugin_dir)
        parts = set(rel.parts[:-1])
        if parts & EXCLUDE_DIRS:
            continue
        if not include_data and rel.parts and rel.parts[0] == "data":
            continue
        if item.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if item.name in EXCLUDE_FILES:
            continue
        yield item, rel


def pack_plugin(raw_dir: str, manifest: dict, *, out_dir: str = "",
                include_data: bool = False, write_manifest_file: bool = True,
                auto_vendor: bool = True, include_metadata: bool = False) -> dict:
    """校验 → （可选）内置依赖 → 写 plugin.json → 打 zip。"""
    path = resolve_plugin_dir(raw_dir)
    if not path.is_dir():
        raise ValueError(f"目录不存在：{path}")

    validation = validate_manifest(manifest, str(path))
    if not validation["ok"]:
        return {"ok": False, "errors": validation["errors"],
                "warnings": validation["warnings"], "stage": "validate"}

    manifest = build_manifest(manifest)
    plugin_id = full_id(manifest)

    vendored = None
    if auto_vendor:
        analysis = analyze_dependencies(str(path))
        pending = [r for r in analysis["modules"]
                   if r["state"] in ("copy", "pip", "unknown")
                   or (r["state"] == "ready")]
        if pending:
            vendored = vendor_dependencies(
                str(path), include_metadata=include_metadata
            )
            if vendored.get("failed"):
                return {
                    "ok": False,
                    "stage": "vendor",
                    "errors": [f"依赖内置失败：{r}" for r in vendored["failed"]],
                    "warnings": validation["warnings"],
                    "vendor": vendored,
                }

    if write_manifest_file:
        write_manifest(path, manifest)
    elif not (path / "plugin.json").is_file():
        # 目录里没有清单又选择不写，会打出"装不上"的包，这里兜底生成
        write_manifest(path, manifest)
        validation["warnings"].append("目录里没有 plugin.json，已自动生成")

    output = Path(out_dir).expanduser() if out_dir else path.parent / "dist"
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / f"{plugin_id}.zip"
    if zip_path.exists():
        zip_path.unlink()

    entries: list[dict] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for absolute, relative in iter_pack_files(path, include_data=include_data):
            arcname = "/".join(relative.parts)
            zf.write(absolute, arcname)
            entries.append({
                "name": arcname,
                "size": absolute.stat().st_size,
            })

    size = zip_path.stat().st_size
    return {
        "ok": True,
        "zip": str(zip_path),
        "out_dir": str(output),
        "plugin_id": plugin_id,
        "count": len(entries),
        "size": size,
        "size_human": _human(size),
        "files": entries,
        "warnings": validation["warnings"],
        "vendor": vendored,
        "manifest": manifest,
        "duration_ms": 0,
    }


# --------------------------------------------------------------------------- #
# 目录对话框（tkinter，本地工具用）
# --------------------------------------------------------------------------- #
def pick_directory(initial: str = "") -> str:
    """弹出系统目录选择框，返回绝对路径；取消返回空串。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            title="选择插件目录",
            initialdir=initial or str(Path.cwd()),
            mustexist=True,
        )
    finally:
        root.destroy()
    if not selected:
        return ""
    return str(Path(selected).resolve())


def open_path(target: str) -> None:
    """用系统默认方式打开文件夹。"""
    path = Path(target)
    if not path.exists():
        raise ValueError(f"路径不存在：{path}")
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
