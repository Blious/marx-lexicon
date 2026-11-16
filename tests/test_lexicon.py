import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from marx_lexicon.catalog import DataError, load_dataset, validate_links
from marx_lexicon.cli import main
from marx_lexicon.database import build_database
from marx_lexicon.models import Alignment, MentionReference, Passage
from marx_lexicon.render import markdown
from marx_lexicon.service import Lexicon

ROOT = Path(__file__).resolve().parents[1]
CAPITAL = "edition-capital-de-mew23"
MANUSCRIPTS = "edition-manuscripts-de-mew"


@pytest.fixture(scope="session")
def database(tmp_path_factory):
    path = tmp_path_factory.mktemp("lexicon") / "sample.sqlite"
    build_database(ROOT, path)
    return path


@pytest.fixture
def lexicon(database):
    with Lexicon(database) as service:
        yield service


def prepare(lexicon, text, target="en", edition=CAPITAL, **kwargs):
    return lexicon.prepare_translation(text, source_language="de", target_language=target,
        profile_id="demo-de-zh" if target == "zh-Hans" else "demo-de-en",
        edition_id=edition, **kwargs)


def test_complete_trilingual_samples():
    dataset = load_dataset(ROOT)
    assert len(dataset.entries) == 22
    assert len(dataset.concepts) == 8
    assert len(dataset.attestations) == 7
    assert len(dataset.translations) == 17
    editions = {edition.id: edition for edition in dataset.editions}
    for item in dataset.attestations:
        assert {editions[p.edition_id].language for p in item.passages} == {"de", "zh-Hans", "en"}
        assert item.review_status == "unreviewed"
        assert all(alignment.status == "candidate" for alignment in item.alignments)


@pytest.mark.parametrize("query,language,expected", [
    ("价值", "zh-Hans", "entry-zh-Hans-jiazhi"),
    ("劳动力", "zh-Hans", "entry-zh-Hans-laodongli"),
    ("Werth", "de", "entry-de-wert"),
    ("Arbeitskräften", "de", "entry-de-arbeitskraft"),
    ("Entäusserung", "de", "entry-de-entaeusserung"),
    ("Enta\u0308ußerung", "de", "entry-de-entaeusserung"),
    ("labor-power", "en", "entry-en-labour-power"),
])
def test_lookup_forms_and_chinese_short_terms(lexicon, query, language, expected):
    assert lexicon.lookup(query, language)["results"][0]["id"] == expected


def test_full_text_query_and_literal_punctuation(lexicon):
    result = lexicon.lookup("substance", "en")
    assert "entry-en-value" in {item["id"] for item in result["results"]}
    assert lexicon.lookup('" OR NOTAWORD * %')["results"] == []
    assert lexicon.lookup("价值", "zh-Hans", limit=1)["results"][0]["id"] == "entry-zh-Hans-jiazhi"


def test_same_target_keeps_distinct_german_entries_and_evidence(lexicon):
    result = lexicon.read_entry("entry-zh-Hans-yangqi")
    ids = {entry["id"] for entry in result["linked_entries"]}
    assert {"entry-de-aufhebung", "entry-de-aufheben"} <= ids
    assert len(result["entry"]["senses"]) == 3
    assert result["article"] and "Aufheben" in result["article"]
    assert {edition["language"] for edition in result["editions"]} == {"de", "zh-Hans", "en"}
    assert "出处：" in markdown(result)
    assert "570" in markdown(result)


def test_phrase_inflection_boundaries_and_original_offsets(lexicon):
    text = "Mehrwert und Gebrauchswert; Arbeitskräften, abstrakten\n Arbeit und Wert."
    result = prepare(lexicon, text)
    assert [item["lemma"] for item in result["occurrences"]] == ["Arbeitskraft", "abstrakte Arbeit", "Wert"]
    for item in result["occurrences"]:
        assert text[item["start"]:item["end"]] == item["surface"]
        assert item["recommendation"] is not None


def test_longest_phrase_suppresses_registered_shorter_overlap(lexicon):
    entry = lexicon.entries["entry-de-abstrakte-arbeit"].model_copy(deep=True)
    entry.id = "entry-de-arbeit-support"
    entry.lemma = "Arbeit"
    entry.forms = []
    lexicon.dataset.entries.append(entry)
    lexicon.entries[entry.id] = entry
    result = prepare(lexicon, "abstrakte Arbeit")
    assert len(result["occurrences"]) == 1
    assert result["occurrences"][0]["entry_id"] == "entry-de-abstrakte-arbeit"


def test_trilingual_translation_profile_and_english_alternative(lexicon):
    text = (ROOT / "examples/economics.de.txt").read_text(encoding="utf-8")
    english = prepare(lexicon, text)
    chinese = prepare(lexicon, text, "zh-Hans")
    assert [item["recommendation"]["text"] for item in english["occurrences"]] == ["labour-power", "abstract labour", "value"]
    assert [item["recommendation"]["text"] for item in chinese["occurrences"]] == ["劳动力", "抽象劳动", "价值"]
    alternatives = english["occurrences"][0]["candidates"][0]["translations"]
    assert any(item["target"]["lemma"] == "capacity for labour" and not item["selected_by_profile"] for item in alternatives)
    evidence = next(item for item in english["attestations"] if item["id"] == "att-labour-power")
    assert "第四章第三节" in evidence["passages"][0]["location"]["section"]
    assert "Chapter 6" in evidence["passages"][2]["location"]["section"]


def test_ambiguous_senses_and_explicit_selection(lexicon):
    text = (ROOT / "examples/philosophy.de.txt").read_text(encoding="utf-8")
    result = prepare(lexicon, text, edition=MANUSCRIPTS)
    aufhebung = next(item for item in result["occurrences"] if item["lemma"] == "Aufhebung")
    assert aufhebung["recommendation"] is None
    assert len(aufhebung["candidates"]) == 2
    assert aufhebung["unresolved"]
    selected = prepare(lexicon, text, edition=MANUSCRIPTS, sense_id="sense-de-aufhebung-aufhebung-property")
    choices = {item["lemma"]: item["recommendation"]["text"] for item in selected["occurrences"]}
    assert choices == {"Entfremdung": "estrangement", "Entäußerung": "alienation", "Aufhebung": "transcendence", "Aufheben": "superseding"}


def test_missing_context_preserves_conditional_candidates(lexicon):
    result = prepare(lexicon, "Wert", edition=None)
    item = result["occurrences"][0]
    assert item["recommendation"] is None
    candidate = item["candidates"][0]["translations"][0]
    assert candidate["target"]["lemma"] == "value"
    assert candidate["applicability"] == "needs_context"


def test_wrong_profile_direction_and_work_edition_mismatch(lexicon):
    with pytest.raises(ValueError, match="语言方向"):
        lexicon.prepare_translation("Wert", source_language="de", target_language="en", profile_id="demo-de-zh")
    with pytest.raises(ValueError, match="作品与版本"):
        prepare(lexicon, "Wert", work_id="work-manuscripts-1844")


def test_scoped_glossary_includes_alternatives_and_sources(lexicon):
    result = lexicon.export_glossary("demo-de-en")
    assert len(result["rows"]) == 9
    assert sum(row["selected_by_profile"] for row in result["rows"]) == 8
    assert all(row["scope"]["edition_ids"] and row["evidence_ids"] for row in result["rows"])
    rendered = markdown(result)
    assert "capacity for labour" in rendered and "https://" in rendered


def test_invalid_reference_reports_origin_and_field():
    dataset = load_dataset(ROOT)
    item = dataset.translations[0]
    item.target_sense_id = "missing-sense"
    with pytest.raises(DataError, match=r"translations/example.yaml.*target_sense_id.*missing-sense"):
        validate_links(dataset, ROOT, {item.id: "data/translations/example.yaml[0]"})


def test_duplicate_id_and_invalid_span_are_rejected():
    dataset = load_dataset(ROOT)
    dataset.entries.append(dataset.entries[0].model_copy(deep=True))
    with pytest.raises(DataError, match="重复 ID"):
        validate_links(dataset, ROOT, {})
    raw = dataset.attestations[0].passages[0].model_dump()
    raw["mentions"][0]["end"] = len(raw["text"]) + 1
    with pytest.raises(ValidationError, match="超出引文"):
        Passage.model_validate(raw)


def test_term_alignment_requires_explicit_valid_mentions():
    with pytest.raises(ValidationError, match="至少两处"):
        Alignment(passage_ids=["one", "two"], level="term", note="测试")
    dataset = load_dataset(ROOT)
    evidence = next(item for item in dataset.attestations if item.id == "att-alienation-pair")
    passages = evidence.passages[:2]
    evidence.alignments.append(Alignment(
        passage_ids=[p.id for p in passages], level="term", note="测试多对多标注引用",
        mention_refs=[MentionReference(passage_id=p.id, mention_index=index) for p in passages for index in (0, 1)]))
    validate_links(dataset, ROOT, {})
    evidence.alignments[-1].mention_refs[0].mention_index = 99
    with pytest.raises(DataError, match="mention_refs"):
        validate_links(dataset, ROOT, {})


def test_cli_exports_unicode_files_and_reports_unknown_id(database, tmp_path, capsys):
    output = tmp_path / "术语.json"
    assert main(["--database", str(database), "lookup", "价值", "--language", "zh-Hans", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["results"][0]["lemma"] == "价值"
    assert main(["--database", str(database), "read-entry", "missing-entry"]) == 1
    assert "missing-entry" in capsys.readouterr().err
