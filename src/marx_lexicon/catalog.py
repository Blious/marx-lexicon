"""从 YAML 读取数据，检查术语之间的引用关系。"""

import unicodedata
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import (
    Attestation, Concept, Dataset, Edition, Entry, Manifest, Profile,
    Translation, Work,
)

CATEGORIES = {
    "works": ("sources/works", Work),
    "editions": ("sources/editions", Edition),
    "entries": ("entries", Entry),
    "concepts": ("concepts", Concept),
    "translations": ("translations", Translation),
    "attestations": ("attestations", Attestation),
    "profiles": ("profiles", Profile),
}


class DataError(ValueError):
    pass


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def forms(entry: Entry) -> list[str]:
    return [entry.lemma, *(form.text for form in entry.forms)]


def load_dataset(root: Path) -> Dataset:
    data_root = root / "data"
    origins = {}
    records = {}
    errors = []
    try:
        manifest = Manifest.model_validate(yaml.safe_load(
            (data_root / "dataset.yaml").read_text(encoding="utf-8")))
    except (ValidationError, yaml.YAMLError) as error:
        raise DataError(f"data/dataset.yaml: {error}") from error
    for category, (directory, model) in CATEGORIES.items():
        items = []
        paths = sorted((data_root / directory).rglob("*.yaml"))
        if not paths:
            errors.append(f"data/{directory}: 缺少数据文件")
        for path in paths:
            name = path.relative_to(root).as_posix()
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as error:
                errors.append(f"{name}: {error}")
                continue
            if not isinstance(raw, list):
                errors.append(f"{name}: 顶层须为 YAML 列表")
                continue
            for index, record in enumerate(raw):
                try:
                    item = model.model_validate(record)
                except ValidationError as error:
                    errors.append(f"{name}[{index}]: {error}")
                    continue
                items.append(item)
                origins.setdefault(item.id, f"{name}[{index}]")
        records[category] = items
    if errors:
        raise DataError("\n".join(errors))
    dataset = Dataset(manifest=manifest, **records)
    validate_links(dataset, root, origins)
    return dataset


def validate_links(dataset: Dataset, root: Path, origins: dict[str, str]):
    errors = []
    all_ids = set()

    def fail(owner, field, message):
        errors.append(f"{origins.get(owner, owner)} ({owner}).{field}: {message}")

    def register(identifier, owner):
        if identifier in all_ids:
            fail(owner, "id", f"重复 ID {identifier}")
        all_ids.add(identifier)

    tables = {}
    for category in CATEGORIES:
        items = getattr(dataset, category)
        tables[category] = {item.id: item for item in items}
        for item in items:
            register(item.id, item.id)
    senses = {}
    for entry in dataset.entries:
        for sense in entry.senses:
            register(sense.id, entry.id)
            senses[sense.id] = (entry, sense)
        if entry.article and not (root / entry.article).is_file():
            fail(entry.id, "article", f"文件不存在：{entry.article}")
        if entry.language not in dataset.manifest.languages:
            fail(entry.id, "language", "不在 dataset.languages 中")
    tables["senses"] = senses

    def ref(owner, field, identifier, table):
        if identifier not in tables[table]:
            fail(owner, field, f"不存在的 {table} 引用 {identifier}")

    def scope(owner, value):
        for work_id in value.work_ids:
            ref(owner, "scope.work_ids", work_id, "works")
        for edition_id in value.edition_ids:
            ref(owner, "scope.edition_ids", edition_id, "editions")

    for edition in dataset.editions:
        ref(edition.id, "work_id", edition.work_id, "works")
        if edition.language not in dataset.manifest.languages:
            fail(edition.id, "language", "不在 dataset.languages 中")
    for entry in dataset.entries:
        for sense in entry.senses:
            ref(entry.id, f"senses.{sense.id}.concept_id", sense.concept_id, "concepts")
            for identifier in sense.work_ids:
                ref(entry.id, "senses.work_ids", identifier, "works")
            for identifier in sense.evidence_ids:
                ref(entry.id, "senses.evidence_ids", identifier, "attestations")
    for concept in dataset.concepts:
        for identifier in concept.evidence_ids:
            ref(concept.id, "evidence_ids", identifier, "attestations")
        for relation in concept.relations:
            ref(concept.id, "relations.target_id", relation.target_id, "concepts")
            for identifier in relation.evidence_ids:
                ref(concept.id, "relations.evidence_ids", identifier, "attestations")
    for evidence in dataset.attestations:
        local_passages = {passage.id for passage in evidence.passages}
        for passage in evidence.passages:
            register(passage.id, evidence.id)
            ref(evidence.id, "passages.edition_id", passage.edition_id, "editions")
            for mention in passage.mentions:
                ref(evidence.id, "mentions.entry_id", mention.entry_id, "entries")
                ref(evidence.id, "mentions.sense_id", mention.sense_id, "senses")
                if mention.sense_id not in senses:
                    continue
                entry, _ = senses[mention.sense_id]
                if entry.id != mention.entry_id:
                    fail(evidence.id, "mentions.sense_id", "义项不属于所指词条")
                surface = passage.text[mention.start:mention.end]
                if normalize(surface) not in {normalize(value) for value in forms(entry)}:
                    fail(evidence.id, "mentions", f"{surface!r} 未登记在 {entry.id} 的词形中")
                edition = tables["editions"].get(passage.edition_id)
                if edition and edition.language != entry.language:
                    fail(evidence.id, "mentions.entry_id", "语例版本与词条语言不符")
        for alignment in evidence.alignments:
            if not set(alignment.passage_ids) <= local_passages:
                fail(evidence.id, "alignments.passage_ids", "须指向本组语例")
            passages_by_id = {p.id: p for p in evidence.passages}
            for mention_ref in alignment.mention_refs:
                passage = passages_by_id.get(mention_ref.passage_id)
                if (mention_ref.passage_id not in alignment.passage_ids or passage is None
                        or mention_ref.mention_index >= len(passage.mentions)):
                    fail(evidence.id, "alignments.mention_refs", "须指向对应组内有效的术语标注")
    for translation in dataset.translations:
        ref(translation.id, "source_sense_id", translation.source_sense_id, "senses")
        ref(translation.id, "target_sense_id", translation.target_sense_id, "senses")
        scope(translation.id, translation.scope)
        for identifier in translation.evidence_ids:
            ref(translation.id, "evidence_ids", identifier, "attestations")
            evidence = tables["attestations"].get(identifier)
            if evidence:
                mentioned = {m.sense_id for p in evidence.passages for m in p.mentions}
                if not {translation.source_sense_id, translation.target_sense_id} <= mentioned:
                    fail(translation.id, "evidence_ids", f"{identifier} 缺少源义项或目标义项的语例")
    for profile in dataset.profiles:
        scope(profile.id, profile.scope)
        if profile.dataset_version != dataset.manifest.dataset_version:
            fail(profile.id, "dataset_version", "与数据集版本不符")
        for selection in profile.selections:
            scope(profile.id, selection.scope)
            ref(profile.id, "selections.source_sense_id", selection.source_sense_id, "senses")
            ref(profile.id, "selections.translation_id", selection.translation_id, "translations")
            translation = tables["translations"].get(selection.translation_id)
            if not translation:
                continue
            if translation.source_sense_id != selection.source_sense_id:
                fail(profile.id, "selections", "所选译法与源义项不符")
            source = senses.get(translation.source_sense_id)
            target = senses.get(translation.target_sense_id)
            if source and source[0].language != profile.source_language:
                fail(profile.id, "source_language", "与所选译法的源语言不符")
            if target and target[0].language != profile.target_language:
                fail(profile.id, "target_language", "与所选译法的目标语言不符")
    if errors:
        raise DataError("\n".join(errors))
