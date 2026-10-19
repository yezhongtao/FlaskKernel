"""极简语法高亮（纯 Python，无第三方依赖）。

按行处理：注释 / 字符串 / 数字 / 关键字 / 函数调用 / 装饰器。
输出 HTML 片段，调用方负责拼行号与 `<pre>`。
"""
from __future__ import annotations

import html
import re

# 语言 → 关键字
KEYWORDS = {
    "python": """and as assert async await break class continue def del elif else except
        finally for from global if import in is lambda nonlocal not or pass raise return
        try while with yield None True False self""".split(),
    "javascript": """var let const function return if else for while do break continue new
        class extends import export from default try catch finally throw typeof instanceof
        in of this super null undefined true false async await yield switch case""".split(),
    "typescript": """var let const function return if else for while do break continue new
        class extends implements interface type enum import export from default try catch
        finally throw typeof instanceof in of this super null undefined true false async
        await yield switch case public private readonly""".split(),
    "java": """public private protected class interface extends implements static final
        void int long double float boolean char String if else for while do return new
        try catch finally throw import package null true false""".split(),
    "go": """package import func var const type struct interface map chan go defer return
        if else for range switch case break continue nil true false""".split(),
    "rust": """fn let mut const struct enum impl trait use mod pub return if else match for
        while loop break continue self Self true false""".split(),
    "sql": """select from where insert into values update set delete create table drop
        alter add index join left right inner outer on group by order having limit offset
        as and or not null is in like between distinct count sum avg max min union""".split(),
    "shell": """if then else elif fi for in do done case esac while until function return
        export local echo cd exit set unset""".split(),
    "css": """color background border margin padding display flex grid position top left
        right bottom width height font transition transform animation media import""".split(),
    "json": """true false null""".split(),
    "yaml": """true false null yes no on off""".split(),
}

# 扩展名 → 语言
EXT_LANG = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".go": "go", ".rs": "rust", ".sql": "sql",
    ".sh": "shell", ".bash": "shell", ".bat": "shell", ".cmd": "shell", ".ps1": "shell",
    ".css": "css", ".scss": "css", ".less": "css",
    ".json": "json", ".jsonc": "json",
    ".yml": "yaml", ".yaml": "yaml",
    ".html": "markup", ".htm": "markup", ".xml": "markup", ".svg": "markup",
    ".vue": "markup",
    ".md": "markdown", ".markdown": "markdown",
    ".ini": "ini", ".cfg": "ini", ".toml": "ini", ".env": "ini", ".conf": "ini",
    ".txt": "text", ".log": "text", ".csv": "text", ".tsv": "text",
}

COMMENT_PREFIX = {
    "python": "#", "shell": "#", "yaml": "#", "ini": "#", "toml": "#",
    "javascript": "//", "typescript": "//", "java": "//", "go": "//", "rust": "//",
    "sql": "--",
}


def lang_of(path: str) -> str:
    suffix = "." + str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""
    return EXT_LANG.get(suffix, "text")


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _keyword_re(lang: str):
    words = KEYWORDS.get(lang)
    if not words:
        return None
    ordered = sorted(set(words), key=len, reverse=True)
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(word) for word in ordered))


def highlight_line(line: str, lang: str) -> str:
    """返回一行的 HTML（已转义）。"""
    if lang == "markup":
        return _highlight_markup(line)
    if lang == "markdown":
        return _highlight_markdown(line)
    if lang in ("text", "csv"):
        return _escape(line)

    prefix = COMMENT_PREFIX.get(lang, "")
    parts: list[str] = []
    rest = line
    keyword_re = _keyword_re(lang)

    # 注释优先（不在字符串里的简单判断：找第一个注释符，且它前面没有引号）
    comment_at = -1
    if prefix:
        idx = rest.find(prefix)
        while idx != -1:
            before = rest[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                comment_at = idx
                break
            idx = rest.find(prefix, idx + 1)

    code = rest if comment_at == -1 else rest[:comment_at]
    comment = "" if comment_at == -1 else rest[comment_at:]

    pattern = re.compile(
        r"(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"
        r"|(?P<number>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"
        r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"|(?P<other>[^A-Za-z0-9_]+)"
    )
    for match in pattern.finditer(code):
        kind = match.lastgroup
        text = match.group(0)
        if kind == "string":
            parts.append(f'<span class="tok-str">{_escape(text)}</span>')
        elif kind == "number":
            parts.append(f'<span class="tok-num">{_escape(text)}</span>')
        elif kind == "name":
            if keyword_re and keyword_re.fullmatch(text):
                parts.append(f'<span class="tok-kw">{_escape(text)}</span>')
            else:
                parts.append(_escape(text))
        else:
            parts.append(_escape(text))
    if comment:
        parts.append(f'<span class="tok-com">{_escape(comment)}</span>')
    return "".join(parts)


def _highlight_markup(line: str) -> str:
    """HTML/XML：标签名、属性名、属性值分开着色。"""
    pattern = re.compile(
        r"(?P<comment>&lt;!--.*?--&gt;)"
        r"|(?P<tag>&lt;/?[A-Za-z][\w:-]*)"
        r"|(?P<attr>\s[A-Za-z_:][\w:.-]*=)"
        r"|(?P<value>&quot;[^&]*?&quot;|'[^']*?')"
        r"|(?P<other>[^&<]+|&lt;|&gt;|.)"
    )
    escaped = html.escape(line, quote=True)
    parts = []
    for match in pattern.finditer(escaped):
        kind = match.lastgroup
        text = match.group(0)
        if kind == "comment":
            parts.append(f'<span class="tok-com">{text}</span>')
        elif kind == "tag":
            parts.append(f'<span class="tok-kw">{text}</span>')
        elif kind == "attr":
            parts.append(f'<span class="tok-attr">{text}</span>')
        elif kind == "value":
            parts.append(f'<span class="tok-str">{text}</span>')
        else:
            parts.append(text)
    return "".join(parts)


def _highlight_markdown(line: str) -> str:
    escaped = html.escape(line, quote=False)
    if escaped.startswith("#"):
        return f'<span class="tok-kw">{escaped}</span>'
    if escaped.startswith(("- ", "* ", "1. ")):
        return f'<span class="tok-attr">{escaped}</span>'
    if escaped.startswith(">"):
        return f'<span class="tok-com">{escaped}</span>'
    escaped = re.sub(r"`([^`]+)`", r'<span class="tok-str">`\1`</span>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r'<span class="tok-kw">**\1**</span>', escaped)
    return escaped


def highlight_lines(text: str, lang: str) -> list[str]:
    lines = text.split("\n")
    return [highlight_line(line, lang) for line in lines]
