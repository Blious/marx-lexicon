# 完整示例的使用

`economics.de.txt` 与 `philosophy.de.txt` 是用于检索演示的编辑输入，组合已有术语，**不是马克思原句**。有出处的短引文存放在 `data/attestations/`，两者用途分开。

先按根目录 README 安装、校验并构建，再运行其中的 `prepare-translation` 命令。

## 经济学

输入包含 `Arbeitskraft`、`abstrakte Arbeit` 和 `Wert`。指定 `edition-capital-de-mew23` 后，德译中方案给出劳动力、抽象劳动、价值；德译英方案给出 labour-power、abstract labour、value。

`capacity for labour` 仍作为英语备选返回，带有同一短语例的依据。书目与章节位置分别属于各译本，英文劳动力定义位于第六章，不沿用德中第四章第三节的章号。

## 哲学

输入并列 `Entfremdung`、`Entäußerung`、`Aufhebung` 和 `Aufheben`。德译中示例分别采用异化、外化，并保留 Aufhebung 的两个语境义项。附属词条 Aufheben 对应另行登记的名词化不定式语例。

对于 Aufhebung，德译英参考包保留宗教语境的 annulment 和私有财产语境的 transcendence。仅凭词面不选其中一个，`recommendation` 为 null，`unresolved` 提示补充义项。

如当前实际语境确为所列私有财产义项，可在原命令增加：

```text
--sense sense-de-aufhebung-aufhebung-property
```

此时可选用 transcendence；它是示例所据 Milligan 译本的实际用词，不代表其他英语传统必须沿用。`Aufheben` 的 superseding 另列，不能混作词形。

## AI 精简输出

JSON 默认按词条合并重复命中，保留全部跨度与候选。说明通过 `note_ref`、范围通过 `scope_ref` 引用本包的共享表；这些短引用仅在当前响应内有效。目标义项与源义项相同的字段不重复传输，不同的字段用 `target_` 前缀保留。

精简结果中的 `evidence` 包含当前语言方向的实际用词、出处定位和对应状态。完整短引文、网页链接、逐词对应和书目可按 `read_more` 提供的参数读取：

```powershell
python -m marx_lexicon read-evidence att-alienation-pair --language de --language en --dataset-version 0.1.1
```

`--detail full` 输出完整视图；两者的推荐结果和限制条件一致。`compare_reference_size.py` 比较两组示例及重复输入的 JSON 字符数，不将字符数冒充特定模型的 token 数。

## 人工阅读与数据编辑

`read-entry ... --format markdown --output build/词条.md` 可导出包含文章与出处的词条。`export-glossary` 输出方案的术语表和全部相关语例。查询数据来自已构建数据库，修改 YAML 后要再次 build。

所有样例关系和解释都是待审阅工作稿。增加例证时，先登记具体版本，再标注短引文及词语位置；同语种其他译法也应独立登记其来源。
