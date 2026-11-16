"""对本地规范源文件执行整批编辑、草稿保存及引用检查。"""

import base64
import copy
import json
import os
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml
from filelock import FileLock, Timeout
from pydantic import ValidationError

from .catalog import CATEGORIES, DataError, load_dataset, validate_links
from .database import build_database
from .edit_models import ChangeBatch, DraftRequest, ImportRequest
from .models import Dataset


class EditingConflict(ValueError):
    """资料已变化或存在并发保存。"""


class InvalidChanges(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("批次未通过校验，请修复错误或保存为编辑草稿")


def dump(value):
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)


def errors_for(error):
    if isinstance(error, ValidationError):
        return [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in error.errors()]
    return [{"field": "references", "message": line} for line in str(error).splitlines()]


def walk(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}.{index}")
    else:
        yield path, value


def reset_review(value, previous=None):
    """变更内容回到待审阅状态，未改动的嵌套记录保留审阅结果。"""
    if value == previous:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "status":
                value[key] = "candidate" if "passage_ids" in value else "draft"
            elif key == "review_status":
                value[key] = "unreviewed"
            else:
                reset_review(child, previous.get(key) if isinstance(previous, dict) else None)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            old = None
            if isinstance(previous, list):
                if isinstance(child, dict) and "id" in child:
                    old = next((p for p in previous if isinstance(p, dict) and p.get("id") == child["id"]), None)
                elif index < len(previous):
                    old = previous[index]
            reset_review(child, old)


def remap_mentions(old, new):
    """未显式修改对应组时，按原术语标注身份迁移索引。"""
    old_passages = {p["id"]: p for p in old["passages"]}
    new_passages = {p["id"]: p for p in new["passages"]}
    for alignment in new.get("alignments", []):
        for ref in alignment.get("mention_refs", []):
            before = old_passages.get(ref["passage_id"])
            after = new_passages.get(ref["passage_id"])
            if not before or not after:
                raise ValueError("删除了被对应组引用的段落；请在同一批次修改 alignments")
            mention = before["mentions"][ref["mention_index"]]
            matches = [i for i, item in enumerate(after["mentions"]) if item == mention]
            if len(matches) != 1:
                raise ValueError("删除或改变了被对应组引用的术语标注；请显式修改 alignments")
            ref["mention_index"] = matches[0]


class Editor:
    def __init__(self, root: Path, database: Path):
        self.root = root.resolve()
        self.database = database.resolve()
        if not (self.root / "data/dataset.yaml").is_file():
            raise ValueError("编辑模式需要包含 data/dataset.yaml 的本地仓库，独立数据库只支持查阅")
        self.runtime = self.root / "build/editor"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.runtime / "write.lock"), timeout=5)
        with self.locked():
            pass

    @staticmethod
    def _atomic(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".writing")
        temporary.write_bytes(content)
        os.replace(temporary, path)

    @contextmanager
    def locked(self):
        try:
            with self.lock:
                self._recover()
                yield
        except Timeout as error:
            raise EditingConflict("另一个编辑正在保存，请稍后重试") from error

    def _recover(self):
        path = self.runtime / "transaction.json"
        if not path.exists():
            return
        transaction = json.loads(path.read_text(encoding="utf-8"))
        for name, encoded in transaction["before"].items():
            target = self.root / name
            if encoded is None:
                target.unlink(missing_ok=True)
            else:
                self._atomic(target, base64.b64decode(encoded))
        if transaction["published"]:
            build_database(self.root, self.database)
        path.unlink()

    def _revision(self):
        path = self.root / "data/editor-state.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))["revision"] if path.exists() else 0

    def _expect(self, revision):
        if revision != self._revision():
            raise EditingConflict(f"编辑版本已变化：请求 {revision}，当前 {self._revision()}；请重新读取")

    def _records(self):
        dataset = load_dataset(self.root)
        records = {kind: {item.id: item.model_dump(mode="json") for item in getattr(dataset, kind)}
                   for kind in CATEGORIES}
        records["articles"] = {p.relative_to(self.root).as_posix(): {"id": p.relative_to(self.root).as_posix(),
                               "content": p.read_text(encoding="utf-8")}
                               for p in (self.root / "articles").rglob("*.md")}
        return dataset, records

    def _auxiliary(self, directory):
        return [yaml.safe_load(p.read_text(encoding="utf-8"))
                for p in sorted((self.root / "data" / directory).glob("*.yaml"))]

    def _drafts(self):
        return self._auxiliary("drafts")

    def read(self, kind="entries", identifiers=None, offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 须非负，limit 须在 1—100 之间")
        with self.locked():
            dataset, records = self._records()
            values = self._auxiliary(kind.replace("_", "-")) if kind in ("drafts", "import_notes") else list(records[kind].values())
            if identifiers is not None:
                table = {item["id"]: item for item in values}
                values = [table[identifier] for identifier in identifiers]
            return {"revision": self._revision(), "dataset_version": dataset.manifest.dataset_version,
                    "storage": "local", "kind": kind, "total": len(values),
                    "offset": offset, "limit": limit, "records": values[offset:offset + limit]}

    def schema(self, kind=None):
        schemas = {name: model.model_json_schema() for name, (_, model) in CATEGORIES.items()}
        schemas["articles"] = {"type": "object", "required": ["id", "content"], "properties": {
            "id": {"type": "string", "description": "articles/ 下的 .md 路径"}, "content": {"type": "string"}}}
        return {"revision": self._revision(), "schema": schemas[kind] if kind else ChangeBatch.model_json_schema(),
                "kinds": [*CATEGORIES, "articles"], "rules": "update 仅修改给出的字段；数组整体替换；删除不级联；review 为显式学术审阅。"}

    def references(self, kind, identifier):
        with self.locked():
            _, records = self._records()
            record = records[kind].pop(identifier)
            targets = {v for p, v in walk(record) if p.split(".")[-1] == "id"}
            return {"revision": self._revision(), "references": self._references(records, targets)}

    def _references(self, records, deleted):
        refs = []
        for kind, items in records.items():
            for identifier, record in items.items():
                for field, value in walk(record):
                    if field.split(".")[-1] != "id" and isinstance(value, str) and value in deleted:
                        refs.append({"kind": kind, "id": identifier, "field": field, "target": value})
        return refs

    def _candidate(self, batch):
        self._expect(batch.expected_revision)
        dataset, before = self._records()
        records = copy.deepcopy(before)
        errors, changed, deleted = [], [], set()
        seen = set()
        for index, op in enumerate(batch.operations):
            try:
                key = (op.kind, op.id)
                if key in seen:
                    raise ValueError("同一批次每个资源只允许一条操作；请合并其修改")
                seen.add(key)
                if op.kind == "articles":
                    article = Path(op.id)
                    if article.is_absolute() or ".." in article.parts or not op.id.startswith("articles/") or article.suffix != ".md":
                        raise ValueError("文章 ID 须为 articles/ 下的相对 .md 路径")
                    if op.action in ("create", "update") and (set(op.values) - {"id", "content"}
                            or ("content" in op.values and not isinstance(op.values["content"], str))):
                        raise ValueError("文章仅接受字符串 content 和稳定 id")
                    if op.action == "create" and "content" not in op.values:
                        raise ValueError("文章缺少 content")
                table = records[op.kind]
                old = table.get(op.id)
                if "id" in op.values and op.values["id"] != op.id:
                    raise ValueError("稳定 ID 不可修改")
                if op.action == "create":
                    if old is not None:
                        raise ValueError("ID 已存在；同名术语请使用独立 ID，或显式 update")
                    value = {**copy.deepcopy(op.values), "id": op.id}
                    reset_review(value)
                    table[op.id] = value
                else:
                    if old is None:
                        raise ValueError("ID 不存在")
                    if op.action == "delete":
                        deleted.update(v for p, v in walk(old) if p.split(".")[-1] == "id")
                        del table[op.id]
                    elif op.action == "review":
                        for field, value in list(walk(old)):
                            if field.split(".")[-1] in ("status", "review_status"):
                                parent = old
                                parts = field.split(".")
                                for part in parts[:-1]:
                                    parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                                parent[parts[-1]] = "reviewed"
                    else:
                        value = {**copy.deepcopy(old), **copy.deepcopy(op.values)}
                        if op.kind == "attestations":
                            CATEGORIES[op.kind][1].model_validate(value)
                        if op.kind == "attestations" and "passages" in op.values and ("alignments" not in op.values or op.values["alignments"] == old.get("alignments", [])):
                            remap_mentions(old, value)
                        reset_review(value, old)
                        if op.kind == "entries" and any(value.get(k) != old.get(k) for k in ("lemma", "forms", "language", "part_of_speech")):
                            reset_review(value)
                        if op.kind == "attestations" and value != old:
                            for alignment in value.get("alignments", []):
                                alignment["status"] = "candidate"
                        table[op.id] = value
                changed.append({"action": op.action, "kind": op.kind, "id": op.id})
            except ValidationError as error:
                errors.extend({"operation": index, "id": op.id, **item} for item in errors_for(error))
            except (ValueError, KeyError) as error:
                errors.append({"operation": index, "id": op.id, "field": op.kind, "message": str(error)})
        before_ids = {v for items in before.values() for record in items.values() for p, v in walk(record) if p.split(".")[-1] == "id" and isinstance(v, str)}
        after_ids = {v for items in records.values() for record in items.values() for p, v in walk(record) if p.split(".")[-1] == "id" and isinstance(v, str)}
        deleted.update(before_ids - after_ids)
        references = self._references(records, deleted)
        for ref in references:
            errors.append({**ref, "message": "被删除的资源仍被引用，请在本批次显式修复"})
        candidate = None
        if not errors:
            try:
                candidate = Dataset(manifest=dataset.manifest, **{k: list(records[k].values()) for k in CATEGORIES})
                validate_links(candidate, self.root, {}, set(records["articles"]))
            except (ValidationError, DataError) as error:
                errors.extend(errors_for(error))
        report = {"valid": not errors, "revision": self._revision(),
                  "dataset_version": dataset.manifest.dataset_version, "counts": dict(Counter(c["action"] for c in changed)),
                  "changes": changed, "errors": errors, "references": references}
        return candidate, before, records, report

    def preview(self, batch: ChangeBatch):
        with self.locked():
            return self._candidate(batch)[3]

    def _source_changes(self, before, after):
        changes = {}
        for kind, (directory, _) in CATEGORIES.items():
            remaining = {key for key, value in after[kind].items() if before[kind].get(key) != value}
            for path in sorted((self.root / "data" / directory).rglob("*.yaml")):
                items = yaml.safe_load(path.read_text(encoding="utf-8"))
                touched = any(item["id"] in remaining or item["id"] not in after[kind] for item in items)
                new = [after[kind][item["id"]] if item["id"] in remaining else item
                       for item in items if item["id"] in after[kind]] if touched else items
                remaining.difference_update(item["id"] for item in items)
                if items != new:
                    changes[path.relative_to(self.root).as_posix()] = dump(new).encode()
            if remaining:
                name = f"data/{directory}/managed.yaml"
                existing = yaml.safe_load((self.root / name).read_text(encoding="utf-8")) if (self.root / name).exists() else []
                # managed.yaml 也可能已经在上述循环中修改。
                current = yaml.safe_load(changes[name]) if name in changes else existing
                current.extend(after[kind][identifier] for identifier in sorted(remaining))
                changes[name] = dump(current).encode()
        for identifier in before["articles"].keys() | after["articles"].keys():
            if before["articles"].get(identifier) != after["articles"].get(identifier):
                changes[identifier] = (after["articles"][identifier]["content"].encode()
                                       if identifier in after["articles"] else None)
        return changes

    def _persist(self, changes, published):
        revision = self._revision() + 1
        changes["data/editor-state.yaml"] = dump({"revision": revision}).encode()
        transaction = {"before": {name: base64.b64encode((self.root / name).read_bytes()).decode()
                                   if (self.root / name).exists() else None for name in changes},
                       "published": published}
        journal = self.runtime / "transaction.json"
        self._atomic(journal, json.dumps(transaction).encode())
        try:
            for name, content in changes.items():
                if content is None:
                    (self.root / name).unlink(missing_ok=True)
                else:
                    self._atomic(self.root / name, content)
            if published:
                build_database(self.root, self.database)
            journal.unlink()
        except Exception:
            self._recover()
            raise
        return revision

    def _apply(self, batch, draft_id=None, notes=None):
        candidate, before, after, report = self._candidate(batch)
        if not report["valid"]:
            raise InvalidChanges(report)
        version = candidate.manifest.dataset_version.split(".")
        version[-1] = str(int(version[-1]) + 1)
        candidate.manifest.dataset_version = ".".join(version)
        for profile in after["profiles"].values():
            profile["dataset_version"] = candidate.manifest.dataset_version
        changes = self._source_changes(before, after)
        changes["data/dataset.yaml"] = dump(candidate.manifest.model_dump(mode="json")).encode()
        if draft_id:
            changes[f"data/drafts/{draft_id}.yaml"] = None
        notes_id = None
        if notes:
            notes_id = f"import-{uuid4().hex}"
            changes[f"data/import-notes/{notes_id}.yaml"] = dump({"id": notes_id, "title": batch.message,
                "notes": notes, "resources": report["changes"]}).encode()
        revision = self._persist(changes, True)
        return {"local_saved": True, "revision": revision, "dataset_version": candidate.manifest.dataset_version,
                "counts": report["counts"], "changes": report["changes"], "storage": "local",
                **({"import_notes_id": notes_id} if notes_id else {})}

    def apply(self, batch: ChangeBatch):
        with self.locked():
            return self._apply(batch)

    def _save_draft(self, request):
        self._expect(request.expected_revision)
        drafts = {d["id"]: d for d in self._drafts()}
        identifier = request.id or f"draft-{uuid4().hex}"
        if request.id and request.id not in drafts:
            raise KeyError(request.id)
        previous = drafts.get(identifier)
        record = {"id": identifier, "title": request.title, "notes": request.notes,
                  "base_revision": previous["base_revision"] if previous else self._revision(),
                  "operations": [op.model_dump(mode="json") for op in request.operations]}
        # 草稿修改的 expected_revision 是人工重读后的明确重定基准。
        if previous:
            record["base_revision"] = self._revision()
        record["saved_revision"] = self._revision() + 1
        validation = self._candidate(ChangeBatch(expected_revision=self._revision(), operations=request.operations))[3] if request.operations else {"valid": False, "errors": [{"message": "空草稿"}]}
        record["validation"] = validation
        revision = self._persist({f"data/drafts/{identifier}.yaml": dump(record).encode()}, False)
        return {"local_saved": True, "id": identifier, "revision": revision, "validation": validation, "storage": "local"}

    def save_draft(self, request: DraftRequest):
        with self.locked():
            return self._save_draft(request)

    def publish_draft(self, identifier, expected_revision):
        with self.locked():
            self._expect(expected_revision)
            draft = next((d for d in self._drafts() if d["id"] == identifier), None)
            if not draft:
                raise KeyError(identifier)
            if draft["saved_revision"] != expected_revision:
                raise EditingConflict("草稿保存后资料已变化；请重新读取、修订并保存草稿，再发布")
            return self._apply(ChangeBatch(expected_revision=expected_revision, operations=draft["operations"],
                                          message="发布编辑草稿：" + draft["title"]), identifier, draft["notes"])

    def delete_draft(self, identifier, expected_revision):
        with self.locked():
            self._expect(expected_revision)
            if identifier not in {d["id"] for d in self._drafts()}:
                raise KeyError(identifier)
            revision = self._persist({f"data/drafts/{identifier}.yaml": None}, False)
            return {"local_saved": True, "id": identifier, "revision": revision, "storage": "local"}

    def import_records(self, request: ImportRequest):
        from .importing import parse_import
        with self.locked():
            self._expect(request.expected_revision)
            _, records = self._records()
            operations, notes = parse_import(request, records)
            batch = ChangeBatch(expected_revision=request.expected_revision, operations=operations, message=request.title)
            report = {**self._candidate(batch)[3], "notes": notes}
            if request.include_operations:
                report["operations"] = [op.model_dump(mode="json") for op in operations]
            if request.destination == "draft":
                return self._save_draft(DraftRequest(expected_revision=request.expected_revision, title=request.title,
                                                    operations=operations, notes=notes))
            if request.destination == "publish":
                return self._apply(batch, notes=notes)
            return report
