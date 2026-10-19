"""内核数据模型：Plugin / 权限 / 菜单 / 路由。"""
from __future__ import annotations

from datetime import datetime

from core.db import db


class Plugin(db.Model):
    __tablename__ = "plugins"

    id = db.Column(db.String(160), primary_key=True)  # full_id
    author = db.Column(db.String(64), nullable=False, index=True)
    group_name = db.Column(db.String(64), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False, index=True)

    label = db.Column(db.String(128), default="")
    description = db.Column(db.Text, default="")
    version = db.Column(db.String(32), default="")

    parent = db.Column(db.String(160), nullable=True)
    parent_min_version = db.Column(db.String(32), nullable=True)

    dependencies = db.Column(db.JSON, nullable=False, default=dict)
    provides = db.Column(db.JSON, nullable=False, default=dict)

    url_prefix = db.Column(db.String(160), nullable=True)
    actual_url_prefix = db.Column(db.String(160), nullable=True)

    enabled = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(16), nullable=False, default="disabled")
    last_error = db.Column(db.Text, nullable=True)

    installed_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "author": self.author,
            "group": self.group_name,
            "name": self.name,
            "label": self.label,
            "description": self.description or "",
            "version": self.version,
            "parent": self.parent,
            "parent_min_version": self.parent_min_version,
            "dependencies": self.dependencies or {},
            "provides": self.provides or {},
            "url_prefix": self.url_prefix,
            "actual_url_prefix": self.actual_url_prefix,
            "enabled": bool(self.enabled),
            "status": self.status,
            "last_error": self.last_error,
            "installed_at": self.installed_at.isoformat() if self.installed_at else None,
        }

    def __repr__(self) -> str:
        return f"<Plugin {self.id} {self.status}>"


class PluginPermission(db.Model):
    __tablename__ = "plugin_permissions"

    id = db.Column(db.Integer, primary_key=True)
    plugin_id = db.Column(db.String(160), index=True, nullable=False)
    code = db.Column(db.String(64), nullable=False)          # 短码
    full_code = db.Column(db.String(255), nullable=False, index=True)
    label = db.Column(db.String(255), default="")

    __table_args__ = (
        db.UniqueConstraint("plugin_id", "code", name="uq_plugin_perm_code"),
    )


class PluginUsesPermission(db.Model):
    __tablename__ = "plugin_uses_permissions"

    id = db.Column(db.Integer, primary_key=True)
    plugin_id = db.Column(db.String(160), index=True, nullable=False)
    permission_code = db.Column(db.String(255), nullable=False, index=True)
    reason = db.Column(db.String(255), default="")

    __table_args__ = (
        db.UniqueConstraint("plugin_id", "permission_code", name="uq_plugin_uses_perm"),
    )


class Menu(db.Model):
    __tablename__ = "menus"

    id = db.Column(db.Integer, primary_key=True)
    plugin_id = db.Column(db.String(160), index=True, nullable=False)
    name = db.Column(db.String(200), unique=True, nullable=False)
    label = db.Column(db.String(128), default="")
    url = db.Column(db.String(255), default="")
    parent_id = db.Column(db.Integer, nullable=True)
    perm = db.Column(db.String(128), nullable=True)
    order = db.Column(db.Integer, default=0)
    icon = db.Column(db.String(64), nullable=True)


class Route(db.Model):
    __tablename__ = "routes"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(200), unique=True, nullable=False)
    owner_id = db.Column(db.String(160), index=True, nullable=False)  # full_id 或 __core__
    owner_kind = db.Column(db.String(16), nullable=False, default="plugin")  # core / plugin
    parent_id = db.Column(db.String(160), nullable=True)
    is_override = db.Column(db.Boolean, default=False)
    logical_path = db.Column(db.String(200), nullable=True)