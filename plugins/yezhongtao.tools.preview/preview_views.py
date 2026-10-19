"""文件预览插件：浏览目录 + 在线预览。

页面：/preview/            文件树 + 目录列表 + 预览面板
      /preview/?path=a/b   打开某个目录或文件
      /preview/settings    设置（可浏览目录、预览上限）
接口：/preview/api/tree、/preview/api/preview
原文件：/preview/raw/<路径>（图片/音视频/PDF 直接给浏览器，支持 Range）
        /preview/download/<路径>（下载）
"""
from __future__ import annotations

import mimetypes

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

from core.permission import require_perm
from core.paths import rel_path

import preview_config as cfg
import preview_files as files

bp = Blueprint("preview", __name__)

_state: dict = {}
PLUGIN_ID = "yezhongtao.tools.preview"
SKIP_DIRS = {".venv", "venv", ".git", "node_modules", "__pycache__", "runtime",
             "staging", ".idea", ".pytest_cache", "dist", "build"}


def configure(**kwargs) -> None:
    _state.update(kwargs)


def _vault() -> files.Vault:
    return _state["vault"]


def _settings() -> dict:
    return _state.get("settings") or {}


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@bp.route("/")
@require_perm("view")
def page():
    raw_path = str(request.args.get("path") or "").strip()
    try:
        entry_path = _vault().resolve(raw_path)
    except files.PreviewError as exc:
        flash(str(exc), "error")
        entry_path = _vault().resolve("")

    context = {
        "page_title": "文件预览",
        "page_sub": "代码高亮 / 表格 / Word / 图片 / 视频 / 音频",
        "current": _vault().rel_of(entry_path) if entry_path != _vault().base_dir else "",
        "is_dir": entry_path.is_dir(),
        "tree": _build_tree("", depth=0),
        "roots": [rel_path(root) for root in _vault().roots()],
        "fmt_size": _fmt_size,
        "kind_of": files.kind_of,
    }
    context["crumbs"] = _breadcrumbs(context["current"])

    if entry_path.is_dir():
        try:
            listing = _vault().list_dir(raw_path)
        except files.PreviewError as exc:
            flash(str(exc), "error")
            listing = {"path": "", "parent": "", "entries": []}
        context.update(entries=listing["entries"], parent=listing["parent"],
                       dir_path=listing["path"])
    else:
        try:
            context["preview"] = _vault().preview(raw_path)
        except files.PreviewError as exc:
            flash(str(exc), "error")
            context["preview"] = {"ok": False, "message": str(exc)}
    return render_template("yezhongtao.tools.preview/preview.html", **context)


@bp.route("/api/tree")
@require_perm("view")
def api_tree():
    path = str(request.args.get("path") or "")
    try:
        return {"ok": True, **_vault().list_dir(path)}
    except files.PreviewError as exc:
        return {"ok": False, "error": str(exc)}, 400


@bp.route("/api/preview")
@require_perm("view")
def api_preview():
    path = str(request.args.get("path") or "")
    try:
        return {"ok": True, **_vault().preview(path)}
    except files.PreviewError as exc:
        return {"ok": False, "error": str(exc)}, 400


@bp.route("/raw/<path:rel>")
@require_perm("view")
def raw(rel: str):
    try:
        target = _vault().resolve(rel)
    except files.PreviewError:
        abort(404)
    if not target.is_file():
        abort(404)
    mimetype = files.MEDIA_TYPE.get(files.suffix_of(target)) \
        or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return send_file(str(target), mimetype=mimetype, conditional=True)


@bp.route("/download/<path:rel>")
@require_perm("download")
def download(rel: str):
    try:
        target = _vault().resolve(rel)
    except files.PreviewError:
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(str(target), as_attachment=True, download_name=target.name)


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
@bp.route("/settings", methods=["GET", "POST"])
@require_perm("settings")
def settings_page():
    settings = _settings()
    if request.method == "POST":
        errors, changed = cfg.apply_form(request.form, settings)
        errors.extend(cfg.validate(settings))
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            _state["save_settings"](settings)
            _state["vault"] = _state["make_vault"](settings)
            _state["settings"] = settings
            flash("设置已保存，共修改 %d 项" % len(changed), "success")
        return redirect(url_for(".settings_page"))

    return render_template(
        "yezhongtao.tools.preview/settings.html",
        page_title="文件预览设置",
        page_sub="可浏览目录与预览上限",
        schema=cfg.SCHEMA,
        settings=settings,
        roots=[rel_path(root) for root in _vault().roots()],
    )


# --------------------------------------------------------------------------- #
# 目录树
# --------------------------------------------------------------------------- #
def _build_tree(rel: str, depth: int, budget: dict | None = None) -> list[dict]:
    """只列目录，最多 3 层、每层 40 个，够用又不至于把页面撑爆。"""
    budget = budget if budget is not None else {"left": 200}
    if depth > 2 or budget["left"] <= 0:
        return []
    try:
        folder = _vault().resolve(rel)
    except files.PreviewError:
        return []
    if not folder.is_dir():
        return []
    show_hidden = bool(_settings().get("show_hidden"))
    try:
        children = sorted((item for item in folder.iterdir() if item.is_dir()),
                          key=lambda item: item.name.lower())
    except OSError:
        return []
    nodes = []
    for child in children:
        if budget["left"] <= 0 or len(nodes) >= 40:
            break
        if child.name.startswith(".") and not show_hidden:
            continue
        if child.name in SKIP_DIRS:
            continue
        if child.name.startswith("__") and child.name.endswith("__"):
            continue
        budget["left"] -= 1
        child_rel = _vault().rel_of(child)
        nodes.append({
            "name": child.name,
            "rel": child_rel,
            "children": _build_tree(child_rel, depth + 1, budget),
        })
    return nodes


def _breadcrumbs(current: str) -> list[dict]:
    parts = [part for part in current.split("/") if part]
    crumbs = [{"name": "项目根", "rel": ""}]
    acc = ""
    for part in parts:
        acc = part if not acc else acc + "/" + part
        crumbs.append({"name": part, "rel": acc})
    return crumbs


def _fmt_size(size: int) -> str:
    size = int(size or 0)
    if size < 1024:
        return "%d B" % size
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024.0)
    return "%.1f MB" % (size / 1048576.0)
