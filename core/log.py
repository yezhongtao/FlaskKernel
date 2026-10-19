"""日志系统：根 logger + 按天切分文件 + 可选控制台。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "[%(asctime)s %(levelname)-8s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(log_dir: str | Path, level: str = "INFO", keep_days: int = 30) -> None:
    global _configured

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    file_handler = TimedRotatingFileHandler(
        filename=str(log_dir / "app.log"),
        when="midnight",
        interval=1,
        backupCount=max(int(keep_days), 1),
        encoding="utf-8",
        delay=False,
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 开发时挂控制台；打包后不挂
    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # 抑制 werkzeug 访问日志
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)