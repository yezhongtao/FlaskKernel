"""HTTP 请求体读取的小工具。

页面里的前端脚本通过 `App.api(...)` 提交时会自动发 **JSON**
（`Content-Type: application/json`），而传统的 HTML 表单提交发的是
`application/x-www-form-urlencoded`。

内核的接口两种都要能接：用 `payload()` 统一读取，别直接写 `request.form.get(...)`，
否则前端用 JS 提交时永远取不到值。
"""
from __future__ import annotations

from flask import request


def payload() -> dict:
    """把 JSON 请求体或表单字段统一读成 dict（JSON 优先）。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    form = request.form.to_dict(flat=True)
    if form:
        return form
    # 少数情况下只发了 query string（例如没写 body 的 POST）
    return request.args.to_dict(flat=True)
