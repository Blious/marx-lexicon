"""整体术语文档与规范源批次之间的转换。"""

import copy

from .catalog import CATEGORIES
from .edit_models import ChangeBatch, DraftRequest, Operation, TermSaveRequest
from .service import Lexicon
from .models import Term


class TermEditing:
    def read_editable_term(self, term_id):
        with self.locked(), Lexicon(self.database) as lexicon:
            term = lexicon.terms[term_id]
            return self._editable_term(lexicon, term)

    def assemble_term(self, term):
        with self.locked(), Lexicon(self.database) as lexicon:
            return self._editable_term(lexicon, Term.model_validate(term))

    def _editable_term(self, lexicon, term):
        ids = lexicon.term_resource_ids(term)
        resources = {}
        for kind in CATEGORIES:
            if kind in ("terms", "profiles"):
                continue
            resources[kind] = [item.model_dump(mode="json") for item in getattr(lexicon.dataset, kind)
                               if item.id in ids[kind]]
        for entry in resources["entries"]:
            entry["senses"] = [s for s in entry["senses"] if s["id"] in ids["senses"]]
        resources["articles"] = [{"id": name, "content": lexicon.articles[name]} for name in sorted(ids["articles"])]
        shared = []
        for other in lexicon.dataset.terms:
            if other.id == term.id:
                continue
            other_ids = lexicon.term_resource_ids(other)
            for kind in ids:
                for identifier in sorted(ids[kind] & other_ids[kind]):
                    shared.append({"kind": kind, "id": identifier, "term_id": other.id, "title": other.title})
        return {**lexicon.version(), "revision": self._revision(),
                "document": {"term": term.model_dump(mode="json"), "resources": resources}, "shared_usage": shared,
                "external_sense_ids": {e.id: [s.id for s in e.senses if s.id not in ids["senses"]]
                                       for e in lexicon.dataset.entries if e.id in ids["entries"]}}

    def reference_catalog(self):
        with self.locked():
            _, records = self._records()
            catalog = {kind: [{"id": item["id"], "label": item.get("title") or item.get("lemma") or item.get("note") or
                              next(iter(item.get("labels", {}).values()), item["id"])}
                             for item in items.values()] for kind, items in records.items()}
            catalog["senses"] = [{"id": s["id"], "label": f"{e['lemma']} · {e['language']} · {s['definition']}"}
                                 for e in records["entries"].values() for s in e["senses"]]
            return {"revision": self._revision(), "catalog": catalog}

    def list_unassigned(self, offset=0, limit=20):
        with self.locked(), Lexicon(self.database) as lexicon:
            return {"revision": self._revision(), **lexicon.list_unassigned(offset, limit)}

    def _term_batch(self, request):
        self._expect(request.expected_revision)
        _, current = self._records()
        term = copy.deepcopy(request.term)
        identifier = term.get("id")
        if not identifier:
            raise ValueError("术语缺少稳定 id")
        if any(d.kind == "terms" and d.id == identifier for d in request.delete):
            term_deleted = True
        else:
            term_deleted = False
        pending = {}

        def put(kind, record):
            rid = record.get("id")
            if not isinstance(rid, str) or not rid:
                raise ValueError(f"{kind} 的记录缺少稳定 id")
            key = (kind, rid)
            if key in pending:
                raise ValueError(f"重复资源 {kind}/{rid}")
            pending[key] = copy.deepcopy(record)

        if not term_deleted:
            put("terms", term)
        concepts = set(term.get("concept_ids", current["terms"].get(identifier, {}).get("concept_ids", [])))
        concepts.update(current["terms"].get(identifier, {}).get("concept_ids", []))
        for kind, records in request.resources.items():
            if kind not in (*CATEGORIES, "articles") or kind == "profiles":
                raise ValueError(f"整体术语不接受资源类别 {kind}，项目配置使用高级管理")
            for record in records:
                put(kind, record)
        for (kind, rid), record in pending.items():
            if kind != "entries" or "senses" not in record:
                continue
            old = current["entries"].get(rid, {})
            senses = {s["id"]: copy.deepcopy(s) for s in old.get("senses", [])}
            seen = set()
            for sense in record["senses"]:
                sid = sense.get("id")
                if not sid or sid in seen:
                    raise ValueError(f"{rid} 存在缺失或重复的义项 ID")
                seen.add(sid)
                merged = {**senses.get(sid, {}), **sense}
                if merged.get("concept_id") not in concepts and not (
                        request.destination == "draft" and merged.get("concept_id") not in current["concepts"]):
                    raise ValueError(f"义项 {sid} 不属于本术语；跨术语归属调整使用高级批次")
                senses[sid] = merged
            record["senses"] = list(senses.values())
        deletes = {(d.kind, d.id) for d in request.delete if d.kind != "senses"}
        for deletion in request.delete:
            if deletion.kind != "senses":
                continue
            owner = next((e for e in current["entries"].values()
                          if any(s["id"] == deletion.id for s in e["senses"])), None)
            if not owner:
                raise KeyError(deletion.id)
            if ("entries", owner["id"]) in deletes:
                continue
            if next(s for s in owner["senses"] if s["id"] == deletion.id)["concept_id"] not in concepts:
                raise ValueError("不能从本术语删除其他术语的义项")
            key = ("entries", owner["id"])
            record = pending.setdefault(key, {"id": owner["id"], "senses": copy.deepcopy(owner["senses"])})
            record["senses"] = [s for s in record["senses"] if s["id"] != deletion.id]
            if not record["senses"]:
                raise ValueError("删除最后一个义项须同时明确删除其语言词条")
        operations = []
        for (kind, rid), record in pending.items():
            if (kind, rid) in deletes:
                continue
            old = current[kind].get(rid)
            if old is not None and all(old.get(k) == v for k, v in record.items()):
                continue
            operations.append(Operation(kind=kind, id=rid, action="update" if old is not None else "create", values=record))
        operations.extend(Operation(action="delete", kind=kind, id=rid) for kind, rid in sorted(deletes))
        if not operations:
            # 明确保存无内容差异时仍沿用一次批次与版本更新的约定。
            operations = [Operation(kind="terms", id=identifier, action="update", values=term)]
        return ChangeBatch(expected_revision=request.expected_revision, operations=operations, message=request.message)

    def preview_term(self, request: TermSaveRequest):
        with self.locked():
            return self._candidate(self._term_batch(request))[3]

    def save_term(self, request: TermSaveRequest):
        with self.locked():
            batch = self._term_batch(request)
            draft = None
            if request.draft_id:
                draft = next((d for d in self._drafts() if d["id"] == request.draft_id), None)
                if not draft:
                    raise KeyError(request.draft_id)
            notes = draft["notes"] if draft else []
            if request.destination == "draft":
                return self._save_draft(DraftRequest(expected_revision=request.expected_revision, id=request.draft_id,
                    title=request.message, operations=batch.operations, notes=notes,
                    term_document=request.model_dump(mode="json", exclude={"expected_revision", "destination", "draft_id"})))
            if draft:
                if draft["saved_revision"] != request.expected_revision:
                    from .editing import EditingConflict
                    raise EditingConflict("草稿保存后资料已变化，请重新读取并修订草稿")
            return self._apply(batch, request.draft_id, notes)
