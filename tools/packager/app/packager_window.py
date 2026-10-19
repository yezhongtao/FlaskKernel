"""插件打包工具主窗口（PySide6 桌面 UI）。

界面结构
--------
    ┌ 顶部：标题 + 当前插件 ID + Python 版本 ──────────────┐
    │ ① 目录   ② 声明（左表单 / 右实时 JSON）             │
    │ ③ 依赖   ④ 打包                                    │
    ├ 底部：运行日志 ───────────────────────────────────┤
    └ 状态栏 + 细进度条 ─────────────────────────────────┘

耗时动作（分析 / 内置依赖 / 打包）都在 QThread 里跑，界面不卡。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

import packager_core as core
from packager_ui import (Task, block_title, button, card, field, item, label,
                         table)

APPLICATION = "PluginPackager"
ORGANIZATION = "FlaskKernel"
# 设置写在工具目录旁，不碰注册表，也方便随工具一起搬走
SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.ini"


def make_icon() -> QIcon:
    """程序化画一个图标（靛蓝渐变 + PK 字样），不依赖外部资源。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0, QColor("#6366f1"))
    gradient.setColorAt(1, QColor("#a855f7"))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 16, 16)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Segoe UI", 22, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "PK")
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("插件打包工具")
        self.setWindowIcon(make_icon())
        self.resize(1220, 880)
        self.setMinimumSize(1000, 720)

        self.settings = QSettings(str(SETTINGS_FILE), QSettings.IniFormat)

        self.dir: str = ""
        self.extras: dict = {}
        self.interfaces: list[dict] = []
        self.analysis: dict | None = None
        self.last_pack: dict | None = None
        self.task: Task | None = None
        self._busy_button: QPushButton | None = None
        self._busy_text: str = ""
        self._loading = False
        self._pending_analyze = False

        self._build_ui()
        self._bind()
        self._restore_state()
        self._refresh_preview()

        self._log("工具已启动，Python：" + sys.executable)
        self._log("第 1 步：选一个插件目录（也可以把文件夹拖进窗口）")

        self.setAcceptDrops(True)

    # ================================================================== 界面
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_tab_dir(), "① 目录")
        self.tabs.addTab(self._build_tab_manifest(), "② 声明")
        self.tabs.addTab(self._build_tab_deps(), "③ 依赖")
        self.tabs.addTab(self._build_tab_pack(), "④ 打包")
        self.tabs.setTabToolTip(0, "选择插件目录并扫描现有 plugin.json")
        self.tabs.setTabToolTip(1, "填写插件声明，右侧实时预览 plugin.json")
        self.tabs.setTabToolTip(2, "分析 import，把第三方依赖自动内置到 libs/")
        self.tabs.setTabToolTip(3, "校验声明并生成可直接安装的 zip")
        outer.addWidget(self.tabs, 1)

        outer.addWidget(self._build_log())

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar{border:0;background:#eef1f6;}"
            "QProgressBar::chunk{background:#4f46e5;border-radius:2px;}"
        )
        outer.addWidget(self.progress)

        self.statusBar().showMessage("就绪")

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(72)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setPixmap(make_icon().pixmap(38, 38))
        icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(icon_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        title = QLabel("插件打包工具")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("填声明 → 自动内置依赖 → 生成可直接安装的 zip")
        subtitle.setObjectName("HeaderSub")
        text_box.addWidget(title)
        text_box.addWidget(subtitle)
        layout.addLayout(text_box)
        layout.addStretch(1)

        self.head_id = QLabel("未选择插件")
        self.head_id.setStyleSheet(
            "background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.28);"
            "border-radius: 9px; padding: 4px 10px; color: #ffffff;"
            "font-family: Consolas, monospace; font-size: 11.5px;"
        )
        layout.addWidget(self.head_id)

        version = QLabel(f"Python {sys.version.split()[0]}")
        version.setStyleSheet(
            "background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.22);"
            "border-radius: 9px; padding: 4px 10px; color: #e8ecff; font-size: 11px;"
        )
        layout.addWidget(version)
        return header

    # ---------------------------------------------------------- ① 目录
    def _build_tab_dir(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        frame, body = card(
            "选择插件目录",
            "目录名建议就是 author.group.name；里面至少要有 hooks.py。"
            "扫描会读取已有 plugin.json 并回填到「② 声明」。",
        )

        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(
            r"例如 G:\File\FlaskKernel\plugins\yezhongtao.oa.notice")
        self.dir_edit.setClearButtonEnabled(True)

        picker = QHBoxLayout()
        picker.setSpacing(8)
        self.btn_browse = button("选择文件夹…", slot=self.on_browse)
        self.btn_scan = button("扫描", "Primary", self.on_scan)
        picker.addWidget(self.dir_edit, 1)
        picker.addWidget(self.btn_browse)
        picker.addWidget(self.btn_scan)
        body.addLayout(picker)

        recent_row = QHBoxLayout()
        recent_row.setSpacing(8)
        recent_row.addWidget(label("最近使用", "SectionSub"))
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(380)
        self.recent_combo.setToolTip("点选可直接打开最近扫描过的插件目录")
        self.recent_combo.activated.connect(self._on_recent_activated)
        recent_row.addWidget(self.recent_combo)
        recent_row.addStretch(1)
        self.btn_open_dir = button("在资源管理器中打开", slot=self.on_open_plugin_dir)
        recent_row.addWidget(self.btn_open_dir)
        body.addLayout(recent_row)

        self.dir_info = label("尚未扫描", "SectionSub", wrap=True)
        self.dir_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.dir_info)

        self.dir_files = QPlainTextEdit()
        self.dir_files.setReadOnly(True)
        self.dir_files.setFixedHeight(120)
        self.dir_files.setPlaceholderText("扫描后这里会列出 .py / libs / import 概况")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.dir_files.setFont(mono)
        body.addWidget(self.dir_files)

        layout.addWidget(frame)

        tip, tip_body = card("扫描结果怎么看")
        tip_body.addWidget(label(
            "· hooks.py：内核加载插件必需，缺失会直接提示\n"
            "· Python 文件 / templates / static：会打进 zip 的内容\n"
            "· libs：已经内置的第三方依赖，扫描 import 时会跳过它们\n"
            "· plugin.json：有就回填表单；没有就按目录名预填 author / group / name",
            "SectionSub", wrap=True))
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------- ② 声明
    def _build_tab_manifest(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_validate = button("校验声明", slot=self.on_validate)
        self.btn_save_manifest = button("保存 plugin.json", slot=self.on_save_manifest)
        self.validate_label = label("", "SectionSub", wrap=True)
        bar.addWidget(self.btn_validate)
        bar.addWidget(self.btn_save_manifest)
        bar.addWidget(self.validate_label, 1)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_manifest_form())

        preview = QFrame()
        preview.setObjectName("Card")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(block_title("plugin.json 实时预览"))
        preview_layout.addWidget(label(
            "下面就是打包时写进插件目录的内容，随表单实时更新。",
            "Hint", wrap=True))
        self.preview_view = QPlainTextEdit()
        self.preview_view.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.preview_view.setFont(mono)
        preview_layout.addWidget(self.preview_view, 1)
        splitter.addWidget(preview)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([720, 460])
        layout.addWidget(splitter, 1)
        return page

    def _build_manifest_form(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 12, 4)
        body.setSpacing(14)

        # ---- 基本信息 ----
        basic, basic_body = card("基本信息", "必填四项：author / group / name / version")
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("yezhongtao")
        self.group_edit = QLineEdit()
        self.group_edit.setPlaceholderText("oa")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("notice")
        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("1.0.0")
        for widget in (self.author_edit, self.group_edit, self.name_edit,
                       self.version_edit):
            widget.setFont(QFont("Consolas", 10))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(field("author *", self.author_edit, "小写字母开头"), 0, 0)
        grid.addWidget(field("group *", self.group_edit, "同上"), 0, 1)
        grid.addWidget(field("name *", self.name_edit, "同上"), 0, 2)
        grid.addWidget(field("version *", self.version_edit, "数字段，如 1.0.0"), 0, 3)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        basic_body.addLayout(grid)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("公告通知")
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("/notice")
        self.prefix_edit.setFont(QFont("Consolas", 10))
        self.parent_edit = QLineEdit()
        self.parent_edit.setPlaceholderText("留空 = 独立插件")
        self.parent_edit.setFont(QFont("Consolas", 10))
        self.parent_min_edit = QLineEdit()
        self.parent_min_edit.setPlaceholderText("1.0.0")
        self.parent_min_edit.setFont(QFont("Consolas", 10))

        row = QGridLayout()
        row.setHorizontalSpacing(12)
        row.setVerticalSpacing(10)
        row.addWidget(field("label（显示名）", self.label_edit), 0, 0)
        row.addWidget(field("url_prefix", self.prefix_edit,
                            "冲突时自动落到 /plugin/<id>"), 0, 1)
        row.addWidget(field("parent（父插件）", self.parent_edit,
                            "父插件停用会级联停用我"), 0, 2)
        row.addWidget(field("parent_min_version", self.parent_min_edit), 0, 3)
        for column in range(4):
            row.setColumnStretch(column, 1)
        basic_body.addLayout(row)

        self.id_preview = QLineEdit()
        self.id_preview.setReadOnly(True)
        self.id_preview.setFont(QFont("Consolas", 10))
        basic_body.addWidget(field("生成的插件 ID（= author.group.name）", self.id_preview,
                                   "也是蓝图名与权限前缀"))

        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setObjectName("InputText")
        self.desc_edit.setPlaceholderText("发布和查看公告")
        self.desc_edit.setFixedHeight(58)
        basic_body.addWidget(field("description（描述）", self.desc_edit))
        body.addWidget(basic)

        # ---- 依赖插件 ----
        deps_card, deps_body = card("dependencies · 依赖的其它插件",
                                    "被依赖插件缺失或版本不够时，本插件会进入 error 状态")
        self.deps_table = self._two_column_table("插件 ID", "最低版本", 92)
        deps_body.addLayout(self._table_section(self.deps_table, "添加依赖",
                                                self.on_add_dep, self.on_remove_dep))
        body.addWidget(deps_card)

        # ---- 权限 ----
        perms_card, perms_body = card("permissions · 我提供的权限点",
                                      "内核自动加前缀，完整码是 <插件id>.<短码>")
        self.perms_table = self._two_column_table("权限短码", "说明", 92)
        perms_body.addLayout(self._table_section(self.perms_table, "添加权限",
                                                 self.on_add_perm, self.on_remove_perm))
        body.addWidget(perms_card)

        # ---- 使用他人权限 ----
        uses_card, uses_body = card("uses_permissions · 我要用别人的权限",
                                    "跨插件调用前必须声明，否则调用会抛 PermissionError")
        self.uses_table = self._two_column_table("完整权限码", "用途", 92)
        uses_body.addLayout(self._table_section(self.uses_table, "添加声明",
                                                self.on_add_uses, self.on_remove_uses))
        body.addWidget(uses_card)

        # ---- 接口 ----
        provide_card, provide_body = card("provides · 我对外提供的接口",
                                          "key 建议写成「模块.类型」，例如 notice.api；"
                                          "选中左侧接口后，在右侧编辑它的方法")
        self.provides_table = self._two_column_table("接口 key", "接口说明", 110)
        self.methods_table = table(["方法名", "说明", "requires_perm"], 110)
        self.methods_table.setEnabled(False)

        provide_row = QHBoxLayout()
        provide_row.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(block_title("接口列表"))
        left.addWidget(self.provides_table, 1)
        left_buttons = QHBoxLayout()
        left_buttons.addStretch(1)
        left_buttons.addWidget(button("添加接口", slot=self.on_add_provide))
        left_buttons.addWidget(button("删除接口", "Danger", self.on_remove_provide))
        left.addLayout(left_buttons)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(block_title("方法列表"))
        right.addWidget(self.methods_table, 1)
        right_buttons = QHBoxLayout()
        right_buttons.addStretch(1)
        right_buttons.addWidget(button("添加方法", slot=self.on_add_method))
        right_buttons.addWidget(button("删除方法", "Danger", self.on_remove_method))
        right.addLayout(right_buttons)

        provide_row.addLayout(left, 1)
        provide_row.addLayout(right, 1)
        provide_body.addLayout(provide_row)
        body.addWidget(provide_card)

        body.addStretch(1)
        scroll.setWidget(content)
        return scroll

    # ---------------------------------------------------------- ③ 依赖
    def _build_tab_deps(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_analyze = button("分析依赖", slot=self.on_analyze)
        self.btn_vendor = button("一键内置缺失依赖", "Primary", self.on_vendor)
        self.chk_metadata = QCheckBox("同时收录 .dist-info")
        self.chk_metadata.setToolTip("保留后插件里能查依赖版本，包会稍大")
        bar.addWidget(self.btn_analyze)
        bar.addWidget(self.btn_vendor)
        bar.addWidget(self.chk_metadata)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.dep_summary = label("还没有分析，点「分析依赖」开始。", "SectionSub", wrap=True)
        layout.addWidget(self.dep_summary)

        self.analysis_table = table(
            ["模块", "引用位置", "类型", "发行版", "版本", "状态"], 260)
        layout.addWidget(self.analysis_table, 1)

        note, note_body = card("内置依赖的原理")
        note_body.addWidget(label(
            "1. 扫描插件目录里的 .py，把 import 分成：标准库 / 内核已提供 / 插件本地 / 第三方；\n"
            "2. 第三方用 importlib.metadata 找到发行版与版本，按发行版的文件清单复制进 libs/；\n"
            "3. 会递归带上它的依赖（传递依赖），flask / sqlalchemy 等内核已有库自动跳过；\n"
            "4. 本机没装的依赖才走 python -m pip install --target libs（需要联网）；\n"
            "5. 内核加载插件时会把 libs/ 临时加进 sys.path，所以换台机器也能直接 import。",
            "SectionSub", wrap=True))
        layout.addWidget(note)
        return page

    # ---------------------------------------------------------- ④ 打包
    def _build_tab_pack(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        options, options_body = card("打包选项",
                                     "输出 <插件id>.zip，zip 根目录直接是 plugin.json，"
                                     "可在「插件管理 → 安装插件」上传。")
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空 = 插件目录旁的 dist/")
        self.out_edit.setClearButtonEnabled(True)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(button("选择…", slot=self.on_pick_out))
        options_body.addWidget(field("输出目录", self._wrap_layout(out_row),
                                     "建议统一放到项目的 dist/ 目录"))

        checks = QHBoxLayout()
        checks.setSpacing(18)
        self.chk_write = QCheckBox("写回 plugin.json")
        self.chk_write.setChecked(True)
        self.chk_auto = QCheckBox("自动内置缺失依赖")
        self.chk_auto.setChecked(True)
        self.chk_data = QCheckBox("打包 data/ 目录")
        self.chk_data.setToolTip("默认排除运行时数据")
        checks.addWidget(self.chk_write)
        checks.addWidget(self.chk_auto)
        checks.addWidget(self.chk_data)
        checks.addStretch(1)
        options_body.addLayout(checks)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_pack = button("生成插件包", "Primary", self.on_pack)
        self.btn_open_out = button("打开输出目录", slot=self.on_open_out)
        self.btn_open_out.setEnabled(False)
        self.pack_hint = label("会先校验声明，再内置依赖，最后打 zip", "Hint")
        actions.addWidget(self.btn_pack)
        actions.addWidget(self.btn_open_out)
        actions.addWidget(self.pack_hint, 1)
        options_body.addLayout(actions)
        layout.addWidget(options)

        self.pack_result, result_body = card("打包结果")
        self.pack_result.setVisible(False)
        self.pack_title = label("还没有打包", "BlockTitle")
        result_body.addWidget(self.pack_title)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.pack_path = QLineEdit()
        self.pack_path.setReadOnly(True)
        self.pack_path.setFont(QFont("Consolas", 9))
        self.btn_copy_path = button("复制路径", slot=self.on_copy_zip)
        self.btn_reveal = button("打开文件夹", slot=self.on_open_out)
        path_row.addWidget(self.pack_path, 1)
        path_row.addWidget(self.btn_copy_path)
        path_row.addWidget(self.btn_reveal)
        result_body.addLayout(path_row)

        self.pack_meta = label("", "SectionSub", wrap=True)
        result_body.addWidget(self.pack_meta)
        self.pack_notes = label("", "SectionSub", wrap=True)
        result_body.addWidget(self.pack_notes)

        self.pack_files = QPlainTextEdit()
        self.pack_files.setReadOnly(True)
        self.pack_files.setFixedHeight(140)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.pack_files.setFont(mono)
        result_body.addWidget(self.pack_files)

        layout.addWidget(self.pack_result, 1)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------- 日志
    def _build_log(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Card")
        box.setFixedHeight(150)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.addWidget(block_title("运行日志"))
        head.addStretch(1)
        head.addWidget(button("清空", slot=self.on_clear_log))
        layout.addLayout(head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.log_view.setFont(mono)
        layout.addWidget(self.log_view)
        return box

    # ---------------------------------------------------------- 小工具
    @staticmethod
    def _wrap_layout(layout) -> QWidget:
        widget = QWidget()
        widget.setObjectName("RowBox")
        widget.setLayout(layout)
        return widget

    def _two_column_table(self, first: str, second: str, height: int):
        widget = table([first, second], height)
        widget.setColumnWidth(0, 230)
        widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        return widget

    def _table_section(self, widget, add_text: str, add_slot, remove_slot) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        box.addWidget(widget)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button(add_text, slot=add_slot))
        buttons.addWidget(button("删除选中", "Danger", remove_slot))
        box.addLayout(buttons)
        return box

    # ================================================================== 绑定
    def _bind(self) -> None:
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(220)
        self._preview_timer.timeout.connect(self._refresh_preview)

        for widget in (self.author_edit, self.group_edit, self.name_edit,
                       self.version_edit, self.label_edit, self.prefix_edit,
                       self.parent_edit, self.parent_min_edit):
            widget.textChanged.connect(self._schedule_preview)
        self.desc_edit.textChanged.connect(self._schedule_preview)
        for widget in (self.deps_table, self.perms_table, self.uses_table,
                       self.provides_table, self.methods_table):
            widget.itemChanged.connect(self._schedule_preview)

        self.provides_table.itemSelectionChanged.connect(self._load_methods)
        self.provides_table.itemChanged.connect(self._on_provide_edited)
        self.methods_table.itemChanged.connect(self._on_method_edited)
        self.dir_edit.returnPressed.connect(self.on_scan)

    def _schedule_preview(self, *_args) -> None:
        if self._loading:
            return
        self._update_head_id()
        self._preview_timer.start()

    # ================================================================== 设置
    def _restore_state(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._refresh_recent()
        last_out = self.settings.value("last_out_dir", "")
        if last_out:
            self.out_edit.setText(str(last_out))
        last_dir = self.settings.value("last_dir", "")
        if last_dir and Path(str(last_dir)).is_dir():
            self.dir_edit.setText(str(last_dir))

    def _save_state(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("last_dir", self.dir or self.dir_edit.text().strip())
        self.settings.setValue("last_out_dir", self.out_edit.text().strip())

    def _recent_dirs(self) -> list[str]:
        raw = self.settings.value("recent_dirs", [])
        if isinstance(raw, str):
            raw = [raw]
        return [str(entry) for entry in (raw or []) if str(entry).strip()]

    def _push_recent(self, path: str) -> None:
        items = [entry for entry in self._recent_dirs() if entry != path]
        items.insert(0, path)
        self.settings.setValue("recent_dirs", items[:8])
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        items = self._recent_dirs()
        if not items:
            self.recent_combo.addItem("（还没有记录）", "")
        else:
            self.recent_combo.addItem("选择最近打开过的目录…", "")
            for path in items:
                self.recent_combo.addItem(path, path)
        self.recent_combo.blockSignals(False)

    def _on_recent_activated(self, _index: int) -> None:
        path = self.recent_combo.currentData()
        if not path:
            return
        self.dir_edit.setText(str(path))
        self.on_scan()

    # ================================================================== 目录
    def on_browse(self) -> None:
        start = self.dir_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择插件目录", start)
        if not selected:
            return
        self.dir_edit.setText(str(Path(selected)))
        self.on_scan()

    def on_open_plugin_dir(self) -> None:
        target = self.dir or self.dir_edit.text().strip()
        if not target or not Path(target).is_dir():
            self._warn("请先选择并扫描一个插件目录")
            return
        try:
            core.open_path(target)
        except Exception as exc:  # noqa: BLE001
            self._warn(str(exc))

    def on_scan(self) -> None:
        raw = self.dir_edit.text().strip()
        try:
            data = core.scan_plugin_dir(raw)
        except ValueError as exc:
            self._warn(str(exc))
            return

        self.dir = data["path"]
        self.dir_edit.setText(self.dir)
        self._render_dir_info(data)
        self._push_recent(self.dir)

        if data["has_manifest"]:
            self._fill_manifest(data["manifest"])
            self._log(f"已读取 plugin.json：{data['path']}")
        else:
            self._prefill_from_folder(data)
            self._log(f"没有 plugin.json，已按目录名预填：{data['name']}")

        if data.get("manifest_error"):
            self._warn(data["manifest_error"])
        if not data["has_hooks"]:
            self._warn("这个目录缺少 hooks.py，内核无法加载该插件")

        self._refresh_preview()
        self.statusBar().showMessage(f"已扫描：{data['name']}", 6000)
        self._save_state()
        if self.tabs.currentIndex() == 0:
            self.tabs.setCurrentIndex(1)

    def _render_dir_info(self, data: dict) -> None:
        parts = [
            f"路径：{data['path']}",
            f"hooks.py：{'有' if data['has_hooks'] else '缺失'}",
            f"Python 文件：{data['py_count']}",
            f"templates：{data['templates']} 个文件",
            f"static：{data['static']} 个文件",
            f"libs：{data['libs_count']} 项",
            f"plugin.json：{'已存在' if data['has_manifest'] else '没有'}",
            f"总体积：{self._fmt_size(data['size'])}",
        ]
        if data["data_files"]:
            parts.append(f"data/：{data['data_files']} 个文件（默认不入包）")
        self.dir_info.setText("  ·  ".join(parts))

        lines = [f"路径：{data['path']}"]
        if data["py_files"]:
            lines.append("py：" + "、".join(data["py_files"]))
        if data["libs"]:
            lines.append("libs：" + "、".join(data["libs"]))
        if data.get("imports"):
            modules = "、".join(sorted({row["module"] for row in data["imports"]}))
            lines.append("import：" + modules)
        self.dir_files.setPlainText("\n".join(lines))

    def _prefill_from_folder(self, data: dict) -> None:
        parts = data["name"].split(".")
        if len(parts) == 3:
            self.author_edit.setText(parts[0])
            self.group_edit.setText(parts[1])
            self.name_edit.setText(parts[2])
        if not self.version_edit.text().strip():
            self.version_edit.setText("1.0.0")
        self._update_head_id()

    # ================================================================== 表单
    def _update_head_id(self) -> None:
        parts = [self.author_edit.text().strip(), self.group_edit.text().strip(),
                 self.name_edit.text().strip()]
        plugin_id = "" if any(not part for part in parts) else ".".join(parts)
        self.id_preview.setText(plugin_id)
        self.head_id.setText(plugin_id or "未填写插件 ID")

    def _collect_manifest(self) -> dict:
        return {
            "author": self.author_edit.text().strip(),
            "group": self.group_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "version": self.version_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "parent": self.parent_edit.text().strip(),
            "parent_min_version": self.parent_min_edit.text().strip(),
            "url_prefix": self.prefix_edit.text().strip(),
            "dependencies": self._table_dict(self.deps_table),
            "permissions": self._table_dict(self.perms_table),
            "uses_permissions": self._table_dict(self.uses_table),
            "provides": self._collect_provides(),
            "_extras": dict(self.extras),
        }

    def _fill_manifest(self, manifest: dict) -> None:
        self._loading = True
        try:
            self.author_edit.setText(manifest.get("author") or "")
            self.group_edit.setText(manifest.get("group") or "")
            self.name_edit.setText(manifest.get("name") or "")
            self.version_edit.setText(manifest.get("version") or "")
            self.label_edit.setText(manifest.get("label") or "")
            self.desc_edit.setPlainText(manifest.get("description") or "")
            self.parent_edit.setText(manifest.get("parent") or "")
            self.parent_min_edit.setText(manifest.get("parent_min_version") or "")
            self.prefix_edit.setText(manifest.get("url_prefix") or "")

            self.extras = {
                key: value for key, value in manifest.items()
                if key not in {
                    "author", "group", "name", "version", "label", "description",
                    "parent", "parent_min_version", "url_prefix", "dependencies",
                    "permissions", "uses_permissions", "provides",
                }
            }

            self._fill_table(self.deps_table, manifest.get("dependencies") or {})
            self._fill_table(self.perms_table, manifest.get("permissions") or {})
            self._fill_table(self.uses_table, manifest.get("uses_permissions") or {})

            self.interfaces = []
            for key, spec in (manifest.get("provides") or {}).items():
                spec = spec if isinstance(spec, dict) else {}
                self.interfaces.append({
                    "key": key,
                    "label": spec.get("label") or "",
                    "methods": [
                        {
                            "name": method.get("name") or "",
                            "label": method.get("label") or "",
                            "requires_perm": method.get("requires_perm") or "",
                        }
                        for method in (spec.get("methods") or [])
                        if isinstance(method, dict)
                    ],
                })
            self._fill_provides()
        finally:
            self._loading = False
            self._update_head_id()

    @staticmethod
    def _table_dict(widget) -> dict:
        result: dict[str, str] = {}
        for row in range(widget.rowCount()):
            key_item = widget.item(row, 0)
            value_item = widget.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text().strip() if value_item else ""
            if key:
                result[key] = value
        return result

    @staticmethod
    def _fill_table(widget, data: dict) -> None:
        widget.blockSignals(True)
        widget.setRowCount(0)
        for key, value in (data or {}).items():
            row = widget.rowCount()
            widget.insertRow(row)
            widget.setItem(row, 0, item(str(key), mono=True))
            widget.setItem(row, 1, item(str(value)))
        widget.blockSignals(False)

    def _refresh_preview(self) -> None:
        manifest = core.build_manifest(self._collect_manifest())
        self.preview_view.setPlainText(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )

    # ---------------------------------------------------------- provides
    def _collect_provides(self) -> dict:
        self._sync_interfaces_from_table()
        result: dict[str, dict] = {}
        for entry in self.interfaces:
            key = (entry.get("key") or "").strip()
            if not key:
                continue
            spec: dict = {}
            if entry.get("label"):
                spec["label"] = entry["label"]
            methods = []
            for method in entry.get("methods") or []:
                name = (method.get("name") or "").strip()
                if not name:
                    continue
                row = {"name": name}
                if method.get("label"):
                    row["label"] = method["label"]
                if method.get("requires_perm"):
                    row["requires_perm"] = method["requires_perm"]
                methods.append(row)
            if methods:
                spec["methods"] = methods
            result[key] = spec
        return result

    def _sync_interfaces_from_table(self) -> None:
        for row in range(min(self.provides_table.rowCount(), len(self.interfaces))):
            key_item = self.provides_table.item(row, 0)
            label_item = self.provides_table.item(row, 1)
            self.interfaces[row]["key"] = key_item.text().strip() if key_item else ""
            self.interfaces[row]["label"] = label_item.text().strip() if label_item else ""

    def _fill_provides(self) -> None:
        self.provides_table.blockSignals(True)
        self.provides_table.setRowCount(0)
        for entry in self.interfaces:
            row = self.provides_table.rowCount()
            self.provides_table.insertRow(row)
            self.provides_table.setItem(row, 0, item(entry.get("key") or "", mono=True))
            self.provides_table.setItem(row, 1, item(entry.get("label") or ""))
        self.provides_table.blockSignals(False)
        if self.interfaces:
            self.provides_table.selectRow(0)
        self._load_methods()

    def _current_interface_index(self) -> int:
        model = self.provides_table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return -1
        index = rows[0].row()
        return index if index < len(self.interfaces) else -1

    def _load_methods(self) -> None:
        index = self._current_interface_index()
        self._loading = True
        try:
            self.methods_table.blockSignals(True)
            self.methods_table.setRowCount(0)
            if index < 0:
                self.methods_table.setEnabled(False)
                return
            self.methods_table.setEnabled(True)
            for method in self.interfaces[index].get("methods") or []:
                row = self.methods_table.rowCount()
                self.methods_table.insertRow(row)
                self.methods_table.setItem(row, 0, item(method.get("name") or "", mono=True))
                self.methods_table.setItem(row, 1, item(method.get("label") or ""))
                self.methods_table.setItem(
                    row, 2, item(method.get("requires_perm") or "", mono=True))
        finally:
            self.methods_table.blockSignals(False)
            self._loading = False

    def _on_provide_edited(self, _item) -> None:
        if self._loading:
            return
        self._sync_interfaces_from_table()

    def _on_method_edited(self, _item) -> None:
        if self._loading:
            return
        index = self._current_interface_index()
        if index < 0:
            return
        methods = []
        for row in range(self.methods_table.rowCount()):
            values = []
            for column in range(3):
                cell = self.methods_table.item(row, column)
                values.append(cell.text().strip() if cell else "")
            if values[0]:
                methods.append({
                    "name": values[0], "label": values[1],
                    "requires_perm": values[2],
                })
        self.interfaces[index]["methods"] = methods

    # ================================================================== 表格
    def on_add_dep(self) -> None:
        self._append_row(self.deps_table, "yezhongtao.oa.auth")

    def on_add_perm(self) -> None:
        self._append_row(self.perms_table, "view")

    def on_add_uses(self) -> None:
        self._append_row(self.uses_table, "yezhongtao.oa.notice.delete")

    def on_add_provide(self) -> None:
        self.interfaces.append({"key": "", "label": "", "methods": []})
        self._fill_provides()
        row = self.provides_table.rowCount() - 1
        if row >= 0:
            self.provides_table.selectRow(row)
            self.provides_table.editItem(self.provides_table.item(row, 0))
        self._schedule_preview()

    def on_add_method(self) -> None:
        index = self._current_interface_index()
        if index < 0:
            self._warn("请先选中（或添加）一个接口")
            return
        row = self.methods_table.rowCount()
        self.methods_table.insertRow(row)
        self.methods_table.setItem(row, 0, item("", mono=True))
        self.methods_table.setItem(row, 1, item(""))
        self.methods_table.setItem(row, 2, item("", mono=True))
        self.methods_table.setCurrentCell(row, 0)
        self.methods_table.editItem(self.methods_table.item(row, 0))
        self._schedule_preview()

    def on_remove_dep(self) -> None:
        self._remove_row(self.deps_table)

    def on_remove_perm(self) -> None:
        self._remove_row(self.perms_table)

    def on_remove_uses(self) -> None:
        self._remove_row(self.uses_table)

    def on_remove_provide(self) -> None:
        index = self._current_interface_index()
        if index < 0:
            self._warn("请先选中要删除的接口")
            return
        del self.interfaces[index]
        self._fill_provides()
        self._schedule_preview()

    def on_remove_method(self) -> None:
        index = self._current_interface_index()
        if index < 0:
            return
        rows = self.methods_table.selectionModel().selectedRows()
        if not rows:
            self._warn("请先选中要删除的方法")
            return
        self.methods_table.removeRow(rows[0].row())
        self._on_method_edited(None)
        self._schedule_preview()

    def _append_row(self, widget, placeholder: str = "") -> None:
        row = widget.rowCount()
        widget.insertRow(row)
        first = item("", mono=True)
        widget.setItem(row, 0, first)
        widget.setItem(row, 1, item(""))
        widget.setCurrentCell(row, 0)
        widget.editItem(first)
        if placeholder:
            first.setToolTip(f"例如 {placeholder}")
        self._schedule_preview()

    def _remove_row(self, widget) -> None:
        rows = widget.selectionModel().selectedRows()
        if not rows:
            self._warn("请先选中要删除的行")
            return
        widget.removeRow(rows[0].row())
        self._schedule_preview()

    # ================================================================== 校验
    def on_validate(self) -> None:
        manifest = core.build_manifest(self._collect_manifest())
        result = core.validate_manifest(manifest, self.dir or None)
        self._render_validate(result)
        if result["ok"]:
            self._log(f"✔ 声明校验通过：{result['full_id']}")
            for warning in result["warnings"]:
                self._log(f"  · 提示：{warning}")
            QMessageBox.information(
                self, "校验通过",
                f"声明格式正确：{result['full_id']}\n" +
                (f"\n{len(result['warnings'])} 条提示，详见运行日志。"
                 if result["warnings"] else ""),
            )
        else:
            self._log("✘ 声明校验未通过：")
            for error in result["errors"]:
                self._log(f"  · {error}")
            QMessageBox.warning(
                self, "校验未通过",
                "\n".join(result["errors"][:8]) +
                ("\n…" if len(result["errors"]) > 8 else ""),
            )

    def _render_validate(self, result: dict) -> None:
        errors = result.get("errors") or []
        warnings = result.get("warnings") or []
        if result.get("ok"):
            text = "<span style='color:#059669;font-weight:700'>✔ 校验通过</span>"
            if warnings:
                text += f" <span style='color:#b45309'>· {len(warnings)} 条提示</span>"
        else:
            text = (f"<span style='color:#dc2626;font-weight:700'>✘ {len(errors)} 个问题</span>"
                    f" <span style='color:#64748b'>· {errors[0]}</span>")
        self.validate_label.setText(text)

    def on_save_manifest(self) -> None:
        if not self._require_dir():
            return
        manifest = core.build_manifest(self._collect_manifest())
        result = core.validate_manifest(manifest, self.dir)
        self._render_validate(result)
        if not result["ok"]:
            QMessageBox.warning(self, "无法保存", "\n".join(result["errors"][:8]))
            return
        path = core.write_manifest(Path(self.dir), manifest)
        self._log(f"✔ 已写入 {path}")
        self.statusBar().showMessage("plugin.json 已保存", 5000)
        QMessageBox.information(self, "已保存", f"已写入：\n{path}")

    # ================================================================== 依赖
    def on_analyze(self) -> None:
        if not self._require_dir():
            return
        self._log("开始分析依赖…")
        self._run_task(core.analyze_dependencies, (self.dir,),
                       self._on_analysis_done, self.btn_analyze, "分析中…")

    def _on_analysis_done(self, data: dict) -> None:
        self.analysis = data
        modules = data.get("modules") or []

        self.analysis_table.blockSignals(True)
        self.analysis_table.setRowCount(0)
        pending = 0
        for row_data in modules:
            row = self.analysis_table.rowCount()
            self.analysis_table.insertRow(row)
            self.analysis_table.setItem(row, 0, item(row_data.get("module", ""), mono=True))
            self.analysis_table.setItem(
                row, 1, item(f"{row_data.get('file', '')}:{row_data.get('line', '')}"))
            self.analysis_table.setItem(row, 2, item(self._kind_label(row_data)))
            self.analysis_table.setItem(row, 3, item(row_data.get("dist", "") or "—", mono=True))
            self.analysis_table.setItem(row, 4, item(row_data.get("version", "") or "—", mono=True))
            state_item = item(self._state_label(row_data.get("state", "")))
            color = {
                "ready": "#059669", "copy": "#0284c7",
                "pip": "#d97706", "unknown": "#d97706",
            }.get(row_data.get("state", ""), "#64748b")
            state_item.setForeground(QColor(color))
            self.analysis_table.setItem(row, 5, state_item)
            if row_data.get("state") in ("copy", "pip", "unknown"):
                pending += 1

        if not modules:
            self.analysis_table.insertRow(0)
            self.analysis_table.setItem(0, 0, item("没有扫描到任何 import"))
            self.analysis_table.setSpan(0, 0, 1, 6)
        self.analysis_table.blockSignals(False)

        summary = data.get("summary") or {}
        self.dep_summary.setText(
            f"共 {summary.get('total', 0)} 个 import · 第三方 {summary.get('thirdparty', 0)} · "
            f"已在 libs {summary.get('ready', 0)} · 可复制 {summary.get('copy', 0)} · "
            f"需 pip {summary.get('pip', 0)} · 跳过 {summary.get('skip', 0)}"
        )
        self.btn_vendor.setEnabled(pending > 0 or summary.get("ready", 0) > 0)
        self._log(f"依赖分析完成：模块 {len(modules)} 个，待内置 {pending} 个")
        for row_data in modules:
            if row_data.get("state") in ("copy", "pip", "unknown"):
                self._log(
                    f"  · {row_data['module']}  [{row_data.get('dist', '?')} "
                    f"{row_data.get('version', '')}]  {self._state_label(row_data.get('state'))}"
                )
        self.tabs.setTabText(2, f"③ 依赖 ({pending})" if pending else "③ 依赖")
        self.statusBar().showMessage("依赖分析完成", 5000)

    def on_vendor(self) -> None:
        if not self._require_dir():
            return
        answer = QMessageBox.question(
            self, "一键内置依赖",
            "会把第三方依赖（含传递依赖）从当前 Python 环境复制到插件的 libs/ 目录，\n"
            "已有的会跳过；本机没装的包会尝试 pip 安装（需要网络）。\n\n继续吗？",
        )
        if answer != QMessageBox.Yes:
            return
        self._log("开始内置依赖…")
        self._run_task(
            core.vendor_dependencies,
            (self.dir, None, self.chk_metadata.isChecked()),
            self._on_vendor_done, self.btn_vendor, "内置中…",
        )

    def _on_vendor_done(self, data: dict) -> None:
        for result in data.get("results") or []:
            action = result.get("action")
            if action == "copy":
                self._log(
                    f"  + 复制 {result.get('dist', '')} {result.get('version', '')} "
                    f"({result.get('files', 0)} 个文件, "
                    f"{self._fmt_size(result.get('bytes', 0))})"
                )
            elif action == "pip":
                self._log(f"  + pip 安装：{', '.join(result.get('packages') or [])}")
            elif action == "skip":
                self._log(f"  - 跳过 {result.get('dist', result.get('module', ''))}"
                          f"（{result.get('reason', '')}）")
            else:
                self._log(f"  ! 失败 {result.get('dist', result.get('module', ''))}："
                          f"{result.get('error', '')}")

        if data.get("failed"):
            self._warn(f"{len(data['failed'])} 个依赖内置失败，详见日志")
        else:
            self._log(f"内置完成：libs/ 现在 {len(data.get('libs') or [])} 项，"
                      f"{self._fmt_size(data.get('libs_size', 0))}")
        if data.get("libs"):
            self._log("libs/：" + "、".join(data["libs"]))
        self._pending_analyze = True

    # ================================================================== 打包
    def on_pack(self) -> None:
        if not self._require_dir():
            return
        options = {
            "out_dir": self.out_edit.text().strip(),
            "include_data": self.chk_data.isChecked(),
            "write_manifest_file": self.chk_write.isChecked(),
            "auto_vendor": self.chk_auto.isChecked(),
            "include_metadata": self.chk_metadata.isChecked(),
        }
        manifest = self._collect_manifest()
        self._log("开始打包…")
        self.pack_hint.setText("正在校验 / 内置依赖 / 打 zip…")
        self._run_task(core.pack_plugin, (self.dir, manifest),
                       self._on_pack_done, self.btn_pack, "打包中…", **options)

    def _on_pack_done(self, data: dict) -> None:
        self.pack_hint.setText("会先校验声明，再内置依赖，最后打 zip")
        if not data.get("ok"):
            errors = data.get("errors") or ["未知错误"]
            self._log(f"✘ 打包失败（{data.get('stage', '')}）：")
            for error in errors:
                self._log("  · " + str(error))
            self.pack_result.setVisible(True)
            self.pack_title.setText("打包失败")
            self.pack_title.setStyleSheet("color:#dc2626; font-weight:700;")
            self.pack_path.setText("")
            self.pack_meta.setText("；".join(str(error) for error in errors[:4]))
            self.pack_notes.setText("")
            self.pack_files.setPlainText("")
            QMessageBox.warning(self, "打包失败",
                                "\n".join(str(error) for error in errors[:6]))
            return

        self.last_pack = data
        self.btn_open_out.setEnabled(True)
        self._render_pack_result(data)
        self._log(f"✔ 打包成功：{data['zip']}")
        self._log(f"  文件 {data['count']} 个，大小 {data['size_human']}，"
                  f"耗时 {data.get('duration_ms', 0)}ms")
        for warning in data.get("warnings") or []:
            self._log(f"  · 提示：{warning}")
        vendor = data.get("vendor") or {}
        if vendor.get("vendored"):
            names = [entry.get("dist") or entry.get("module")
                     for entry in vendor["vendored"]]
            self._log("  打包时内置：" + "、".join(str(name) for name in names))
        self._save_state()
        self.statusBar().showMessage(f"已生成 {Path(data['zip']).name}", 8000)
        QMessageBox.information(
            self, "打包成功",
            f"{data['plugin_id']}.zip\n\n"
            f"文件 {data['count']} 个 · {data['size_human']}\n"
            f"{data['zip']}\n\n"
            f"下一步：到「插件管理 → 安装插件」上传这个 zip。",
        )

    def _render_pack_result(self, data: dict) -> None:
        self.pack_result.setVisible(True)
        self.pack_title.setText(f"打包成功 · {data['plugin_id']}")
        self.pack_title.setStyleSheet("color:#059669; font-weight:700;")
        self.pack_path.setText(data["zip"])
        self.pack_meta.setText(
            f"文件 {data['count']} 个 · 大小 {data['size_human']} · "
            f"耗时 {data.get('duration_ms', 0)}ms · 输出目录 {data.get('out_dir', '')}"
        )
        notes = []
        for warning in data.get("warnings") or []:
            notes.append("提示：" + warning)
        vendor = data.get("vendor") or {}
        if vendor.get("vendored"):
            names = [entry.get("dist") or entry.get("module")
                     for entry in vendor["vendored"]]
            notes.append("本次内置依赖：" + "、".join(str(name) for name in names))
        self.pack_notes.setText("\n".join(notes))

        files = data.get("files") or []
        lines = [f"{entry['name']}    {self._fmt_size(entry['size'])}"
                 for entry in files[:400]]
        if len(files) > 400:
            lines.append(f"… 其余 {len(files) - 400} 个文件")
        self.pack_files.setPlainText("\n".join(lines))

    def on_copy_zip(self) -> None:
        if not self.last_pack:
            self._warn("还没有打包结果")
            return
        QApplication.clipboard().setText(self.last_pack["zip"])
        self.statusBar().showMessage("已复制 zip 路径", 4000)

    def on_open_out(self) -> None:
        target = ""
        if self.last_pack:
            target = self.last_pack.get("out_dir") or str(Path(self.last_pack["zip"]).parent)
        if not target:
            target = self.out_edit.text().strip()
        if not target:
            self._warn("还没有打包结果")
            return
        try:
            core.open_path(target)
        except Exception as exc:  # noqa: BLE001
            self._warn(str(exc))

    def on_pick_out(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.out_edit.text().strip() or str(Path.home()))
        if selected:
            self.out_edit.setText(str(Path(selected)))

    def on_clear_log(self) -> None:
        self.log_view.clear()

    # ================================================================== 任务
    def _run_task(self, fn, args: tuple, on_done, busy_button: QPushButton | None = None,
                  busy_text: str = "", **kwargs) -> None:
        if self.task is not None and self.task.isRunning():
            self._warn("上一个任务还没结束，请稍候")
            return

        self._busy_button = busy_button
        self._busy_text = busy_button.text() if busy_button else ""
        if busy_button:
            busy_button.setEnabled(False)
            busy_button.setText(busy_text or "处理中…")
        self.progress.setRange(0, 0)
        self.statusBar().showMessage(busy_text or "处理中…")

        task = Task(fn, *args, **kwargs)
        task.done.connect(on_done)
        task.failed.connect(self._on_task_failed)
        task.finished.connect(self._on_task_finished)
        self.task = task
        task.start()

    def _on_task_failed(self, message: str) -> None:
        self._log("✘ " + message)
        self.pack_hint.setText("操作失败，详见日志")
        QMessageBox.warning(self, "操作失败", message)

    def _on_task_finished(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        if self._busy_button is not None:
            self._busy_button.setEnabled(True)
            self._busy_button.setText(self._busy_text or "完成")
            self._busy_button = None
        self.statusBar().showMessage("就绪", 3000)
        if getattr(self, "_pending_analyze", False):
            self._pending_analyze = False
            QTimer.singleShot(0, self.on_analyze)

    # ================================================================== 杂项
    def _require_dir(self) -> bool:
        if (not self.dir or not Path(self.dir).is_dir()) and self.dir_edit.text().strip():
            self.on_scan()
        if not self.dir or not Path(self.dir).is_dir():
            self._warn("请先选择并扫描插件目录")
            return False
        return True

    def _warn(self, message: str) -> None:
        self._log("! " + message)
        self.statusBar().showMessage(message, 8000)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{core.now_text()}] {message}")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    @staticmethod
    def _fmt_size(size) -> str:
        size = float(size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}GB"

    @staticmethod
    def _kind_label(row: dict) -> str:
        return {
            "stdlib": "标准库", "kernel": "内核已提供", "local": "插件本地",
            "toolchain": "构建工具", "vendored": "已内置", "thirdparty": "第三方",
        }.get(row.get("kind", ""), row.get("kind", ""))

    @staticmethod
    def _state_label(state: str) -> str:
        return {
            "skip": "跳过", "ready": "已在 libs", "copy": "可复制",
            "pip": "需 pip 安装", "unknown": "无发行版信息",
        }.get(state, state)

    # ---------------------------------------------------------- 拖拽
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        if path.is_dir():
            self.dir_edit.setText(str(path))
            self.on_scan()
        else:
            self._warn("请拖入一个文件夹")

    def closeEvent(self, event) -> None:
        self._save_state()
        if self.task is not None and self.task.isRunning():
            answer = QMessageBox.question(
                self, "任务进行中",
                "还有任务（分析 / 内置依赖 / 打包）在运行，确定要退出吗？\n"
                "退出会中断当前操作。",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.task.wait(5000)
        event.accept()
