# 本地资料编辑

程序 0.3.0 增加网页与 MCP 编辑。规范来源仍是 YAML／Markdown，SQLite 是可重建的检索快照。保存只修改本地文件，不运行 Git、不自动推送；整理完成后可手动执行 `git diff`、校验和提交推送。

## 启动与网页操作

```powershell
python -m pip install -e ".[web,mcp,export,edit]"
python -m marx_lexicon build
python -m marx_lexicon serve --with-mcp --edit
# 或直接运行默认开启编辑的快速脚本
.\start-web.ps1 -WithMcp
# 关闭所有写入入口
.\start-web.ps1 -WithMcp -ReadOnly
```

打开 `/manage` 或从词条详情进入编辑。类别包括词条、概念、译法、证据、作品、版本、项目方案和 Markdown 文章。表单展示嵌套义项、范围、引文、术语标注和对应组；数组增加或移除后会整体保存。文章可以预览，原始 HTML 禁用。

单个资源可直接保存；涉及相互引用的新增资料先逐一“加入批次”，然后一起校验、保存。当前页可勾选多个资源，批量修改一个字段或加入删除批次。删除引用检查会列出资源及字段；不会级联删除义项、译法、证据、项目选择或文章，须在同一批次显式修复最终引用图。正式批次全部通过后才写入，任一错误均不部分发布。

不完整资料使用“保存为编辑草稿”。在“编辑草稿”目录打开后，可从批次的“编辑”按钮补全每个资源，重新加入批次并保存草稿，再发布。草稿可修改、删除；草稿保存后其他资料变化时，必须重读、修订并重存才能发布，避免覆盖较新的资料。独立编辑草稿不出现在查阅、翻译参考或正式导出中。正式词条的学术 `draft` 工作稿状态与未发布编辑草稿不同，原有工作稿仍可查询。

## 版本、状态与保存

- `data/editor-state.yaml` 的 `revision` 每次本地保存递增；写入须携带所读取的 `expected_revision`。冲突返回 409，重新读取后再处理。
- 正式批次另行递增 `data/dataset.yaml` 的补丁版本，并更新全部 profile 的 `dataset_version`。草稿保存、删除不改变正式数据版本。
- ID 稳定不变，同名资源不合并。`update` 仅修改给出的字段，未给出的字段保留；数组整体替换。删除不能留下悬空引用。
- 导入和普通编辑不自动晋升学术审阅。变更的义项／译法／关系回到 `draft`，变更的证据回到 `unreviewed`、对应回到 `candidate`；明确的 `review` 操作才标为已审阅。
- 术语标注删除或重排时，未显式修改的对应组按原标注身份迁移索引；引用的标注被删除则拒绝保存，须同时修改对应组。位置使用 Python Unicode 码点索引。
- 跨进程锁串行化保存。当前事务日志只保存本次涉及文件的旧内容，成功后删除；失败回滚，进程中断后下次编辑访问自动恢复。没有逐次历史备份。手工直接修改源文件时应停止编辑服务，完成后构建并重启。

## 导入

选择文件或粘贴正文，先预览，再选择加入编辑批次、导入编辑草稿或正式发布。默认 `create` 只新增；`update` 只更新已存在 ID；`upsert` 按 ID 新增或更新。文件未包含的记录保持原样。缺失 ID 会分配并保留在预览操作或草稿中；若直接重新解析无 ID 的文件会重新分配，需使用预览形成的批次或保存的草稿继续操作。

| 格式 | 映射与限制 |
| --- | --- |
| JSON / YAML | 支持单资源、资源列表、按类别的数据包，以及全库 JSON 的 Dataset＋articles。清单版本不回退，正式发布按当前本库版本递增。 |
| CSV | 原生全库表按 entry_id 合并义项，原生项目表按有方向译法导入并保留项目选择及其范围。多值字段为 JSON 单元格。基本词条表可映射列名，格式为 `{"lemma":"原词列","language":"语言列"}`。没有完整证据或概念时先存草稿。 |
| TBX | 支持 v3 TBX-Basic DCA；概念、语言、词条义项分别恢复。本库扩展 note/admin 可恢复 ID、条件和已登记证据；外部自由文本来源仅保留为导入笔记，不伪造作品、版本或引文。多语言术语同组不生成译法或词语对应，preferredTerm 不转成学术已审阅。文章不在 TBX 内，不能据此完整恢复。 |

预览默认返回资源 ID、计数、字段错误和引用错误；`include_operations=true` 按需返回完整操作，网页用它继续编辑批次。MCP 默认不重复返回导入文件中的引文和全文。语义不完整可存草稿，无法解析的 JSON／CSV／XML 或重复 ID 须先修复文件。仅全库 JSON 是完整语义快照；CSV、TBX 不保证独立恢复所有资料，信息保留说明见[导出约定](exports.md)。

导入说明先随草稿保存，正式发布后保留在 `data/import-notes/`；网页“导入注记”目录可只读查阅，MCP 可用 `read_editable_records(kind="import_notes")` 读取。自由文本来源注记不会升级为正式证据，这些编辑过程材料也不属于 Dataset 导出。

## HTTP 与 MCP

写入接口只在 `--edit` 注册；同一应用内的 HTTP MCP 使用同一编辑模式。独立 MCP 使用 `python -m marx_lexicon mcp --edit` 或增加 `--transport streamable-http`。编辑模式仅允许回环监听；独立 SQLite 数据库缺少规范源文件时保持只读。

| HTTP | 作用 |
| --- | --- |
| GET /api/editor/schema?kind=entries | 按需读取一种模型；省略 kind 返回 ChangeBatch 模型 |
| GET /api/editor/records?kind=entries | 读取可编辑原始记录、revision；可重复 identifiers 参数、offset、limit（1—100）；kind=drafts 读取草稿 |
| GET /api/editor/references/{kind}/{id} | 删除前列出包括嵌套义项在内的入站引用 |
| POST /api/editor/preview | ChangeBatch 校验与引用报告 |
| POST /api/editor/apply | ChangeBatch 正式保存 |
| POST /api/editor/import | ImportRequest，destination=preview／draft／publish |
| POST /api/editor/drafts | DraftRequest，带 id 修改，不带 id 新增 |
| POST /api/editor/drafts/{id}/publish | expected_revision，重新校验发布 |
| DELETE /api/editor/drafts/{id} | expected_revision，删除草稿 |
| POST /api/editor/markdown | content，返回禁用 HTML 的预览 |

MCP 在原有八个只读工具外注册：`get_editing_schema`、`read_editable_records`、`preview_changes`、`apply_changes`、`import_records`、`save_draft`、`publish_draft`、`delete_draft`。模型不塞进每个工具描述；先按需读取模型。写入工具标为可写，可能删除资源的工具标为 destructive。每次返回一份 JSON 文本，保存结果只含版本、ID、计数及本地保存状态，不回传全库引文。

例如先 `read_editable_records(kind="works", identifiers=["work-capital-1"])`，再把读取的 revision 传给 `apply_changes` 的 `batch`：

```json
{
  "expected_revision": 0,
  "operations": [
    {"action": "update", "kind": "works", "id": "work-capital-1", "values": {"note": "新的作品说明"}}
  ],
  "message": "修订作品说明"
}
```

`save_draft` 使用 `request={expected_revision,id?,title,operations,notes}`；`import_records` 使用 `request={expected_revision,format,content,kind?,mode?,columns?,destination?,title?,include_operations?}`。未知 ID 返回明确错误；非法批次的 MCP 错误中包含 report；陈旧版本不自动覆盖。客户端示例见 [mcp-edit-stdio.json](../examples/mcp-edit-stdio.json)，替换为实际解释器路径即可。
