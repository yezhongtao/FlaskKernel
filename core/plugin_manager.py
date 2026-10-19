"""插件管理器：校验、拓扑排序、加载、路由分配、接口注册。"""
from __future__ import annotations

import functools
import importlib.util
import re
import sys
import time
from pathlib import Path

from jinja2 import ChoiceLoader, FileSystemLoader

from core import access
from core.config import PLUGINS_DIR, get_config
from core.db import db
from core.log import get_logger
from core.models import Menu, Plugin, PluginUsesPermission, Route
from core.plugin_context import PluginContext

logger = get_logger(__name__)

FORBIDDEN_LIBS = {"flask", "flask_login", "flask_sqlalchemy", "werkzeug",
                  "sqlalchemy", "jinja2"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# --------------------------------------------------------------------------- #
# 版本比较
# --------------------------------------------------------------------------- #
def _parse_version(v) -> tuple:
    parts = []
    for seg in str(v or "0").split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while parts and parts[-1] == 0 and len(parts) > 1:
        parts.pop()
    return tuple(parts) if parts else (0,)


def version_ok(actual, required) -> bool:
    return _parse_version(actual) >= _parse_version(required)


# --------------------------------------------------------------------------- #
# 插件调用代理
# --------------------------------------------------------------------------- #
class _ProviderProxy:
    """包装 provider 对象，调用方法时校验 uses_permissions 并记访问日志。"""

    def __init__(self, obj, caller_id: str, provider_id: str,
                 service_key: str, spec: dict):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_caller", caller_id)
        object.__setattr__(self, "_provider", provider_id)
        object.__setattr__(self, "_service", service_key)
        object.__setattr__(self, "_methods", {
            m.get("name"): m for m in (spec.get("methods") or []) if m.get("name")
        })

    def __getattr__(self, name):
        obj = object.__getattribute__(self, "_obj")
        attr = getattr(obj, name)

        methods = object.__getattribute__(self, "_methods")
        spec = methods.get(name)
        if spec is None or not callable(attr):
            return attr

        perm = spec.get("requires_perm")
        if not perm:
            return attr

        provider = object.__getattribute__(self, "_provider")
        caller = object.__getattribute__(self, "_caller")
        service = object.__getattribute__(self, "_service")
        full_perm = perm if "." in perm else f"{provider}.{perm}"

        @functools.wraps(attr)
        def wrapper(*args, **kwargs):
            exists = db.session.query(PluginUsesPermission.id).filter_by(
                plugin_id=caller, permission_code=full_perm
            ).first()
            if not exists:
                raise PermissionError(
                    f"插件 {caller} 未声明使用权限 {full_perm}"
                )
            t0 = time.perf_counter()
            ok = True
            try:
                return attr(*args, **kwargs)
            except Exception:
                ok = False
                raise
            finally:
                access.record_plugin_call(
                    caller, provider, service, name,
                    (time.perf_counter() - t0) * 1000, ok,
                )

        return wrapper

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_obj"), name, value)


# --------------------------------------------------------------------------- #
# PluginManager
# --------------------------------------------------------------------------- #
class PluginManager:
    def __init__(self, app):
        self.app = app
        self._core_loader = app.jinja_env.loader
        self._plugin_loaders: list = []

        self.contexts: dict[str, PluginContext] = {}
        self.providers: dict[str, tuple[str, object, dict]] = {}
        self.events: dict[str, list[tuple[str, object]]] = {}
        self.loaded_order: list[str] = []

    # ===================== 对外入口 =====================
    def load_all(self) -> None:
        if not self.app.config.get("PLUGIN_AUTO_LOAD", True):
            logger.info("插件自动加载已关闭（plugin.auto_load = false）")
            return

        plugins = Plugin.query.filter_by(enabled=True).order_by(Plugin.id).all()
        if not plugins:
            logger.info("没有启用中的插件")
            self._purge_stale_menus(set())
            return

        pmap = {p.id: p for p in plugins}
        status: dict[str, str] = {}
        errors: dict[str, str] = {}

        for p in plugins:
            try:
                self._validate_declared(p, pmap)
                status[p.id] = "pending"
            except Exception as exc:
                status[p.id] = "error"
                errors[p.id] = str(exc)

        for pid in self._detect_cycles(pmap):
            status[pid] = "error"
            errors.setdefault(pid, "检测到循环依赖")

        try:
            order = self._topo_sort(pmap)
        except Exception as exc:
            logger.exception("拓扑排序失败")
            for p in plugins:
                self._persist(p, "error", f"拓扑排序失败: {exc}")
            self._purge_stale_menus(set())
            return

        logger.info("插件加载顺序: %s", " -> ".join(order) if order else "(空)")

        for pid in order:
            p = pmap[pid]

            if status.get(pid) == "error":
                self._persist(p, "error", errors.get(pid))
                logger.error("插件 %s 声明校验失败: %s", pid, errors.get(pid))
                continue

            upstream_err = self._check_upstream(p, status)
            if upstream_err:
                status[pid] = "blocked"
                self._persist(p, "blocked", upstream_err)
                logger.warning("插件 %s 被阻塞: %s", pid, upstream_err)
                continue

            try:
                self._load_one(p)
                status[pid] = "loaded"
                self._persist(p, "loaded", None)
                logger.info("加载插件 %s", pid)
            except Exception as exc:
                logger.exception("插件 %s 加载失败", pid)
                status[pid] = "error"
                self._persist(p, "error", str(exc))
                self._rollback(pid)

        # 加载结束后清掉"没加载成功"的插件留下的菜单：
        # 插件被停用/卸载/加载失败时，侧栏不应再出现它的入口
        self._purge_stale_menus(set(self.loaded_order))

    def _purge_stale_menus(self, keep_ids: set[str]) -> None:
        removed = 0
        for row in Menu.query.all():
            if row.plugin_id and row.plugin_id not in keep_ids:
                db.session.delete(row)
                removed += 1
        if removed:
            db.session.commit()
            logger.info("已清理 %d 个失效的插件菜单", removed)

    # ===================== 校验 =====================
    def _validate_declared(self, p: Plugin, pmap: dict) -> None:
        for dep_id, min_ver in (p.dependencies or {}).items():
            dep = db.session.get(Plugin, dep_id)
            if dep is None:
                raise RuntimeError(f"缺少依赖插件: {dep_id}")
            if min_ver and not version_ok(dep.version, min_ver):
                raise RuntimeError(
                    f"依赖 {dep_id} 版本不满足（需要 >= {min_ver}，实际 {dep.version}）"
                )

        if p.parent:
            if p.parent not in pmap:
                parent_row = db.session.get(Plugin, p.parent)
                if parent_row is None:
                    raise RuntimeError(f"父插件不存在: {p.parent}")
                raise RuntimeError(f"父插件 {p.parent} 未启用")
            parent = pmap[p.parent]
            if p.parent_min_version and not version_ok(parent.version, p.parent_min_version):
                raise RuntimeError(
                    f"父插件 {p.parent} 版本不满足"
                    f"（需要 >= {p.parent_min_version}，实际 {parent.version}）"
                )

    def _check_upstream(self, p: Plugin, status: dict) -> str | None:
        for dep in (p.dependencies or {}):
            st = status.get(dep)
            if st in ("error", "blocked"):
                return f"依赖插件 {dep} 状态异常（{st}）"
        if p.parent:
            st = status.get(p.parent)
            if st in ("error", "blocked"):
                return f"父插件 {p.parent} 状态异常（{st}）"
            if st != "loaded":
                return f"父插件 {p.parent} 未加载"
        return None

    @staticmethod
    def _detect_cycles(pmap: dict) -> set:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {pid: WHITE for pid in pmap}
        bad: set[str] = set()

        def dfs(pid: str, path: list):
            color[pid] = GRAY
            path.append(pid)
            p = pmap[pid]
            nxt = list((p.dependencies or {}).keys())
            if p.parent:
                nxt.append(p.parent)
            for d in nxt:
                if d not in pmap:
                    continue
                if color[d] == GRAY:
                    idx = path.index(d)
                    bad.update(path[idx:])
                elif color[d] == WHITE:
                    dfs(d, path)
            path.pop()
            color[pid] = BLACK

        for pid in list(pmap):
            if color[pid] == WHITE:
                dfs(pid, [])
        return bad

    @staticmethod
    def _topo_sort(pmap: dict) -> list:
        result: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(pid: str):
            if pid in visited or pid not in pmap:
                return
            if pid in visiting:
                raise RuntimeError(f"循环依赖: {pid}")
            visiting.add(pid)
            p = pmap[pid]
            for d in (p.dependencies or {}):
                visit(d)
            if p.parent:
                visit(p.parent)
            visiting.discard(pid)
            visited.add(pid)
            result.append(pid)

        for pid in sorted(pmap):
            visit(pid)
        return result

    # ===================== 加载单个插件 =====================
    def _load_one(self, p: Plugin) -> None:
        plugin_dir = Path(PLUGINS_DIR) / p.id
        if not plugin_dir.is_dir():
            raise RuntimeError(f"插件目录不存在: {plugin_dir}")

        hooks_file = plugin_dir / "hooks.py"
        if not hooks_file.is_file():
            raise RuntimeError("插件缺少 hooks.py")

        libs_dir = plugin_dir / "libs"
        if libs_dir.is_dir():
            self._check_libs(libs_dir)

        db.session.query(Route).filter_by(owner_id=p.id).delete()
        db.session.flush()
        prefix = self._allocate_prefix(p)

        module = self._import_hooks(p, hooks_file, libs_dir)

        register = getattr(module, "register", None)
        if not callable(register):
            raise RuntimeError("hooks.py 必须提供 register(ctx) 函数")

        data_dir = plugin_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        ctx = PluginContext(
            plugin_id=p.id,
            name=p.name,
            label=p.label or p.name,
            version=p.version,
            base_dir=plugin_dir,
            data_dir=data_dir,
            app=self.app,
            manager=self,
            url_prefix=prefix,
            provides=p.provides or {},
            dependencies=p.dependencies or {},
        )
        self.contexts[p.id] = ctx

        # 重新加载时先清掉这个插件的旧菜单：插件不注册菜单了，侧栏就不该再留着
        Menu.query.filter_by(plugin_id=p.id).delete()
        db.session.commit()

        try:
            register(ctx)
        except Exception:
            self.contexts.pop(p.id, None)
            raise

        self._install_template_loader(plugin_dir)
        self.loaded_order.append(p.id)

        db.session.add(Route(
            path=prefix,
            owner_id=p.id,
            owner_kind="plugin",
            parent_id=p.parent,
            is_override=False,
            logical_path=p.url_prefix or prefix,
        ))
        p.actual_url_prefix = prefix
        db.session.commit()

    def _import_hooks(self, p: Plugin, hooks_file: Path, libs_dir: Path):
        mod_name = "oaplugin_" + p.id.replace(".", "_")
        sys.modules.pop(mod_name, None)

        spec = importlib.util.spec_from_file_location(mod_name, str(hooks_file))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法创建模块规格: {hooks_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module

        inserted = False
        if libs_dir.is_dir():
            sys.path.insert(0, str(libs_dir))
            inserted = True
        try:
            spec.loader.exec_module(module)
        finally:
            if inserted:
                try:
                    sys.path.remove(str(libs_dir))
                except ValueError:
                    pass
        return module

    @staticmethod
    def _check_libs(libs_dir: Path) -> None:
        for entry in libs_dir.iterdir():
            name = entry.name
            if entry.is_dir():
                pkg = name
            elif name.endswith(".py"):
                pkg = name[:-3]
            else:
                continue
            normalized = pkg.lower().replace("-", "_")
            if normalized in FORBIDDEN_LIBS:
                raise RuntimeError(f"插件 libs/ 不允许包含内核已有的库: {pkg}")

    # ===================== URL 分配 =====================
    def _kernel_urls(self):
        return self.app.extensions.get("kernel_urls")

    def _allocate_prefix(self, p: Plugin) -> str:
        kernel = self._kernel_urls()
        reserved = kernel.reserved() if kernel else set()
        fallback = kernel.plugin_fallback_prefix if kernel else "/plugin"

        desired = (p.url_prefix or "").strip() or f"/{p.name}"
        # 「URL 映射」页改过的路径存在 config.json 的 plugin.path_overrides，
        # 重启后优先用它，否则用户改的映射会被插件声明的 url_prefix 覆盖回去
        try:
            raw_overrides = (get_config().get("plugin") or {}).get("path_overrides") or {}
            overrides = {key: value for key, value in raw_overrides.items()
                         if isinstance(value, str)} if isinstance(raw_overrides, dict) else {}
        except Exception:  # noqa: BLE001
            overrides = {}
        override = str(overrides.get(p.id) or "").strip()
        if override:
            desired = override
            logger.info("插件 %s 使用自定义路径 %s", p.id, override)
        if not desired.startswith("/"):
            desired = "/" + desired
        desired = desired.rstrip("/") or "/"

        taken: dict[str, str] = {}
        for r in db.session.query(Route).all():
            if r.owner_id != p.id:
                taken[r.path] = r.owner_id

        # 与内核冲突 → 兜底
        if desired in reserved:
            return f"{fallback}/{p.id}"

        if desired not in taken:
            return desired

        owner = taken[desired]
        # 与父插件冲突 → 允许覆盖
        if p.parent and owner == p.parent:
            return desired

        # 与别的插件冲突 → 兜底
        return f"{fallback}/{p.id}"

    # ===================== 菜单 =====================
    def _register_menus(self, plugin_id: str, menus: list) -> None:
        for item in menus:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("name") or "menu"
            name = raw_name if raw_name.startswith(plugin_id + ".") else f"{plugin_id}.{raw_name}"

            row = Menu.query.filter_by(name=name).first()
            if row is None:
                row = Menu(name=name)
                db.session.add(row)

            row.plugin_id = plugin_id
            row.label = item.get("label", raw_name)
            row.url = item.get("url", "")
            row.parent_id = item.get("parent_id")
            row.perm = item.get("perm")
            row.order = int(item.get("order", 0) or 0)
            row.icon = item.get("icon")

        db.session.commit()

    # ===================== 接口注册 / 获取 =====================
    def _register_provider(self, plugin_id: str, key: str, obj, spec: dict) -> None:
        if key in self.providers:
            owner = self.providers[key][0]
            raise RuntimeError(f"接口 key 冲突: {key}（已被 {owner} 占用）")
        self.providers[key] = (plugin_id, obj, spec or {})

    def _require(self, caller_id: str, key: str, deps: dict):
        entry = self.providers.get(key)
        if entry is None:
            raise LookupError(f"接口不存在: {key}")

        provider_id, obj, spec = entry
        if provider_id != caller_id and provider_id not in (deps or {}):
            raise PermissionError(
                f"插件 {caller_id} 未在 dependencies 中声明对 {provider_id} 的依赖"
            )
        if provider_id == caller_id:
            return obj
        return _ProviderProxy(obj, caller_id, provider_id, key, spec)

    def _register_event(self, plugin_id: str, event: str, handler) -> None:
        self.events.setdefault(event, []).append((plugin_id, handler))

    def emit(self, event: str, *args, **kwargs) -> None:
        for plugin_id, handler in self.events.get(event, []):
            try:
                handler(*args, **kwargs)
            except Exception:
                logger.exception("事件 %s 处理失败（插件 %s）", event, plugin_id)

    def _on_blueprint_registered(self, plugin_id: str, bp) -> None:
        logger.debug("插件 %s 注册蓝图 %s (url_prefix=%s)",
                     plugin_id, bp.name, bp.url_prefix)

    # ===================== 模板加载器 =====================
    def _install_template_loader(self, plugin_dir: Path) -> None:
        tpl_dir = plugin_dir / "templates"
        if not tpl_dir.is_dir():
            return
        loader = FileSystemLoader(str(tpl_dir))
        self._plugin_loaders.insert(0, loader)
        self.app.jinja_env.loader = ChoiceLoader(
            self._plugin_loaders + [self._core_loader]
        )

    # ===================== 状态 =====================
    @staticmethod
    def _persist(p: Plugin, status: str, error: str | None) -> None:
        p.status = status
        p.last_error = error
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _rollback(self, plugin_id: str) -> None:
        self.contexts.pop(plugin_id, None)
        for key in [k for k, v in self.providers.items() if v[0] == plugin_id]:
            self.providers.pop(key, None)
        for evt in list(self.events):
            self.events[evt] = [h for h in self.events[evt] if h[0] != plugin_id]
        if plugin_id in self.loaded_order:
            self.loaded_order.remove(plugin_id)

    # ===================== 查询辅助 =====================
    def get_context(self, plugin_id: str) -> PluginContext | None:
        return self.contexts.get(plugin_id)

    def list_providers(self) -> dict:
        return {k: v[0] for k, v in self.providers.items()}