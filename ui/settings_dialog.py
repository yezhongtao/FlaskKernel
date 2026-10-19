"""桌面 UI 的「项目设置」：与网页 /admin/settings 用同一份 CONFIG_SCHEMA。

两边的字段、校验、重启规则完全一致，避免桌面端和网页端能改的东西不一样。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from core import config_schema, service_control
from core.config import get_config, update_config


class SettingsDialog(QDialog):
    """全量配置编辑（应用 / 内核 URL / 日志 / 桌面面板 / 插件 / 服务 / 用户 / 告警）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目设置")
        self.setObjectName("SettingsDialog")
        self.resize(820, 660)
        self.cfg = get_config(reload=True)
        self.widgets: dict[str, tuple[dict, QWidget]] = {}
        self._build()
        self.setStyleSheet(parent.styleSheet() if parent else "")

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title = QLabel("项目设置")
        title.setObjectName("HeaderTitle")
        root.addWidget(title)

        hint = QLabel(
            "与网页「系统设置」同一份配置（config.json）。带「需重启」的项保存后点右侧按钮重启服务生效。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.tabs = QTabWidget(self)
        for group in config_schema.CONFIG_SCHEMA:
            self.tabs.addTab(self._build_section(group), group["title"])
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        footer.addWidget(self.status, 1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存设置")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

    def _build_section(self, group: dict) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        for field in group["fields"]:
            path = f"{group['section']}.{field['key']}"
            value = config_schema.display_value(self.cfg, group["section"],
                                                field["key"], field["type"])
            widget = self._make_widget(field, value)
            if field.get("help"):
                widget.setToolTip(field["help"])
            label = QLabel(field["label"] + ("　⏻" if field.get("restart") else ""))
            if field.get("help"):
                label.setToolTip(field["help"])
            self.widgets[path] = (field, widget)
            form.addRow(label, widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _make_widget(self, field: dict, value: str) -> QWidget:
        ftype = field["type"]
        if ftype == "bool":
            box = QCheckBox("启用")
            box.setChecked(value in ("1", "true", "on"))
            return box
        if ftype == "select":
            combo = QComboBox()
            for option in field.get("options") or []:
                combo.addItem(str(option))
            if value in [str(o) for o in field.get("options") or []]:
                combo.setCurrentText(value)
            return combo
        if ftype == "int":
            spin = QSpinBox()
            spin.setRange(int(field.get("min", 0)), int(field.get("max", 1000000)))
            try:
                spin.setValue(int(float(value or 0)))
            except (TypeError, ValueError):
                spin.setValue(0)
            spin.setMinimumWidth(160)
            return spin
        if ftype == "json":
            editor = QPlainTextEdit(value)
            editor.setFixedHeight(96)
            editor.setObjectName("Mono")
            return editor
        line = QLineEdit(value)
        if ftype == "secret":
            line.setEchoMode(QLineEdit.Password)
            line.setPlaceholderText("留空 = 下次启动自动生成")
        return line

    # ------------------------------------------------------------------ 收集
    def _collect(self) -> dict:
        form: dict[str, str] = {}
        for path, (field, widget) in self.widgets.items():
            ftype = field["type"]
            if ftype == "bool":
                form[f"__bool__{path}"] = "1"
                form[path] = "on" if widget.isChecked() else ""
            elif ftype == "select":
                form[path] = widget.currentText()
            elif ftype == "int":
                form[path] = str(widget.value())
            elif ftype == "json":
                form[path] = widget.toPlainText()
            else:
                form[path] = widget.text()
        return form

    # ------------------------------------------------------------------ 保存
    def _save(self) -> None:
        patch, errors, restart_paths = config_schema.apply_form(self._collect(), self.cfg)
        if errors:
            QMessageBox.warning(self, "有项填写不对", "\n".join(errors))
            return
        if not patch:
            self.status.setText("没有改动")
            return

        update_config(patch)
        self.cfg = get_config(reload=True)

        if "service" in patch and "keepalive" in patch["service"]:
            try:
                service_control.set_keepalive(bool(patch["service"]["keepalive"]))
            except Exception:  # noqa: BLE001
                pass

        summary = "已保存 %d 个配置项" % sum(len(v) for v in patch.values() if isinstance(v, dict))
        self.status.setText(summary)
        self.accept()

        if restart_paths:
            answer = QMessageBox.question(
                self, "需要重启服务",
                f"已保存。其中 {len(restart_paths)} 项需要重启服务才生效：\n"
                + "、".join(restart_paths[:8])
                + ("\n…" if len(restart_paths) > 8 else "")
                + "\n\n现在重启服务吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                try:
                    service_control.request_control("restart", requested_by="desktop-ui")
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(self, "下发失败", f"无法写入控制指令：{exc}")
        else:
            QMessageBox.information(self, "已保存", "配置已保存，立即生效。")


def open_settings(parent=None) -> None:
    """给控制面板 / 托盘菜单调用的入口。"""
    dialog = SettingsDialog(parent)
    dialog.exec()
