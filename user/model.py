"""User 模型。"""
from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from core.db import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_superadmin = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    permissions = db.Column(db.JSON, nullable=False, default=list)
    ext = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # -------- 属性 --------
    @property
    def is_active(self) -> bool:  # 覆盖 UserMixin 的 is_active
        return bool(self.active)

    # -------- 权限 --------
    def has_perm(self, code: str) -> bool:
        if self.is_superadmin:
            return True
        perms = self.permissions or []
        return code in perms

    # -------- 密码 --------
    def check_password(self, pwd: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, pwd)

    def set_password(self, pwd: str) -> None:
        self.password_hash = generate_password_hash(pwd)

    # -------- 序列化 --------
    def to_safe_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "is_superadmin": bool(self.is_superadmin),
            "active": bool(self.active),
            "permissions": list(self.permissions or []),
            "ext": dict(self.ext or {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.username}>"