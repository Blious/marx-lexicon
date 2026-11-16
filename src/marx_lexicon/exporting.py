"""统一文件导出；交换格式的表达边界随报告一并交付。"""

import csv
import io
import json
from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .render import markdown

BASE_URI = "https://github.com/Blious/marx-lexicon/id/"
TBX_NS = "urn:iso:std:iso:30042:ed-2"
MEDIA = {"json": ("application/json", "json"), "markdown": ("text/markdown", "md"),
         "csv": ("text/csv", "csv"), "tbx": ("application/xml", "tbx"),
         "skos": ("text/turtle", "ttl")}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class ExportResult:
    content: bytes
    media_type: str
    filename: str
    report: dict


def snapshot(lexicon):
    return {**lexicon.version(), "dataset": lexicon.dataset.model_dump(mode="json"),
            "articles": lexicon.articles}


def sense_rows(lexicon):
    return [{"entry_id": e.id, "language": e.language, "lemma": e.lemma,
             "part_of_speech": e.part_of_speech, "forms": [f.model_dump(mode="json") for f in e.forms],
             **s.model_dump(mode="json"), "article": e.article,
             **lexicon.version()} for e in lexicon.dataset.entries for s in e.senses]


def csv_content(rows):
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: compact(value) if isinstance(value, (list, dict)) else value
                         for key, value in row.items()})
    return stream.getvalue().encode("utf-8-sig")


def tbx_content(lexicon, glossary=None):
    ET.register_namespace("", TBX_NS)

    def node(parent, name, text=None, **attrs):
        element = ET.SubElement(parent, "{" + TBX_NS + "}" + name, attrs)
        if text is not None:
            element.text = text
        return element

    root = ET.Element("{" + TBX_NS + "}tbx", {
        "type": "TBX-Basic", "style": "dca", "{http://www.w3.org/XML/1998/namespace}lang": "zh-Hans"})
    header = node(root, "tbxHeader")
    file_desc = node(header, "fileDesc")
    node(node(file_desc, "titleStmt"), "title", lexicon.dataset.manifest.title)
    node(node(file_desc, "sourceDesc"), "p", "数据版本 " + lexicon.dataset.manifest.dataset_version +
         "。范围、审阅状态及有方向的译法条件见 note；不表示全部术语可无条件互换。")
    body = node(node(root, "text"), "body")
    sense_ids = ({r[key]["id"] for r in glossary["rows"] for key in ("source_sense", "target_sense")}
                 if glossary else set(lexicon.senses))
    for concept in lexicon.dataset.concepts:
        members = [(e, s) for identifier, (e, s) in lexicon.senses.items()
                   if identifier in sense_ids and s.concept_id == concept.id]
        if not members:
            continue
        group = node(body, "conceptEntry", id=concept.id)
        node(group, "descrip", concept.definition, type="definition")
        node(group, "note", "概念说明与关系：" + compact(concept.model_dump(mode="json")))
        if glossary:
            node(group, "admin", glossary["profile"]["id"], type="projectSubset")
            node(group, "note", "项目条件：" + compact(glossary["profile"]))
        relations = [r for r in (glossary["rows"] if glossary else
                     [t.model_dump(mode="json") for t in lexicon.dataset.translations])
                     if (r.get("sense_id", r.get("source_sense_id")) in {s.id for _, s in members}
                         or r.get("target_sense_id", r.get("target_sense", {}).get("id"))
                         in {s.id for _, s in members})]
        node(group, "note", "有方向的译法及适用条件：" + compact(relations))
        for language in sorted({e.language for e, _ in members}):
            section = node(group, "langSec", **{"{http://www.w3.org/XML/1998/namespace}lang": language})
            for entry, sense in members:
                if entry.language != language:
                    continue
                term = node(section, "termSec", id=sense.id)
                node(term, "term", entry.lemma)
                node(term, "termNote", entry.part_of_speech if entry.part_of_speech != "phrase" else "other",
                     type="partOfSpeech")
                node(term, "note", "词条与义项：" + compact({"entry_id": entry.id,
                     "part_of_speech": entry.part_of_speech,
                     "forms": [f.model_dump(mode="json") for f in entry.forms], **sense.model_dump(mode="json")}))
                node(term, "admin", compact(lexicon.evidence_bundle(sense.evidence_ids)), type="source")
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def skos_content(lexicon, base_uri):
    from rdflib import Graph, Literal, Namespace, RDF, SKOS, URIRef

    if urlparse(base_uri).scheme not in ("https", "http") or not base_uri.endswith(("/", "#")):
        raise ValueError("base_uri 须为以 / 或 # 结尾的 HTTP(S) URI")
    graph = Graph()
    ns = Namespace(base_uri)
    graph.bind("lex", ns)
    graph.bind("skos", SKOS)
    scheme = ns["scheme"]
    graph.add((scheme, RDF.type, SKOS.ConceptScheme))
    graph.add((scheme, SKOS.prefLabel, Literal(lexicon.dataset.manifest.title, lang="zh-Hans")))
    graph.add((scheme, ns.datasetVersion, Literal(lexicon.dataset.manifest.dataset_version)))
    graph.add((scheme, SKOS.scopeNote, Literal(lexicon.dataset.manifest.description, lang="zh-Hans")))
    for concept in lexicon.dataset.concepts:
        subject = ns[concept.id]
        graph.add((subject, RDF.type, SKOS.Concept))
        graph.add((subject, SKOS.inScheme, scheme))
        graph.add((subject, SKOS.definition, Literal(concept.definition, lang="zh-Hans")))
        for lang, label in concept.labels.items():
            graph.add((subject, SKOS.prefLabel, Literal(label, lang=lang)))
        for evidence in concept.evidence_ids:
            graph.add((subject, ns.evidence, ns[evidence]))
        for index, relation in enumerate(concept.relations):
            relation_id = ns[f"{concept.id}-relation-{index}"]
            graph.add((subject, ns.relation, relation_id))
            graph.add((relation_id, RDF.type, ns.ConceptRelation))
            graph.add((relation_id, ns.target, ns[relation.target_id]))
            graph.add((relation_id, ns.kind, Literal(relation.kind)))
            graph.add((relation_id, ns.status, Literal(relation.status)))
            graph.add((relation_id, SKOS.note, Literal(relation.explanation, lang="zh-Hans")))
            for evidence in relation.evidence_ids:
                graph.add((relation_id, ns.evidence, ns[evidence]))
            if relation.status == "reviewed" and relation.kind in ("broader", "narrower", "related"):
                graph.add((subject, SKOS[relation.kind], ns[relation.target_id]))
    for evidence in lexicon.dataset.attestations:
        graph.add((ns[evidence.id], ns.reviewStatus, Literal(evidence.review_status)))
        for passage in evidence.passages:
            graph.add((ns[evidence.id], ns.source, URIRef(passage.location.url)))
    return graph.serialize(format="turtle").encode("utf-8")


def export_file(lexicon, format="json", profile_id=None, base_uri=BASE_URI):
    if format not in MEDIA or (profile_id and format == "skos"):
        raise ValueError("全库支持 json/markdown/csv/tbx/skos；项目术语表支持 json/markdown/csv/tbx")
    glossary = lexicon.export_glossary(profile_id) if profile_id else None
    rows = glossary["rows"] if glossary else sense_rows(lexicon)
    if format == "json":
        content = (json.dumps(glossary or snapshot(lexicon), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    elif format == "markdown":
        if glossary:
            content = markdown(glossary).encode("utf-8")
        else:
            content = ("# " + lexicon.dataset.manifest.title + "\n\n" +
                       lexicon.dataset.manifest.description + "\n\n" + "\n\n---\n\n".join(
                           markdown(lexicon.read_entry(e.id)) for e in lexicon.dataset.entries)).encode("utf-8")
    elif format == "csv":
        content = csv_content([{**r, **lexicon.version(), "profile": glossary["profile"]} for r in rows]
                              if glossary else rows)
    elif format == "tbx":
        content = tbx_content(lexicon, glossary)
    else:
        content = skos_content(lexicon, base_uri)
    losses = {
        "json": [] if not glossary else ["仅含项目语言方向的术语表及证据，不是全库快照。"],
        "markdown": ["人工阅读视图，不能完整重建结构化关系与标注。"],
        "csv": ["嵌套字段为 JSON 单元格；全库表不包含文章全文、译法关系及完整证据。"
                if not glossary else "项目表保留译法及条件；完整引文和版本书目须按证据 ID 从 JSON 或原库读取。"],
        "tbx": ["TBX-Basic 的标准字段不能执行有方向的译法条件；条件、状态与证据作为 note/admin 保存。",
                "不包含文章全文；不能据术语同组出现推导无条件可互换或学术审定。"],
        "skos": ["只导出概念组织及证据引用，不包含词条义项、项目译法、完整引文与文章。",
                 "草稿关系与 distinct/possible_equivalent 使用项目关系记录，不断言 exactMatch。"],
    }
    media_type, extension = MEDIA[format]
    return ExportResult(content, media_type, f"{profile_id or 'lexicon'}.{extension}", {
        **lexicon.version(), "format": format, "scope": profile_id or "dataset",
        "counts": {"rows": len(rows), "concepts": len({r[key]['concept_id'] for r in rows
                                                       for key in ('source_sense', 'target_sense')})
                   if glossary else len(lexicon.concepts)},
        "complete_snapshot": format == "json" and not profile_id,
        "mapping": {"json": "Dataset + articles（全库）或既有 profile/rows/evidence 结构（项目）",
                    "markdown": "词条/义项/译法/证据的人工视图", "csv": "每行一个义项（全库）或一条译法（项目）",
                    "tbx": "concept → conceptEntry；语言 → langSec；义项 → termSec；条件与证据 → note/admin",
                    "skos": "concept → skos:Concept；labels → prefLabel；已审阅层级/关联 → SKOS 属性"}[format],
        "losses": losses[format], **({"base_uri": base_uri} if format == "skos" else {})})
