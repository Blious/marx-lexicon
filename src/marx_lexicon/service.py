"""供命令行、网页及 MCP 共用的查询逻辑。"""

import json
import re
import sqlite3
from pathlib import Path

from .catalog import forms, normalize
from .database import read_dataset
from .reference import compact_reference, project_evidence
from .terms import TermQueries


class VersionMismatch(ValueError):
    pass


def scope_matches(scope, work_id, edition_id):
    return ((not scope.work_ids or work_id in scope.work_ids)
            and (not scope.edition_ids or edition_id in scope.edition_ids))


def scope_state(scope, work_id, edition_id):
    if ((work_id and scope.work_ids and work_id not in scope.work_ids)
            or (edition_id and scope.edition_ids and edition_id not in scope.edition_ids)):
        return "mismatch"
    return "match" if scope_matches(scope, work_id, edition_id) else "needs_context"


class Lexicon(TermQueries):
    def __init__(self, database: Path):
        if not database.is_file():
            raise FileNotFoundError("数据库不存在，请先运行 python -m marx_lexicon build，或通过 --database 指定数据库")
        self.connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        self.dataset = read_dataset(self.connection)
        self.entries = {entry.id: entry for entry in self.dataset.entries}
        self.terms = {term.id: term for term in self.dataset.terms}
        self.senses = {sense.id: (entry, sense) for entry in self.dataset.entries
                       for sense in entry.senses}
        self.editions = {edition.id: edition for edition in self.dataset.editions}
        self.concepts = {concept.id: concept for concept in self.dataset.concepts}
        self.translations = {item.id: item for item in self.dataset.translations}
        self.evidence = {item.id: item for item in self.dataset.attestations}
        self.profiles = {item.id: item for item in self.dataset.profiles}
        self.articles = json.loads(self.connection.execute(
            "SELECT value FROM metadata WHERE key='articles'").fetchone()[0])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def version(self):
        return {"schema_version": self.dataset.manifest.schema_version,
                "dataset_version": self.dataset.manifest.dataset_version}

    def get_metadata(self):
        return {**self.version(), "title": self.dataset.manifest.title,
                "coverage": self.dataset.manifest.description,
                "languages": self.dataset.manifest.languages,
                "counts": {name: len(getattr(self.dataset, name)) for name in
                           ("terms", "entries", "concepts", "translations", "attestations")},
                "unassigned_concepts": self.list_unassigned(limit=1)["total"],
                "review_counts": {state: sum(e.review_status == state for e in self.dataset.attestations)
                                  for state in ("unreviewed", "reviewed")},
                "works": [w.model_dump(mode="json") for w in self.dataset.works],
                "editions": [e.model_dump(mode="json") for e in self.dataset.editions],
                "concepts": [{"id": c.id, "labels": c.labels} for c in self.dataset.concepts],
                "profiles": [p.model_dump(mode="json", exclude={"selections"})
                             for p in self.dataset.profiles]}

    def list_entries(self, query=None, language=None, work_id=None, concept_id=None, offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 须非负，limit 须在 1 至 100 之间")
        if language and language not in self.dataset.manifest.languages:
            raise ValueError("未登记的语言：" + language)
        if work_id and work_id not in {w.id for w in self.dataset.works}:
            raise KeyError(work_id)
        if concept_id and concept_id not in self.concepts:
            raise KeyError(concept_id)
        if query and query.strip():
            candidates = self.lookup(query, language, max(len(self.entries), 1))["results"]
        else:
            candidates = [self.summary(e.id) for e in sorted(self.dataset.entries,
                          key=lambda e: (normalize(e.lemma), e.id)) if not language or e.language == language]
        candidates = [e for e in candidates if any(
            (not work_id or not s.work_ids or work_id in s.work_ids) and
            (not concept_id or s.concept_id == concept_id) for s in self.entries[e["id"]].senses)]
        return {**self.version(), "results": candidates[offset:offset + limit],
                "total": len(candidates), "offset": offset, "limit": limit}

    def read_concept(self, concept_id):
        concept = self.concepts[concept_id]
        identifiers = set(concept.evidence_ids)
        for relation in concept.relations:
            identifiers.update(relation.evidence_ids)
        return {**self.version(), "concept": concept.model_dump(mode="json"),
                "entries": [self.summary(e.id) for e in self.dataset.entries
                            if any(s.concept_id == concept_id for s in e.senses)],
                "related_concepts": [{"id": r.target_id, "labels": self.concepts[r.target_id].labels}
                                     for r in concept.relations], **self.evidence_bundle(identifiers)}

    def summary(self, entry_id):
        entry = self.entries[entry_id]
        return {"id": entry.id, "language": entry.language, "lemma": entry.lemma,
                "senses": [{"id": sense.id, "definition": sense.definition}
                           for sense in entry.senses]}

    def lookup(self, query, language=None, limit=20):
        if language and language not in self.dataset.manifest.languages:
            raise ValueError("未登记的语言：" + language)
        query = normalize(query)
        if not query:
            raise ValueError("查询词不能为空")
        if limit < 1:
            raise ValueError("limit 须大于零")
        rows = self.connection.execute("""
            SELECT entry_id, MIN(CASE WHEN normalized=? THEN 0
                  WHEN instr(normalized,?)=1 THEN 1 ELSE 2 END) AS rank
            FROM forms WHERE instr(normalized,?)>0 AND (? IS NULL OR language=?)
            GROUP BY entry_id ORDER BY rank, entry_id LIMIT ?
        """, (query, query, query, language, language, limit)).fetchall()
        found = [row[0] for row in rows]
        if len(found) < limit:
            # 查询作为单个 FTS 短语处理，保留引号等字符的字面意义。
            phrase = '"' + query.replace('"', '""') + '"'
            if any("\u3400" <= char <= "\u9fff" for char in query):
                sql = "SELECT entry_id FROM documents WHERE instr(text,?)>0 ORDER BY entry_id"
                parameter = query
            else:
                sql = "SELECT entry_id FROM documents WHERE documents MATCH ? ORDER BY rank, entry_id"
                parameter = phrase
            for (entry_id,) in self.connection.execute(sql, (parameter,)):
                if entry_id not in found and (language is None or self.entries[entry_id].language == language):
                    found.append(entry_id)
                    if len(found) == limit:
                        break
        return {**self.version(), "query": query,
                "results": [self.summary(entry_id) for entry_id in found]}

    def evidence_bundle(self, identifiers):
        evidence = [self.evidence[identifier] for identifier in sorted(set(identifiers))]
        edition_ids = {p.edition_id for item in evidence for p in item.passages}
        work_ids = {self.editions[identifier].work_id for identifier in edition_ids}
        return {
            "attestations": [item.model_dump(mode="json") for item in evidence],
            "editions": [self.editions[identifier].model_dump(mode="json")
                         for identifier in sorted(edition_ids)],
            "works": [work.model_dump(mode="json") for work in self.dataset.works
                      if work.id in work_ids],
        }

    def read_evidence(self, identifiers, languages=None, dataset_version=None):
        if dataset_version is not None and dataset_version != self.dataset.manifest.dataset_version:
            raise VersionMismatch("证据请求的数据版本已变化，请重新准备翻译参考")
        if not identifiers:
            raise ValueError("请提供至少一个证据 ID")
        bundle = self.evidence_bundle(identifiers)
        if languages:
            unknown = set(languages) - set(self.dataset.manifest.languages)
            if unknown:
                raise ValueError("未登记的语言：" + ", ".join(sorted(unknown)))
            bundle = project_evidence(bundle, languages)
        return {**self.version(), **bundle}

    def read_entry(self, entry_id):
        entry = self.entries[entry_id]
        sense_ids = {sense.id for sense in entry.senses}
        translations = [item for item in self.dataset.translations
                        if item.source_sense_id in sense_ids or item.target_sense_id in sense_ids]
        concept_ids = {sense.concept_id for sense in entry.senses}
        evidence_ids = {identifier for sense in entry.senses for identifier in sense.evidence_ids}
        for item in translations:
            evidence_ids.update(item.evidence_ids)
        for concept_id in concept_ids:
            concept = self.concepts[concept_id]
            evidence_ids.update(concept.evidence_ids)
            for relation in concept.relations:
                evidence_ids.update(relation.evidence_ids)
        linked_ids = {self.senses[sense_id][0].id for item in translations
                      for sense_id in (item.source_sense_id, item.target_sense_id)} - {entry_id}
        return {**self.version(), "entry": entry.model_dump(mode="json"),
                "concepts": [self.concepts[key].model_dump(mode="json") for key in sorted(concept_ids)],
                "translations": [item.model_dump(mode="json") for item in translations],
                "linked_entries": [self.summary(key) for key in sorted(linked_ids)],
                "article": self.articles.get(entry.article),
                **self.evidence_bundle(evidence_ids)}

    def get_profile(self, profile_id, source_language, target_language):
        profile = self.profiles[profile_id]
        if (profile.source_language, profile.target_language) != (source_language, target_language):
            raise ValueError("翻译方案的语言方向与请求不符")
        if profile.dataset_version != self.dataset.manifest.dataset_version:
            raise ValueError("翻译方案与数据库版本不符，请重新构建")
        return profile

    def sense_options(self, entry, target_language, profile, work_id, edition_id, sense_id=None):
        options = []
        for sense in entry.senses:
            if sense_id and sense.id != sense_id:
                continue
            if work_id and sense.work_ids and work_id not in sense.work_ids:
                continue
            translations = []
            selections = [selection for selection in profile.selections
                          if selection.source_sense_id == sense.id]
            selected_ids = {selection.translation_id for selection in selections
                            if scope_matches(selection.scope, work_id, edition_id)}
            for item in self.dataset.translations:
                if item.source_sense_id != sense.id:
                    continue
                target, target_sense = self.senses[item.target_sense_id]
                applicability = scope_state(item.scope, work_id, edition_id)
                if target.language != target_language or applicability == "mismatch":
                    continue
                translations.append({
                    **item.model_dump(mode="json"),
                    "target": {"id": target.id, "language": target.language, "lemma": target.lemma},
                    "target_sense": target_sense.model_dump(mode="json"),
                    "target_definition": target_sense.definition,
                    "selected_by_profile": item.id in selected_ids,
                    "applicability": applicability,
                    "selection_notes": [s.note for s in selections if s.translation_id == item.id
                                        and scope_matches(s.scope, work_id, edition_id)],
                    "profile_choices": [{"scope": s.scope.model_dump(mode="json"),
                                         "applicability": scope_state(s.scope, work_id, edition_id),
                                         "note": s.note} for s in selections if s.translation_id == item.id],
                })
            options.append({"sense": sense.model_dump(mode="json"), "translations": translations})
        return options

    def prepare_translation(self, text, *, source_language, target_language, profile_id,
                            work_id=None, edition_id=None, sense_id=None, detail="compact"):
        if detail not in ("compact", "full"):
            raise ValueError("detail 须为 compact 或 full")
        profile = self.get_profile(profile_id, source_language, target_language)
        if not text.strip():
            raise ValueError("待译文本不能为空")
        if edition_id:
            edition = self.editions[edition_id]
            if edition.language != source_language:
                raise ValueError("所选版本与源语言不符")
            if work_id and work_id != edition.work_id:
                raise ValueError("所选作品与版本不符")
            work_id = edition.work_id
        if work_id and work_id not in {work.id for work in self.dataset.works}:
            raise KeyError(work_id)
        if sense_id and (sense_id not in self.senses or self.senses[sense_id][0].language != source_language):
            raise ValueError("所选义项不存在或与源语言不符")
        if not scope_matches(profile.scope, work_id, edition_id):
            raise ValueError("当前作品或版本不在翻译方案的适用范围内")
        matches = []
        for entry in self.dataset.entries:
            if entry.language != source_language:
                continue
            for surface in dict.fromkeys(forms(entry)):
                pattern = r"\s+".join(re.escape(part) for part in surface.split())
                if not entry.language.startswith("zh"):
                    pattern = r"(?<!\w)" + pattern + r"(?!\w)"
                for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                    matches.append((match.start(), match.end(), entry.id))
        # 同一位置先保留最长术语；相同跨度的不同词条一起返回，不消除同形歧义。
        spans = []
        for start, end in sorted({(s, e) for s, e, _ in matches}, key=lambda pair: (-(pair[1]-pair[0]), pair[0])):
            if not any(start < right and end > left for left, right in spans):
                spans.append((start, end))
        occurrences = []
        evidence_ids = set()
        for start, end, entry_id in sorted(set(matches)):
            if (start, end) not in spans:
                continue
            entry = self.entries[entry_id]
            # 显式义项仅约束所属词条，其他命中仍照常处理。
            selected_sense = sense_id if sense_id in {s.id for s in entry.senses} else None
            options = self.sense_options(entry, target_language, profile, work_id, edition_id, selected_sense)
            chosen = [(option, item) for option in options for item in option["translations"]
                      if item["selected_by_profile"]]
            if len(options) == 1 and len(chosen) == 1 and chosen[0][1]["applicability"] == "match":
                recommendation = {"sense_id": options[0]["sense"]["id"],
                                  "translation_id": chosen[0][1]["id"],
                                  "text": chosen[0][1]["target"]["lemma"]}
                unresolved = []
            elif len(options) != 1:
                recommendation = None
                unresolved = ["存在多个义项，需要结合上下文明确。" if options
                              else "当前作品范围内未登记适用义项。"]
            else:
                recommendation = None
                if not options[0]["translations"]:
                    unresolved = ["当前目标语言或范围内没有可用译法。"]
                elif len(chosen) != 1:
                    unresolved = ["缺少唯一的适用方案选择。"]
                else:
                    unresolved = ["译法范围尚缺少作品或版本条件。"]
            for option in options:
                evidence_ids.update(option["sense"]["evidence_ids"])
                for item in option["translations"]:
                    evidence_ids.update(item["evidence_ids"])
                    evidence_ids.update(item["target_sense"]["evidence_ids"])
            occurrences.append({"entry_id": entry_id, "lemma": entry.lemma,
                                "start": start, "end": end, "surface": text[start:end],
                                "candidates": options, "recommendation": recommendation,
                                "unresolved": unresolved})
        concept_ids = {option["sense"]["concept_id"] for item in occurrences for option in item["candidates"]}
        relations = []
        for identifier in sorted(concept_ids):
            for relation in self.concepts[identifier].relations:
                target = self.concepts[relation.target_id]
                relations.append({"source_concept_id": identifier, **relation.model_dump(mode="json"),
                                  "target_labels": {language: label for language, label in target.labels.items()
                                                    if language in (source_language, target_language)}})
                evidence_ids.update(relation.evidence_ids)
        result = {**self.version(), "profile_id": profile.id, "source_language": source_language,
                "target_language": target_language, "work_id": work_id, "edition_id": edition_id,
                "profile": {"id": profile.id, "scope": profile.scope.model_dump(mode="json"), "note": profile.note},
                "concept_relations": relations,
                "text": text, "occurrences": occurrences, **self.evidence_bundle(evidence_ids),
                "note": "参考包仅覆盖已登记词形；未命中不表示文本没有其他术语。义项须结合上下文判断。"}
        return compact_reference(result) if detail == "compact" else result

    def export_glossary(self, profile_id):
        profile = self.profiles[profile_id]
        self.get_profile(profile_id, profile.source_language, profile.target_language)
        rows = []
        evidence_ids = set()
        for entry in self.dataset.entries:
            if entry.language != profile.source_language:
                continue
            for sense in entry.senses:
                for translation in self.dataset.translations:
                    target, target_sense = self.senses[translation.target_sense_id]
                    if translation.source_sense_id != sense.id or target.language != profile.target_language:
                        continue
                    item = translation.model_dump(mode="json")
                    rows.append({"entry_id": entry.id, "source": entry.lemma,
                                 "sense_id": sense.id,
                                 "definition": sense.definition,
                                 "source_language": entry.language, "usage": sense.usage,
                                 "source_sense": sense.model_dump(mode="json"),
                                 "target_language": target.language, "target_entry_id": target.id,
                                 "target_sense": target_sense.model_dump(mode="json"),
                                 "status": item["status"],
                                 "profile_choices": [s.model_dump(mode="json") for s in profile.selections
                                                     if s.translation_id == item["id"]],
                                 "target": target.lemma,
                                 "translation_id": item["id"],
                                 "selected_by_profile": any(s.translation_id == item["id"] for s in profile.selections),
                                 "selection_scopes": [s.scope.model_dump(mode="json") for s in profile.selections
                                                      if s.translation_id == item["id"]],
                                 "scope": item["scope"], "note": item["note"],
                                 "evidence_ids": item["evidence_ids"]})
                    evidence_ids.update(item["evidence_ids"])
                    evidence_ids.update(sense.evidence_ids)
                    evidence_ids.update(target_sense.evidence_ids)
        return {**self.version(), "profile": profile.model_dump(mode="json"),
                "rows": rows, **self.evidence_bundle(evidence_ids)}
