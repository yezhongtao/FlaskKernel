"""SQLAlchemy 实例（全局唯一）+ 轻量结构升级。"""
from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def migrate_schema() -> None:
    """给老数据库补齐新增字段（SQLite 没有正式的迁移框架，这里做最小改动）。

    目前只处理一个：`menus.endpoint`（插件菜单存"接口名"而不是写死的 URL，
    这样改了 URL 映射后菜单会自动跟着变）。
    """
    from sqlalchemy import text

    try:
        rows = db.session.execute(text("PRAGMA table_info(menus)")).fetchall()
    except Exception:  # noqa: BLE001 - 非 SQLite 或表还不存在时直接跳过
        db.session.rollback()
        return

    columns = {row[1] for row in rows}
    if "endpoint" not in columns:
        db.session.execute(text("ALTER TABLE menus ADD COLUMN endpoint VARCHAR(200)"))
        db.session.commit()
