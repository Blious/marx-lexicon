"""可编辑数据的模型；JSON Schema 从这些定义导出。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Manifest(Model):
    schema_version: Literal["2"] = "2"
    dataset_version: str
    title: str
    languages: list[str]
    description: str


class Work(Model):
    id: str
    title: str
    author: str
    written: str
    original_language: str
    note: str = ""


class Edition(Model):
    id: str
    work_id: str
    language: str
    title: str
    kind: Literal["original", "translation"]
    citation: str
    url: str
    translators: list[str] = Field(default_factory=list)
    publication_year: str | None = None
    note: str = ""


class Form(Model):
    text: str
    kind: Literal["inflected", "historical", "variant"]
    note: str = ""


class Sense(Model):
    id: str
    definition: str
    concept_id: str
    usage: str
    work_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["draft", "reviewed"] = "draft"


class Entry(Model):
    id: str
    language: str
    lemma: str
    part_of_speech: Literal["noun", "verb", "phrase", "adjective"]
    forms: list[Form] = Field(default_factory=list)
    senses: list[Sense] = Field(min_length=1)
    article: str | None = None


class Relation(Model):
    kind: Literal["related", "distinct", "broader", "narrower", "possible_equivalent"]
    target_id: str
    explanation: str
    evidence_ids: list[str] = Field(min_length=1)
    status: Literal["draft", "reviewed"] = "draft"


class Concept(Model):
    id: str
    labels: dict[str, str]
    definition: str
    evidence_ids: list[str] = Field(min_length=1)
    relations: list[Relation] = Field(default_factory=list)


class Scope(Model):
    work_ids: list[str] = Field(default_factory=list)
    edition_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Translation(Model):
    id: str
    source_sense_id: str
    target_sense_id: str
    scope: Scope
    evidence_ids: list[str] = Field(min_length=1)
    note: str
    status: Literal["draft", "reviewed"] = "draft"


class Location(Model):
    section: str
    url: str
    printed_page: str | None = None
    manuscript_page: str | None = None
    precision: Literal["page", "section", "quote"]
    note: str = ""


class Mention(Model):
    entry_id: str
    sense_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Passage(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    id: str
    edition_id: str
    text: str
    location: Location
    speaker: str
    material: Literal["body", "heading", "editorial", "quotation"] = "body"
    mentions: list[Mention] = Field(min_length=1)

    @model_validator(mode="after")
    def check_spans(self):
        for mention in self.mentions:
            if not 0 <= mention.start < mention.end <= len(self.text):
                raise ValueError(f"{self.id}: 术语位置超出引文范围")
        return self


class MentionReference(Model):
    passage_id: str
    mention_index: int = Field(ge=0)


class Alignment(Model):
    passage_ids: list[str] = Field(min_length=2)
    mention_refs: list[MentionReference] = Field(default_factory=list)
    level: Literal["passage", "term"]
    note: str
    status: Literal["candidate", "reviewed"] = "candidate"

    @model_validator(mode="after")
    def require_term_refs(self):
        if self.level == "term" and len(self.mention_refs) < 2:
            raise ValueError("词语对应须明确指向至少两处术语标注")
        return self


class ExternalReference(Model):
    provider: str
    evidence_id: str
    version: str | None = None
    url: str | None = None


class Attestation(Model):
    id: str
    note: str
    passages: list[Passage] = Field(min_length=1)
    alignments: list[Alignment] = Field(default_factory=list)
    external_refs: list[ExternalReference] = Field(default_factory=list)
    review_status: Literal["unreviewed", "reviewed"] = "unreviewed"


class Selection(Model):
    source_sense_id: str
    translation_id: str
    scope: Scope = Field(default_factory=Scope)
    note: str = ""


class Profile(Model):
    id: str
    title: str
    source_language: str
    target_language: str
    dataset_version: str
    scope: Scope = Field(default_factory=Scope)
    selections: list[Selection]
    note: str


class Term(Model):
    id: str
    title: str
    summary: str = ""
    concept_ids: list[str] = Field(min_length=1)
    article: str | None = None


class Dataset(Model):
    manifest: Manifest
    terms: list[Term]
    works: list[Work]
    editions: list[Edition]
    entries: list[Entry]
    concepts: list[Concept]
    translations: list[Translation]
    attestations: list[Attestation]
    profiles: list[Profile]
