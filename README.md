# 马克思多语术语库

以原文证据为基础的马克思术语库，面向 AI 翻译调用和人工查阅。以术语为单位汇总多语言表达、概念义项、有方向的译法和版本证据；底层资料分别保存稳定 ID 和引用，允许多种有条件的解释与译法并存。

程序 **0.4.0**、模型 **2**、数据 **0.2.0**：七个术语示例，共 22 个语言词条、8 个概念、7 组三语短语例及 17 条译法关系。术语为价值、劳动力、抽象劳动、异化、外化、`Aufhebung`、`Aufheben`。`Aufhebung` 汇总宗教与私有财产两个语境，`Aufheben` 独立关联；中文“扬弃”的共享词条 ID 保持不变。语例与解释仍是待学术审阅的工作稿，覆盖范围限于列出的作品、版本和上下文。

## 安装与构建

需要 Python 3.11 或更高版本，以及包含 FTS5 的 SQLite。以下命令在仓库根目录运行：

```powershell
python -m pip install -e ".[dev]"
python -m marx_lexicon validate
python -m marx_lexicon build
```

运行与测试均可在安装依赖后离线完成。默认数据库为 `build/lexicon.sqlite`；从其他目录调用时，在子命令前使用 `--root <仓库目录>`，或者对查询命令使用 `--database <数据库文件>`。

## 查阅术语

网页首页按术语展示多语言卡片。检索任一语言、译词或变体后，先归组去重再分页；详情在一个页面展示全部义项、语言表达、译法条件、项目选择、文章与版本证据。MCP 使用 `list_terms`、`read_term`；精简视图保留全部义项与条件，`detail="full"` 展开引文和文章。以下 CLI 用于独立语言词条及翻译参考：

```powershell
python -m marx_lexicon lookup 劳动力 --language zh-Hans --format markdown
python -m marx_lexicon lookup Entäusserung --language de
python -m marx_lexicon lookup alienation --language en
python -m marx_lexicon read-entry entry-de-aufhebung --format markdown
python -m marx_lexicon read-entry entry-zh-Hans-yangqi --format markdown --output build/扬弃.md
```

查询先检索原词与已登记词形，再查说明和语例。完整词条包含义项、译法、辨析文章、短引文、版本信息和出处链接。中文简体使用语言标签 `zh-Hans`。

## 为翻译整理参考

```powershell
python -m marx_lexicon prepare-translation --text-file examples/economics.de.txt --source-language de --target-language zh-Hans --profile demo-de-zh --edition edition-capital-de-mew23 --format markdown --output build/economics.zh.md
python -m marx_lexicon prepare-translation --text-file examples/economics.de.txt --source-language de --target-language en --profile demo-de-en --edition edition-capital-de-mew23 --output build/economics.en.json
python -m marx_lexicon prepare-translation --text-file examples/philosophy.de.txt --source-language de --target-language en --profile demo-de-en --edition edition-manuscripts-de-mew --format markdown --output build/philosophy.en.md
```

经济学示例可得到 `value`、`labour-power` 和 `abstract labour` 等方案选择；哲学示例中的 `Aufhebung` 会保留两个语境候选。明确上下文后，可用 `--sense sense-de-aufhebung-aufhebung-property` 指定其中一个义项，其他术语仍正常处理。

参考包包括输入中的位置、候选义项、方案选择、替代译法及证据。它不生成整段译文，也不通过关键词猜定多义词意义。省略版本或作品条件时，范围未确定的译法保留为候选，并提示补充语境。

面向 AI 的默认 JSON 使用 `translation-brief/1` 精简视图：同一术语只列一次，命中位置合并，重复说明与条件分别放入 `notes` 和 `scopes` 共享表；不回传整段输入，不夹带第三种语言。候选义项、译法差异、使用限制、实际译本用词和审阅状态都予以保留。`recommendation` 是译法 ID，null 表示仍需判断。

完整引文和书目信息可按证据 ID、语言和数据版本继续读取：

```powershell
python -m marx_lexicon read-evidence att-abstract-labour --language de --language en --dataset-version 0.2.0
python -m marx_lexicon read-evidence att-alienation-pair --format markdown
```

需要原来的完整参考结构时，在 `prepare-translation` 命令增加 `--detail full`。JSON 默认精简并去除格式化空白，Markdown 默认完整；可以显式使用 `--detail compact --format markdown` 获取较短的人工视图。Python API 的 `prepare_translation` 默认也为精简结构。具体字段与调用约定见 [AI 翻译参考](docs/ai-translation.md)。

```powershell
python -m marx_lexicon export-glossary --profile demo-de-zh --format markdown --output build/glossary.zh.md
python -m marx_lexicon export-glossary --profile demo-de-en --output build/glossary.en.json
python -m marx_lexicon export-schema --output schemas/dataset.schema.json
python -m pytest
python -X utf8 examples/compare_reference_size.py
```

## 本地网页、MCP 与标准导出

Windows 下可在 PowerShell 中直接运行快速启动脚本：

```powershell
.\start-web.ps1
# 同时开启 MCP，或指定端口且不自动打开浏览器
.\start-web.ps1 -WithMcp
.\start-web.ps1 -Port 8080 -NoBrowser
# 仅查阅，关闭网页和 MCP 编辑入口
.\start-web.ps1 -ReadOnly
```

脚本自动定位仓库，优先使用 `.venv` 的 Python，否则使用 PATH 中的 `python`；缺少依赖时安装网页、导出和编辑依赖，启动前重建数据库，服务就绪后打开浏览器。默认启用本地资料管理；`-ReadOnly` 关闭编辑，`-WithMcp` 的 MCP 与网页使用相同读写模式。按 `Ctrl+C` 停止。可用 `-Python <解释器路径>` 指定环境，`-Install` 按项目声明重新安装依赖。首次使用需要 Python 3.11+，安装缺少的依赖时需要联网。

如果系统执行策略阻止脚本，可仅对本次进程使用 `powershell -ExecutionPolicy Bypass -File .\start-web.ps1`。手动安装与启动方式如下：

```powershell
python -m pip install -e ".[web,mcp,export,edit]"
python -m marx_lexicon serve --with-mcp
# 手动启动命令默认只读，增加 --edit 开启编辑
python -m marx_lexicon serve --with-mcp --edit
```

网页为 `http://127.0.0.1:8000/`，MCP 为 `http://127.0.0.1:8000/mcp`。网页提供检索筛选、义项辨析、版本对照、段落术语参考与下载；不生成整段译文。省略 `--with-mcp` 可只运行网页。

```powershell
python -m marx_lexicon mcp --transport stdio
python -m marx_lexicon mcp --transport streamable-http --port 8001
python -m marx_lexicon export-dataset --format json --output build/dataset.json
python -m marx_lexicon export-dataset --format tbx --output build/lexicon.tbx
python -m marx_lexicon export-dataset --format skos --output build/concepts.ttl
python -m marx_lexicon export-glossary --profile demo-de-en --format csv --output build/demo-de-en.csv
```

全库支持 JSON、Markdown、CSV、TBX-Basic DCA 和 SKOS Turtle；项目表支持前四种。文件旁同时保存 `.report.json`，说明映射与信息损失；仅全库 JSON 是含全部文章的完整语义快照。详见[网页／HTTP／MCP 使用说明](docs/interfaces.md)和[导出约定](docs/exports.md)。

`/manage` 以术语为单位集中编辑多语言、义项、译法、证据、来源和文章，一次校验保存。新建自动分配 ID，通过选择器维护引用；共享资料显示受影响术语。可从同一表单恢复编辑草稿、下载 JSON 术语包，或为待归组概念建立主条目。高级分类与批次管理位于 `/manage/advanced`，项目全局配置也在此维护。

支持 JSON／YAML 单个或批量术语包、分类数据，以及 CSV／TBX 导入。未明确归组的新概念进入待归组；删除主条目仅解除归组，底层删除须明确提交并通过引用检查。所有资料保存仅写入本地，不自动提交或推送 Git。操作与 MCP 写入说明见[本地编辑指南](docs/editing.md)。

运行全部测试前安装 `python -m pip install -e ".[web,mcp,export,edit,dev]"`。标准校验资源已固定，安装依赖后测试无需在线读取来源。

## 目录与维护

| 目录 | 用途 |
| --- | --- |
| `data/terms/` | 术语主条目、概述、有序概念义项和文章引用 |
| `data/entries/` | 各语言词条、词形和义项 |
| `data/concepts/` | 概念说明和有依据的概念关系 |
| `data/translations/` | 义项之间的译法及适用范围 |
| `data/sources/` | 作品和具体出版／数字文本版本 |
| `data/attestations/` | 短语例、术语位置和对应候选 |
| `data/profiles/` | 固定数据版本的项目翻译方案 |
| `data/drafts/` | 未发布的独立编辑草稿，首次保存时建立 |
| `articles/` | 中文辨析文章 |
| `schemas/` | 由 Python 模型导出的 JSON Schema |
| `src/marx_lexicon/` | 模型、构建、查询和命令行 |
| `examples/` | 待译输入及操作说明 |
| `tests/` | 数据和运行行为测试 |
| `build/` | 本地数据库与导出文件，Git 忽略 |

添加词条时先在 YAML 中登记作品、版本、义项、短语例和译法，必要时补充辨析文章，再运行 `validate` 和 `build`。ID 在改名时保持不变；扩大数据或调整模型时同时维护数据版本和相关翻译方案。

每次完成更改后都必须提交并推送到远端，具体流程见[本地开发文档](docs/development.md)。

详见[架构方案](docs/architecture.md)、[数据模型与编辑约定](docs/data-model.md)、[AI 翻译参考](docs/ai-translation.md)和[示例说明](examples/README.md)。模型 2 直接重建数据库，不提供旧数据库或旧模型兼容层；从旧工作区更新后重新运行 `build`。归组不改变底层语义、翻译候选或学术审阅状态。
