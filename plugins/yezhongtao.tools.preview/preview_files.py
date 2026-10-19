"""文件系统访问 + 各类文件解析（纯标准库）。

安全约束：
  · 只能访问配置里 roots 允许的目录（默认项目根），`..` 一律拒绝；
  · 只读：不写、不改、不删任何文件；
  · 文本 / 表格都有上限，避免超大文件卡死页面。
"""
from __future__ import annotations

import csv
import html
import io
import json
import mimetypes
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import preview_config as cfg
import preview_highlight as highlighter

# ---------------------------------------------------------------- 类型表
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg", ".avif"}
VIDEO_EXT = {".mp4", ".webm", ".ogg", ".ogv", ".mov", ".mkv", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".opus", ".wma"}
PDF_EXT = {".pdf"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
WORD_EXT = {".docx", ".docm"}
LEGACY_OFFICE = {".doc", ".xls", ".ppt", ".pps", ".wps", ".et", ".dps", ".rtf"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".whl"}
MEDIA_TYPE = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "video/ogg",
    ".ogv": "video/ogg", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".opus": "audio/ogg",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".avif": "image/avif",
    ".pdf": "application/pdf",
}


def suffix_of(path) -> str:
    return Path(str(path)).suffix.lower()


def kind_of(path) -> str:
    suffix = suffix_of(path)
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in PDF_EXT:
        return "pdf"
    if suffix in TABLE_EXT:
        return "table"
    if suffix in WORD_EXT:
        return "word"
    if suffix in LEGACY_OFFICE:
        return "legacy-office"
    if suffix in ARCHIVE_EXT:
        return "archive"
    return "text"


class PreviewError(Exception):
    pass


class Vault:
    """把 root 限制、目录遍历、预览解析包在一起。"""

    def __init__(self, base_dir: str | Path, settings: dict):
        self.base_dir = Path(base_dir).resolve()
        self.settings = settings

    # ------------------------------------------------------------ 路径安全
    def roots(self) -> list[Path]:
        raw = cfg.normalize_roots(self.settings.get("roots"))
        roots = []
        for item in raw:
            candidate = (self.base_dir / item).resolve()
            try:
                candidate.relative_to(self.base_dir)
            except ValueError:
                continue  # 只允许项目内的路径
            if candidate.is_dir():
                roots.append(candidate)
        return roots or [self.base_dir]

    def resolve(self, rel: str) -> Path:
        text = str(rel or "").replace("\\", "/").strip().strip("/")
        if any(part == ".." for part in text.split("/")):
            raise PreviewError("路径不合法")
        target = (self.base_dir / text).resolve() if text else self.base_dir
        if target == self.base_dir:
            return target  # 根目录本身永远可进（里面再按 roots 限制）
        allowed = False
        for root in self.roots():
            try:
                target.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            raise PreviewError("这个目录不在允许预览的范围内")
        return target

    def rel_of(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.base_dir).as_posix()
        except ValueError:
            return path.as_posix()

    # ------------------------------------------------------------ 目录
    def list_dir(self, rel: str = "") -> dict:
        target = self.resolve(rel)
        if not target.is_dir():
            raise PreviewError("不是目录")
        # 根目录 + 限制了浏览范围时：只把允许的目录列出来
        restricted = [root for root in self.roots() if root != self.base_dir]
        if target == self.base_dir and restricted:
            entries = []
            for root in restricted:
                stat = root.stat()
                entries.append({
                    "name": root.name,
                    "rel": self.rel_of(root),
                    "dir": True,
                    "size": 0,
                    "mtime": _fmt_time(stat.st_mtime),
                    "kind": "dir",
                })
            return {"path": "", "parent": "", "entries": entries}

        show_hidden = bool(self.settings.get("show_hidden"))
        limit = 800
        entries = []
        try:
            children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise PreviewError("读不了这个目录：%s" % exc) from exc
        for child in children:
            if len(entries) >= limit:
                break
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            is_dir = child.is_dir()
            entries.append({
                "name": child.name,
                "rel": self.rel_of(child),
                "dir": is_dir,
                "size": 0 if is_dir else stat.st_size,
                "mtime": _fmt_time(stat.st_mtime),
                "kind": "dir" if is_dir else kind_of(child),
            })
        parent = ""
        if target != self.base_dir:
            try:
                parent = target.parent.relative_to(self.base_dir).as_posix()
                if parent == ".":
                    parent = ""
            except ValueError:
                parent = ""
        return {"path": self.rel_of(target) if target != self.base_dir else "",
                "parent": parent, "entries": entries}

    # ------------------------------------------------------------ 预览
    def preview(self, rel: str) -> dict:
        target = self.resolve(rel)
        if not target.exists():
            raise PreviewError("文件不存在")
        if target.is_dir():
            raise PreviewError("这是目录")
        kind = kind_of(target)
        size = target.stat().st_size
        payload = {
            "ok": True,
            "rel": self.rel_of(target),
            "name": target.name,
            "size": size,
            "mtime": _fmt_time(target.stat().st_mtime),
            "kind": kind,
            "mode": kind,
        }
        if kind in ("image", "video", "audio", "pdf"):
            return payload
        if kind == "archive":
            payload["mode"] = "none"
            payload["message"] = "压缩包不支持在线预览，可以下载后解开查看。"
            return payload
        if kind == "legacy-office":
            payload["mode"] = "none"
            payload["message"] = ("这是旧版二进制 Office 格式（.doc/.xls/.ppt/.rtf），"
                                  "浏览器无法直接预览；请下载后查看。")
            return payload
        if kind == "table":
            payload.update(self._table(target))
            return payload
        if kind == "word":
            payload.update(self._word(target))
            return payload
        payload.update(self._text(target))
        return payload

    # ------------------------------------------------------------ 文本 / 代码
    def _text(self, target: Path) -> dict:
        limit = int(self.settings.get("max_text_mb") or 2) * 1024 * 1024
        size = target.stat().st_size
        if size > limit:
            return {"mode": "none",
                    "message": "文件 %.1f MB，超过预览上限 %.0f MB，请下载后查看。"
                               % (size / 1048576.0, limit / 1048576.0)}
        raw = target.read_bytes()[:limit]
        if b"\x00" in raw[:4096]:
            return {"mode": "none", "message": "看起来是二进制文件，无法按文本预览。"}
        text = _decode(raw)
        lang = highlighter.lang_of(str(target))
        lines = text.split("\n")
        if len(lines) > 4000:
            lines = lines[:4000]
            lines.append("…（只显示前 4000 行）")
        html_lines = highlighter.highlight_lines("\n".join(lines), lang)
        return {"mode": "code", "lang": lang, "lines": html_lines,
                "total_lines": text.count("\n") + 1,
                "truncated": len(html_lines) < text.count("\n") + 1}

    # ------------------------------------------------------------ 表格
    def _table(self, target: Path) -> dict:
        suffix = suffix_of(target)
        if suffix in (".csv", ".tsv"):
            return self._csv(target)
        if suffix in (".xlsx", ".xlsm"):
            return self._xlsx(target)
        return {"mode": "none", "message": "暂不支持这个表格格式"}

    def _csv(self, target: Path) -> dict:
        raw = target.read_bytes()
        text = _decode(raw)
        sample = text[:4096]
        delimiter = ","
        for candidate in (",", ";", "\t", "|"):
            if sample.count(candidate) > sample.count(delimiter):
                delimiter = candidate
        max_rows = int(self.settings.get("max_rows") or 500)
        max_cols = int(self.settings.get("max_cols") or 40)
        rows = []
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        truncated = False
        for index, row in enumerate(reader):
            if index >= max_rows:
                truncated = True
                break
            rows.append([cell[:400] for cell in row[:max_cols]])
        if not rows:
            return {"mode": "none", "message": "空表格"}
        return {"mode": "table", "table": {"head": rows[0], "rows": rows[1:]},
                "truncated": truncated, "total_rows": len(rows)}

    def _xlsx(self, target: Path) -> dict:
        max_rows = int(self.settings.get("max_rows") or 500)
        max_cols = int(self.settings.get("max_cols") or 40)
        try:
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                sheet_files = sorted(name for name in names
                                     if name.startswith("xl/worksheets/sheet")
                                     and name.endswith(".xml"))
                if not sheet_files:
                    return {"mode": "none", "message": "这个 xlsx 里没有找到工作表"}
                shared = _xlsx_shared_strings(archive, names)
                sheet_name = _xlsx_sheet_name(archive, names)
                root = ET.fromstring(archive.read(sheet_files[0]))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            return {"mode": "none", "message": "xlsx 解析失败：%s" % exc}

        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        rows = []
        truncated = False
        for row in root.iter(ns + "row"):
            if len(rows) >= max_rows:
                truncated = True
                break
            cells: dict[int, str] = {}
            for cell in row.findall(ns + "c"):
                ref = cell.get("r") or ""
                column = _column_index(ref)
                if column >= max_cols:
                    continue
                cell_type = cell.get("t")
                value_el = cell.find(ns + "v")
                value = value_el.text if value_el is not None and value_el.text else ""
                if cell_type == "s":
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError):
                        value = ""
                elif cell_type == "inlineStr":
                    inline = cell.find(ns + "is")
                    value = "".join(node.text or "" for node in inline.iter(ns + "t")) \
                        if inline is not None else ""
                elif value and "." in value:
                    try:
                        number = float(value)
                        if abs(number - round(number)) < 1e-9:
                            value = str(int(round(number)))
                        else:
                            value = ("%.4f" % number).rstrip("0").rstrip(".")
                    except ValueError:
                        pass
                cells[column] = (value or "")[:300]
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(index, "") for index in range(min(width, max_cols))])
        if not rows:
            return {"mode": "none", "message": "工作表是空的"}
        head = rows[0]
        body = rows[1:]
        return {"mode": "table", "sheet": sheet_name, "truncated": truncated,
                "table": {"head": head, "rows": body}, "total_rows": len(rows)}

    # ------------------------------------------------------------ Word
    def _word(self, target: Path) -> dict:
        try:
            with zipfile.ZipFile(target) as archive:
                content = archive.read("word/document.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            return {"mode": "none", "message": "docx 解析失败：%s" % exc}
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            return {"mode": "none", "message": "docx 解析失败：%s" % exc}

        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        body = root.find(w + "body")
        blocks: list[str] = []
        if body is None:
            return {"mode": "none", "message": "文档是空的"}
        for child in list(body):
            if child.tag == w + "p":
                blocks.append(_docx_paragraph(child, w))
            elif child.tag == w + "tbl":
                blocks.append(_docx_table(child, w))
        html_body = "".join(blocks) or "<p class='muted'>（空文档）</p>"
        return {"mode": "word", "html": html_body}


# ---------------------------------------------------------------- 工具
def _fmt_time(timestamp: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _xlsx_shared_strings(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except ET.ParseError:
        return []
    values = []
    for item in root.findall(ns + "si"):
        values.append("".join(node.text or "" for node in item.iter(ns + "t")))
    return values


def _xlsx_sheet_name(archive: zipfile.ZipFile, names: list[str]) -> str:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return "Sheet1"
    for sheet in root.iter(ns + "sheet"):
        return sheet.get("name") or "Sheet1"
    return "Sheet1"


def _docx_paragraph(paragraph, w: str) -> str:
    style = ""
    for node in paragraph.iter(w + "pStyle"):
        style = (node.get(w + "val") or "").lower()
        break
    text_parts = []
    for node in paragraph.iter():
        if node.tag == w + "t" and node.text:
            text_parts.append(node.text)
        elif node.tag == w + "tab":
            text_parts.append("\t")
        elif node.tag == w + "br":
            text_parts.append("\n")
    text = "".join(text_parts).strip()
    if not text:
        return "<p class='docx-empty'></p>"
    if style.startswith("heading"):
        level = style.replace("heading", "").strip() or "1"
        try:
            level_number = min(4, max(1, int(level)))
        except ValueError:
            level_number = 2
        return "<h%d>%s</h%d>" % (level_number + 1, html.escape(text), level_number + 1)
    if style.startswith("title"):
        return "<h1>%s</h1>" % html.escape(text)
    return "<p>%s</p>" % html.escape(text)


def _docx_table(table, w: str) -> str:
    rows = []
    for row in table.findall(w + "tr"):
        cells = []
        for cell in row.findall(w + "tc"):
            texts = []
            for node in cell.iter(w + "t"):
                if node.text:
                    texts.append(node.text)
            cells.append("<td>%s</td>" % html.escape(" ".join(texts).strip()))
        rows.append("<tr>%s</tr>" % "".join(cells))
    if not rows:
        return ""
    return "<table class='docx-table'>%s</table>" % "".join(rows)
