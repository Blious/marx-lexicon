# 网页与 MCP

## 启动

Windows 快速启动：`./start-web.ps1`，同时运行 MCP 时使用 `./start-web.ps1 -WithMcp`。脚本可从任意工作目录调用，会检查依赖、重建当前仓库数据库，并在服务就绪后打开浏览器；服务在当前窗口运行，按 Ctrl+C 停止。参数：`-Port 8080`、`-NoBrowser`、`-Python <解释器路径>`、`-Install`（重新安装声明的依赖）。优先选择仓库 `.venv`，否则使用 PATH 中的 Python。首次缺少依赖时会执行 pip 安装；后续直接启动。此构建行为属于启动脚本，CLI 的只读 serve/MCP 命令仍不会隐式构建数据。

以下为手动安装和启动方式：

```powershell
python -m pip install -e ".[web,mcp,export]"
python -m marx_lexicon build
python -m marx_lexicon serve --with-mcp
```

网页：`http://127.0.0.1:8000/`；Streamable HTTP MCP：`http://127.0.0.1:8000/mcp`。无需 MCP 时省略 `--with-mcp`。网页包括检索与筛选、义项、辨析、作品与版本、证据对照、段落参考和导出；所有 CSS、JavaScript 和字体均使用本地资源，不依赖 Node 或 CDN。

仅运行 MCP：

```powershell
python -m marx_lexicon mcp --transport stdio
python -m marx_lexicon mcp --transport streamable-http --port 8001
```

独立 HTTP MCP 位于 `http://127.0.0.1:8001/mcp`。服务默认仅监听本机，`--host`、`--port` 可指定地址。本轮没有在线部署、账户、访问控制或在线编辑。示例见 [stdio 配置](../examples/mcp-stdio.json) 与 [HTTP 配置](../examples/mcp-http.json)，按客户端格式填写；stdio 的 command 建议使用安装本项目的 Python 解释器绝对路径。不会自动修改客户端配置。

从其他目录启动，把 `--root E:/Github/marx-lexicon` 放在子命令前，或用 `--database <绝对路径>` 查询独立快照。数据库缺失时提示构建，不自动修改数据。编辑仍使用 YAML／Markdown，`build` 后各请求读取当前数据库；请求结束即关闭连接，不跨工作线程共享连接。

## MCP 工具

| 工具 | 参数与作用 |
| --- | --- |
| get_metadata | 版本、覆盖、统计和语言、作品、版本、概念名称、项目方案目录 |
| lookup | query，可选 language、limit；精确词形优先 |
| list_entries | 可选 query、language、work_id、concept_id；offset=0、limit=20，最大 100 |
| read_entry | entry_id；完整词条、文章、译法和证据 |
| read_concept | concept_id；概念、词条和关系依据 |
| read_evidence | identifiers 列表，可选 languages、dataset_version |
| prepare_translation | text、source_language、target_language、profile_id；可选 work_id、edition_id、sense_id、detail |
| export_glossary | profile_id；按需读取项目术语表和证据 |

全部工具只读，不访问外部网络。每次输出一份无缩进 JSON 文本，不在 structured_content 再复制。元数据不夹带全库正文。段落参考默认沿用 [translation-brief/1](ai-translation.md)，完整引文按需展开；`detail=full` 显式取得完整参考。批量交换文件使用 CLI 或网页下载。

先调用 get_metadata 确认范围和方案；再按段调用 prepare_translation；需核对时按原包的证据 ID、语言及数据版本调用 read_evidence。候选不等于审定，未命中不表示不存在术语。分页只用于交互检索，翻译参考不按 Top-K 丢弃适用候选。

## HTTP

| 方法与路径 | 内容 |
| --- | --- |
| GET /api/metadata | 同 get_metadata |
| GET /api/entries | 同 list_entries；返回 results、total、offset、limit 与版本 |
| GET /api/entries/{id} | 同 read_entry |
| GET /api/concepts/{id} | 同 read_concept |
| POST /api/evidence | JSON：identifiers、可选 languages、dataset_version |
| POST /api/translation-reference | JSON 参数同 prepare_translation；默认 JSON，?format=markdown 返回人工视图 |
| GET /api/export | format、可选 profile_id、base_uri；report=true 下载报告 |

未知 ID 返回 404，非法参数或条件返回 422，证据版本变化返回 409，JSON 错误保存为 detail。版本变化后重新整理翻译参考。原文首尾空白保留，位置为原始输入 Unicode 码点 `[start,end)`；前端使用 Array.from(text) 而非 UTF-16 下标。证据页保留全部原标注索引，重叠位置仅合并高亮。

Markdown 禁用原始 HTML；文章中仓库相对链接映射到对应 GitHub 文件。HTTP MCP 由顶层 ASGI 应用管理生命周期，支持现代协议及 SDK 提供的旧版初始化握手，无需旧 SSE 专用端点。

依据：[官方 SDK](https://github.com/modelcontextprotocol/python-sdk)、[ASGI 生命周期](https://py.sdk.modelcontextprotocol.io/run/asgi/)、[旧版客户端](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/)。
