# 文件预览（yezhongtao.tools.preview）

通用在线文件预览插件：文件树 + 目录列表 + 预览面板，**只读**，不修改任何文件。

安装后在「插件管理」里点这一行的 **打开管理页** 进入 `/preview/`；
侧栏会出现「文件预览」「预览设置」两项（停用插件时自动清理）。

## 能力

| 类型 | 说明 |
|---|---|
| 代码 / 文本 | 语法高亮（关键字/字符串/注释/数字/标签）、行号、横向滚动、4000 行上限 |
| Excel（.xlsx） | 标准库解析 sharedStrings + worksheet，渲染可滚动表格 |
| CSV / TSV | 自动识别分隔符、去 BOM、表头 + 数据行 |
| Word（.docx） | 标题层级、段落、表格 |
| 图片 | png/jpg/gif/webp/bmp/svg/ico/avif，等比缩放 |
| 视频 | mp4/webm/ogg/mov/mkv…，`<video controls>`，支持拖动进度（Range） |
| 音频 | mp3/wav/flac/m4a/aac/opus…，`<audio controls>` |
| PDF | 内嵌 iframe |
| 其它 | 压缩包、旧版 .doc/.xls/.ppt/.rtf 等给出提示 + 下载按钮 |

## 页面

| 页面 | 路径 | 说明 |
|---|---|---|
| 文件预览 | `/preview/` | 目录树 + 当前目录 + 预览面板 |
| 打开路径 | `/preview/?path=data/logs` | 目录或文件 |
| 设置 | `/preview/settings` | 可浏览目录、文本/表格上限 |

接口：`/preview/api/tree`、`/preview/api/preview`、`/preview/raw/<路径>`、`/preview/download/<路径>`。

## 安全

- `Vault.resolve()` 只允许配置 `roots`（默认项目根）之内的路径，`..` 直接拒绝；
- 只读：不写、不改、不删；
- 文本默认上限 2MB、表格 500 行 × 40 列；
- 页面与 API 都要求 `yezhongtao.tools.preview.view` 权限；下载额外要 `download`。

## 给其它插件的接口

```json
{
  "dependencies": { "yezhongtao.tools.preview": "1.0.0" },
  "uses_permissions": { "yezhongtao.tools.preview.view": "读取预览信息" }
}
```

```python
preview = ctx.require("preview.api")
preview.list_dir("data/logs")
content = preview.preview("plugins/yezhongtao.erp.bridge/erpbridge_core.py")
preview.kind_of("a.xlsx")     # → "table"
```

## 目录

```
yezhongtao.tools.preview/
├── plugin.json
├── hooks.py                register(ctx)：蓝图 / 菜单 / 接口
├── preview_config.py       设置与校验
├── preview_files.py        路径安全 + 目录遍历 + 各格式解析
├── preview_highlight.py    轻量语法高亮
├── preview_views.py        页面 / API / raw
├── templates/yezhongtao.tools.preview/{preview,settings}.html
├── static/{preview.css,preview.js}
└── data/settings.json      运行时生成
```
