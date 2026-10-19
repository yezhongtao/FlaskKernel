"""登录 / 注册 / 登出 / 个人信息 / 修改密码 / LoginManager / 默认超管。"""
from __future__ import annotations

from datetime import datetime

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)

from core import user_service
from core.db import db
from core.log import get_logger
from core.store import load_json
from core.config import CONFIG_FILE
from user.model import User

logger = get_logger(__name__)

DEFAULT_ADMIN_USERNAME = "yezhongtao"
DEFAULT_ADMIN_PASSWORD = "123456"

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录"
login_manager.login_message_category = "warning"

# 不再硬编码 url_prefix，由 app.py 按 kernel.auth_prefix 注册
auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


@login_manager.unauthorized_handler
def unauthorized():
    """未登录统一处理：页面跳登录页，fetch 请求返回 401 JSON。"""
    from flask import jsonify

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "code": 401,
                        "error": "登录已过期，请重新登录"}), 401

    flash(login_manager.login_message, login_manager.login_message_category)
    target = request.full_path if request.query_string else request.path
    if target.endswith("?"):
        target = target[:-1]
    return redirect(url_for("auth.login", next=target))


def register_allowed() -> bool:
    """是否开放自助注册（config.json 的 user.allow_register）。"""
    raw = load_json(CONFIG_FILE, {}) or {}
    user_cfg = raw.get("user") or {}
    return bool(user_cfg.get("allow_register", False))


# --------------------------------------------------------------------------- #
# 登录 / 登出
# --------------------------------------------------------------------------- #
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_next_url())

    error = None
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "on"

        user = User.query.filter_by(username=username).first()
        if user is None or not user.active or not user.check_password(password):
            error = "用户名或密码错误"
            logger.info("登录失败: %s", username)
        else:
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user, remember=remember)
            logger.info("用户登录: %s", username)
            return redirect(_next_url())

    return render_template(
        "auth/login.html", error=error, username=username,
        allow_register=register_allowed(),
    )


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logger.info("用户登出: %s", current_user.username)
    logout_user()
    flash("已退出登录", "success")
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    allow = register_allowed()
    if current_user.is_authenticated:
        return redirect(_next_url())

    if request.method == "POST":
        if not allow:
            flash("系统未开放自助注册，请联系管理员开通账号", "error")
            return render_template(
                "auth/register.html", allow_register=False,
                password_min=user_service.password_min_length(),
            ), 403

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        try:
            user = user_service.register(username, password, password2)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "auth/register.html", allow_register=True, username=username,
                password_min=user_service.password_min_length(),
            ), 400

        login_user(user, remember=False)
        flash(f"注册成功，欢迎 {user.username}", "success")
        logger.info("用户注册并自动登录: %s", user.username)
        return redirect(url_for("core.index"))

    return render_template(
        "auth/register.html", allow_register=allow,
        password_min=user_service.password_min_length(),
    )


# --------------------------------------------------------------------------- #
# 个人信息 / 修改密码
# --------------------------------------------------------------------------- #
@auth_bp.route("/profile")
@login_required
def profile():
    return render_template(
        "auth/profile.html",
        permission_count=len(current_user.permissions or []),
        catalog=user_service.permission_catalog(),
        password_min=user_service.password_min_length(),
    )


@auth_bp.route("/password", methods=["POST"])
@login_required
def change_password():
    old = request.form.get("old_password") or ""
    new = request.form.get("new_password") or ""
    new2 = request.form.get("new_password2") or ""
    try:
        user_service.change_password(current_user, old, new, new2)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("auth.profile"))

    flash("密码已修改，下次请用新密码登录", "success")
    return redirect(url_for("auth.profile"))


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _next_url() -> str:
    nxt = request.args.get("next") or request.form.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for("core.index")


def ensure_default_admin() -> None:
    """无用户时创建默认超管，只执行一次。"""
    if User.query.first() is not None:
        return
    user = User(
        username=DEFAULT_ADMIN_USERNAME,
        is_superadmin=True,
        active=True,
        permissions=[],
        ext={},
    )
    user.set_password(DEFAULT_ADMIN_PASSWORD)
    db.session.add(user)
    db.session.commit()
    logger.warning(
        "已创建默认超管: %s / %s（请立即在【个人中心 → 修改密码】里改掉）",
        DEFAULT_ADMIN_USERNAME,
        DEFAULT_ADMIN_PASSWORD,
    )
