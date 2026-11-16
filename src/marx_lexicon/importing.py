"""交换文件转为显式编辑操作；不能恢复的资料随草稿说明保存。"""

import csv
import io
import json
from uuid import uuid4
from xml.etree import ElementTree as ET

import yaml

from .catalog import CATEGORIES
from .edit_models import Operation
from .exporting import TBX_NS


def identifier(prefix):
    return f"{prefix}-{uuid4().hex[:12]}"


def csv_records(content, columns, kind):
    rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    groups, resources = {}, {}
    for line, raw in enumerate(rows, 2):
        row = {target: raw[source] for target, source in columns.items()} if columns else raw
        for key, value in list(row.items()):
            if value and value.lstrip().startswith(("[", "{")):
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError as error:
                    raise ValueError(f"CSV 第 {line} 行 {key}：JSON 单元格无效") from error
        if "translation_id" in row:
            translation_id = row.get("translation_id") or identifier("translation")
            source = row.get("source_sense", {}).get("id") or row.get("sense_id")
            target = row.get("target_sense", {}).get("id") or row.get("target_sense_id")
            relation = {"id": translation_id, "source_sense_id": source, "target_sense_id": target,
                        **{k: row[k] for k in ("scope", "evidence_ids", "note", "status") if k in row}}
            resources.setdefault("translations", []).append(relation)
            if row.get("profile"):
                resources.setdefault("profiles", {})[row["profile"]["id"]] = row["profile"]
            continue
        if kind != "entries":
            resources.setdefault(kind, []).append({k: v for k, v in row.items() if v != ""})
            continue
        entry_id = row.get("entry_id") or row.get("id") or identifier("entry")
        if entry_id not in groups:
            groups[entry_id] = {k: row[k] for k in ("language", "lemma", "part_of_speech", "forms", "article") if row.get(k) not in (None, "")}
            groups[entry_id].update(id=entry_id, senses=[])
        entry = groups[entry_id]
        for key in ("language", "lemma", "part_of_speech", "forms", "article"):
            if row.get(key) not in (None, "") and entry.get(key) != row[key]:
                raise ValueError(f"CSV 第 {line} 行 {entry_id}.{key}：同一词条的单元格不一致")
        if row.get("senses"):
            entry["senses"] = row["senses"]
        elif any(k in row for k in ("concept_id", "definition", "usage", "sense_id", "entry_id")):
            sense = {k: row[k] for k in ("definition", "concept_id", "usage", "work_ids", "evidence_ids", "status") if k in row}
            sense["id"] = row.get("sense_id") or (row.get("id") if "entry_id" in row else None) or identifier("sense")
            entry["senses"].append(sense)
    resources["entries"] = list(groups.values())
    if "profiles" in resources:
        resources["profiles"] = list(resources["profiles"].values())
    return resources


def tbx_records(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"TBX XML：{error}") from error
    if root.tag != f"{{{TBX_NS}}}tbx" or root.get("type") != "TBX-Basic" or root.get("style") != "dca":
        raise ValueError("仅支持 TBX v3、TBX-Basic DCA 文件")
    ns = {"t": TBX_NS}
    resources = {kind: {} for kind in CATEGORIES}
    notes = []

    def add(kind, record):
        previous = resources[kind].get(record["id"])
        if previous and previous != record:
            raise ValueError(f"TBX 中的 {record['id']} 内容不一致")
        resources[kind][record["id"]] = record

    def unpack(element, prefix):
        for note in element.findall("t:note", ns):
            if (note.text or "").startswith(prefix):
                try:
                    return json.loads(note.text[len(prefix):])
                except json.JSONDecodeError as error:
                    raise ValueError(f"TBX 扩展说明 {prefix} 不是有效 JSON") from error
        return None

    for group in root.findall(".//t:conceptEntry", ns):
        concept = unpack(group, "概念说明与关系：")
        cid = group.get("id") or identifier("concept")
        if concept is None:
            definition = group.findtext("t:descrip[@type='definition']", None, ns) or group.findtext("t:descripGrp/t:descrip[@type='definition']", "", ns)
            concept = {"id": cid, "labels": {}, "definition": definition,
                       "evidence_ids": [], "relations": []}
            notes.append(f"{cid}：外部 TBX 的来源说明保留在导入笔记；须补登记证据，不自动推导译法或学术审阅。")
        for section in group.findall("t:langSec", ns):
            language = section.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            for term in section.findall("t:termSec", ns):
                lemma = term.findtext("t:term", "", ns)
                sense = unpack(term, "词条与义项：")
                if sense:
                    eid = sense.pop("entry_id")
                    forms = sense.pop("forms", [])
                    # 新版导出额外保存词性，旧版 other 无法恢复时需在草稿中补齐。
                    pos = sense.pop("part_of_speech", None) or term.findtext("t:termNote[@type='partOfSpeech']", "noun", ns)
                    existing = resources["entries"].get(eid)
                    if existing:
                        existing["senses"].append(sense)
                    else:
                        add("entries", {"id": eid, "language": language, "lemma": lemma,
                                        "part_of_speech": pos, "forms": forms, "senses": [sense]})
                else:
                    eid = identifier("entry")
                    definition = term.findtext("t:descrip[@type='definition']", concept["definition"], ns)
                    sense = {"id": term.get("id") or identifier("sense"), "definition": definition,
                             "concept_id": concept["id"], "usage": "", "evidence_ids": [], "status": "draft"}
                    pos = term.findtext("t:termNote[@type='partOfSpeech']", "noun", ns)
                    add("entries", {"id": eid, "language": language, "lemma": lemma,
                                    "part_of_speech": pos, "senses": [sense]})
                    concept["labels"].setdefault(language, lemma)
                for source in term.findall("t:admin[@type='source']", ns):
                    try:
                        bundle = json.loads(source.text or "")
                    except json.JSONDecodeError:
                        notes.append(f"{eid} 来源说明：{''.join(source.itertext())}")
                        continue
                    if isinstance(bundle, dict) and any(k in bundle for k in ("works", "editions", "attestations")):
                        for kind in ("works", "editions", "attestations"):
                            for item in bundle.get(kind, []):
                                add(kind, item)
                    else:
                        notes.append(f"{eid} 来源说明：{source.text}")
                for note in term.findall("t:termNote", ns):
                    if note.get("type") != "partOfSpeech":
                        notes.append(f"{eid} {note.get('type')}：{note.text or ''}")
        add("concepts", concept)
        profile = unpack(group, "项目条件：")
        if profile:
            add("profiles", profile)
        translations = unpack(group, "有方向的译法及适用条件：")
        for item in translations or []:
            if "translation_id" in item:
                item = {"id": item["translation_id"], "source_sense_id": item["source_sense"]["id"],
                        "target_sense_id": item["target_sense"]["id"],
                        **{k: item[k] for k in ("scope", "evidence_ids", "note", "status")}}
            add("translations", item)
        for element in group.findall("t:admin", ns) + group.findall("t:note", ns):
            text = "".join(element.itertext())
            if not text.startswith(("概念说明与关系：", "有方向的译法及适用条件：", "项目条件：")):
                notes.append(f"{cid} {element.get('type', '说明')}：{text}")
        for element in group.findall(".//t:descripGrp/t:admin[@type='source']", ns) + group.findall("t:langSec/t:admin[@type='source']", ns):
            notes.append(f"{cid} 来源说明：{''.join(element.itertext())}")
    return {kind: list(items.values()) for kind, items in resources.items()}, notes


def parse_import(request, current):
    notes = []
    if request.format == "csv":
        resources = csv_records(request.content, request.columns, request.kind)
        notes.append("CSV 只恢复所含字段；空缺的完整证据及文章不会自动补造。")
    elif request.format == "tbx":
        resources, notes = tbx_records(request.content)
    else:
        try:
            raw = json.loads(request.content.lstrip("\ufeff")) if request.format == "json" else yaml.safe_load(request.content)
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            raise ValueError(f"导入文件无法解析：{error}") from error
        if isinstance(raw, dict) and "dataset" in raw:
            resources = {**raw["dataset"], "articles": raw.get("articles", {})}
            resources.pop("manifest", None)
            notes.append("快照保留资源 ID；发布时使用本库当前版本并递增，不回退版本号。")
        elif isinstance(raw, dict) and any(k in raw for k in (*CATEGORIES, "articles")):
            resources = raw
        elif isinstance(raw, list):
            resources = {request.kind: raw}
        elif isinstance(raw, dict):
            resources = {request.kind: [raw]}
        else:
            raise ValueError("文件须为资源对象、列表或按资源类别组织的数据包")
    operations = []
    seen = set()
    for kind, items in resources.items():
        if kind not in (*CATEGORIES, "articles"):
            raise ValueError(f"未知资源类别 {kind}")
        if kind == "articles" and isinstance(items, dict):
            items = [{"id": name, "content": content} for name, content in items.items()]
        if not isinstance(items, list):
            raise ValueError(f"{kind} 须为列表")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{kind}[{index}] 须为对象")
            item = dict(item)
            if not item.get("id"):
                item["id"] = identifier(kind.rstrip("s"))
                notes.append(f"{kind}[{index}] 分配稳定 ID：{item['id']}")
            key = (kind, item["id"])
            if key in seen:
                raise ValueError(f"{kind}[{index}] 重复 ID：{item['id']}")
            seen.add(key)
            exists = item["id"] in current[kind]
            action = "update" if request.mode == "update" or (request.mode == "upsert" and exists) else "create"
            operations.append(Operation(action=action, kind=kind, id=item["id"], values=item))
    if not operations:
        raise ValueError("文件中没有可导入的资源")
    return operations, notes
