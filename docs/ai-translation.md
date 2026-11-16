# AI 翻译参考的输出约定

目标是用较少上下文传递翻译判断所需的信息。默认参考包以结构去重和按需读取节省空间，定义、用法、限制和辨析不经模型概括，也不按字数截断。

术语主条目只组织查阅与编辑。翻译仍根据独立词条、义项、译法和证据判断，缺少 Term 归属的正式资料同样参与。`translation-brief/1` 中的 `terms` 指识别结果，不是模型 2 的 Term 主条目集合；归组不改变候选、范围或审阅状态。

## 默认视图与调用方式

`Lexicon.prepare_translation(..., detail="compact")` 返回 `format=translation-brief/1`。省略 detail 也使用此结构。命令行 JSON 默认精简并去除格式化空白；Markdown 默认完整，方便人工查阅。`detail="full"` 或 `--detail full` 可取得逐次命中的完整结构。

按段落调用，提供待译原文、源语言、目标语言、项目方案，以及已知作品和原文版本。原文由调用者保留，精简包不再回传整段文字；位置仍基于原始输入的 Unicode 字符 `[start,end)`，`context.input_characters` 保存字符数。

一次请求返回全部已识别术语和适用候选，不使用 Top-K 丢弃低排名术语。同一词条、同一输入条件的候选只列一次，各实际命中形式及其全部位置存入 matches。未识别的词形和未登记的意义仍可能存在，不能由未命中推导其不存在。

## 信息保留规则

| 内容 | 精简包中的表达 |
| --- | --- |
| 原词与位置 | terms 的稳定 entry_id、lemma、matches.surface 和全部 spans |
| 义项与边界 | 全部适用 candidates；定义、用法、作品范围、状态和证据 ID |
| 译法与备选 | 目标语言的全部适用译法、目标义项 ID、译法说明、状态与适用性 |
| 项目决定 | profile 总体条件及说明；各译法的 profile_choices 保留匹配、待补语境或不匹配的选择条件 |
| 无法确定之处 | recommendation=null；unresolved 说明多义、缺少条件或方案选择等原因 |
| 区分与联系 | 命中概念的一层直接 concept_relations，带解释、依据、目标名称与状态 |
| 实际译本用词 | evidence.passages.terms.form，与目标词条显示名分开 |
| 出处与可靠性 | 实际版本、章节／书页／稿页、定位说明、材料类型、发言主体、审阅及对应状态 |
| 引文与书目全文 | 按 read_more 的证据 ID、语言和数据版本继续读取 |

例如目标词条为 abstract labour，语例实际为 human labour in the abstract，两种表达和有关说明都会保留。原词并列或同组出现不表示它们在全部语境中同义。候选对应与 reviewed 对应也不会因精简而改变状态。

编辑时，把影响译法的辨析写入 Sense.usage、Translation.note 或有依据的概念关系，不要只埋在长篇文章中。例如劳动力与劳动的实际运用、Aufheben 与 Aufhebung 的词条区别，已进入首批义项的具体用法说明。长篇文章仍可通过 read-entry 按需查阅。

notes 与 scopes 是当前响应内的共享表。note_ref、usage_ref、scope_ref 等短引用仅用于本包去重，不能跨请求缓存为稳定身份；词条、义项、译法和证据 ID 才是数据身份。说明原文即使较长也完整保留，不按 token 预算裁剪。

每个译法的目标义项具有独立含义。与源义项相同的 definition、usage、concept_id、work_ids、evidence_ids、status 不重复传输；缺省时继承所在源义项字段。不同之处分别放在 target_definition、target_usage_ref、target_concept_id、target_work_ids、target_evidence_ids、target_status 中。这里的 status 继承源义项状态，不继承译法状态。

recommendation 保存某条译法的 ID；译文从该译法的 text 读取。即使某个候选 selected_by_profile=true，多义尚未消除时也不会产生确定推荐。profile_choices 的适用性描述项目选择条件，译法本身的 applicability 描述该译法范围，两者应分别检查。

## 证据展开与使用顺序

1. 解析 notes、scopes 及全部候选，结合调用者持有的原文判断意义。
2. recommendation 有值时，仍保留它的范围、译法差异和整理状态；它是项目建议，不代表学术审定。
3. 遇到多义、版本差异、对应候选或需要检验引文时，按 read_more 调用 read_evidence。使用原包的 dataset_version，数据版本变化时重新准备参考包。
4. 结合取得的完整上下文决定译法；显式选择的义项可用 sense_id 提供给下一次请求。

```powershell
python -m marx_lexicon read-evidence att-abstract-labour --language de --language en --dataset-version 0.2.0
```

`read_evidence(ids, languages=None, dataset_version=None)` 返回完整引文、各段定位、词语位置、对应关系、版本书目和作品信息。languages 省略时读取所有语言；按语言读取时，对应关系同步投影，原术语标注索引保持不变。筛选后少于两段的关系不作为跨段对应返回。

默认包的 alignment_summaries 是对应层级、状态和说明的摘要，不能从实际用词的并列推导逐词等价。若已登记词语级对应，还会保留 term_alignments 的具体标注引用及说明，相关段落保留 id 和 mention_index；完整引文位置可通过 read_evidence 展开。

## 节省范围与检查

精简包合并重复术语、重复说明和重复范围，删除回传输入、无关目标义项和第三语言，并将完整引文及书目改为按需读取。已有状态、差异说明、限制条件和候选集合不会因节省上下文被丢弃。

`python -X utf8 examples/compare_reference_size.py` 比较两种模式在去掉格式化空白后的 JSON 字符数，并同时展示重复输入的效果。字符数用于可复现的输出体积比较，不能直接视为特定模型的 token 数；确切成本需按调用模型的 tokenizer 测量。

测试对照精简／完整输出的义项、候选、推荐、状态、说明与范围，并覆盖重复命中、未满足条件、目标义项自身差异、词语对应投影，以及按版本继续读取证据。数据本身的学术正确性仍依赖文献整理与审阅。
