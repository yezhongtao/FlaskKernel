"""告警推送：进入安全模式 / 服务异常时 POST 到 webhook。

配置：
    "alert": {"webhook": "https://...", "timeout": 5}

webhook 地址留空时只写日志，不联网。推送在后台线程执行，
失败不会影响主流程（方案 2.5：进安全模式时发送告警，webhook 可配）。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from core.config import get_config
from core.log import get_logger

logger = get_logger(__name__)


def _post(url: str, payload: dict, timeout: float) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("告警推送失败（%s）: %s", url, exc)
        return False


def send_alert(title: str, message: str, level: str = "warning", **extra) -> None:
    """发一条告警。异步、失败静默。"""
    try:
        alert_cfg = (get_config() or {}).get("alert") or {}
    except Exception:
        alert_cfg = {}

    url = (alert_cfg.get("webhook") or "").strip()
    if not url:
        logger.info("告警（未配置 webhook）: [%s] %s", level, title)
        return

    payload = {
        "title": title,
        "message": message,
        "level": level,
        "source": "oa-kernel",
    }
    payload.update(extra)
    timeout = float(alert_cfg.get("timeout") or 5)

    threading.Thread(
        target=_post, args=(url, payload, timeout),
        name="oa-alert", daemon=True,
    ).start()
