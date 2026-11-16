# 导出格式与信息边界

所有入口共用一个导出模块，从 SQLite 快照读取，不在导出时改动数据。产物建议放入被忽略的 build/。

```powershell
python -m marx_lexicon export-dataset --format json --output build/dataset.json
python -m marx_lexicon export-dataset --format markdown --output build/lexicon.md
python -m marx_lexicon export-dataset --format csv --output build/senses.csv
python -m marx_lexicon export-dataset --format tbx --output build/lexicon.tbx
python -m marx_lexicon export-dataset --format skos --output build/concepts.ttl
python -m marx_lexicon export-glossary --profile demo-de-en --format csv --output build/demo-de-en.csv
python -m marx_lexicon export-glossary --profile demo-de-zh --format tbx --output build/demo-de-zh.tbx
```

全库支持五种格式，项目表支持 JSON／Markdown／CSV／TBX。项目表保留原来的 profile、rows 和证据字段，增补原／目标义项、用法、状态及项目选择条件。输出该语言方向的全部备选，不只输出选择项；selected_by_profile 表示方案有登记，不表示已在某段输入中满足条件。

## 报告与快照

指定 --output 时同时写入 `<输出文件名>.report.json`，包含版本、格式、范围、行数、概念数、字段映射、complete_snapshot 和信息损失。没有输出路径时，数据写标准输出，报告写标准错误；网页分别提供文件和报告下载。

只有全库 JSON 的 complete_snapshot=true。dataset 可用 Dataset.model_validate 再校验，articles 保存数据库中的全部文章内容。这是完整语义快照，不是保留 YAML 注释、格式和编辑目录清单的 Git 仓库备份。

| 格式 | 组织方式与边界 |
| --- | --- |
| JSON | 全库为版本 + Dataset + articles；项目为既有 profile/rows/evidence 结构，项目表不是全库快照 |
| Markdown | 全库逐词条、项目逐译法阅读，附范围、状态与证据；不能重建全部结构 |
| CSV | 全库每行一个义项；项目每行一条有方向译法；列表／对象为 JSON 单元格，默认 UTF-8 BOM |
| TBX | TBX v3、TBX-Basic DCA；概念、语言、义项分别映射 conceptEntry、langSec、termSec |
| SKOS | Turtle 概念图，不替代词条、译法、项目、完整引文或文章 |

CSV 项目表分别保存译法 scope、profile_choices 和方案总体 profile.scope，不能只看一个选择布尔值。全库 CSV 不含完整译法关系、证据和文章；项目 CSV 的完整证据仍按 ID 从原库或 JSON 读取。

## TBX

同译名的不同概念不合并，每个词条义项独立生成 termSec，以义项 ID 为身份。标准字段不能执行本库全部有方向译法条件；定义、用法、词形、原词 ID、审阅状态、条件和证据通过合法 note/admin 表达。项目选择不会自动成为 preferredTerm-admn-sts。CAT 软件可能忽略这些说明，不能据同组术语无条件自动替换。长篇文章不进入 TBX。

离线测试固定使用官方 Core RNG、TBX-Basic DCA Schematron 和 ISO Schematron XSLT 2.0，后者通过 SaxonC 执行。资源及固定来源见 [校验资源](../tests/standards/README.md)。这验证交换格式，不代表学术审定。来源：[TBX 开发者资源](https://www.tbxinfo.net/developer-resources/?id=2)。

## SKOS

每语言只使用概念登记的一个 prefLabel。已审阅的 broader／narrower／related 映射对应 SKOS 属性；所有关系另有项目记录保留 kind、target、status、解释和证据。草稿、distinct 和 possible_equivalent 不升级为确定标准关系或 exactMatch。证据节点带审阅状态及原始出处链接。

默认标识前缀为 `https://github.com/Blious/marx-lexicon/id/`，用于标识，不表示该路径已部署读取服务。可用 --base-uri 指定以 / 或 # 结尾的 HTTP(S) 前缀，重复发布应保持一致。依据：[W3C SKOS Reference](https://www.w3.org/TR/skos-reference/)。
