"""所有写入都使用临时源目录，服务从不调用 Git。"""

import asyncio
import base64
import copy
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml
from filelock import FileLock
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from starlette.testclient import TestClient

from marx_lexicon.catalog import load_dataset
from marx_lexicon.database import build_database
from marx_lexicon.edit_models import ChangeBatch, DraftRequest, ImportRequest
from marx_lexicon.editing import Editor, EditingConflict, InvalidChanges
from marx_lexicon.exporting import TBX_NS, export_file
from marx_lexicon.service import Lexicon, VersionMismatch
from marx_lexicon.web import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def editor(tmp_path):
    root = tmp_path / "source"
    for name in ("data", "articles"):
        shutil.copytree(ROOT / name, root / name)
    database = root / "build/lexicon.sqlite"
    build_database(root, database)
    return Editor(root, database)


def op(kind, identifier, action="update", **values):
    return {"kind": kind, "id": identifier, "action": action, "values": values}


def batch(editor, *operations, revision=None):
    return ChangeBatch(expected_revision=editor.read()["revision"] if revision is None else revision, operations=operations)


def new_work(identifier="work-test"):
    return op("works", identifier, "create", title="测试作品", author="作者", written="1844", original_language="de")


def cyclic_records():
    return [op("entries", "entry-test", "create", language="de", lemma="Testwort", part_of_speech="noun",
               senses=[{"id": "sense-test", "definition": "定义", "usage": "用法", "concept_id": "concept-test", "evidence_ids": ["att-test"]}]),
            op("concepts", "concept-test", "create", labels={"de": "Testwort"}, definition="概念", evidence_ids=["att-test"]),
            op("attestations", "att-test", "create", note="测试语例", passages=[{
                "id": "passage-test", "edition_id": "edition-capital-de-mew23", "text": "Testwort",
                "speaker": "作者", "location": {"section": "测试", "url": "https://example.org/test", "precision": "quote"},
                "mentions": [{"entry_id": "entry-test", "sense_id": "sense-test", "start": 0, "end": 8}]}])]


def test_crud_local_save_preserves_omitted_fields_and_profiles(editor, monkeypatch):
    def no_git(*args, **kwargs):
        pytest.fail("保存不得启动外部命令，包括 Git")
    monkeypatch.setattr(subprocess, "run", no_git)
    untouched = (editor.root / "data/entries/de.yaml").read_bytes()
    result = editor.apply(batch(editor, new_work()))
    assert result["local_saved"] and result["storage"] == "local" and result["revision"] == 1
    assert result["dataset_version"] == "0.2.1"
    assert (editor.root / "data/entries/de.yaml").read_bytes() == untouched
    editor.apply(batch(editor, op("works", "work-test", title="修改标题")))
    record = editor.read("works", ["work-test"])["records"][0]
    assert record["author"] == "作者" and record["title"] == "修改标题"
    dataset = load_dataset(editor.root)
    assert all(p.dataset_version == "0.2.2" for p in dataset.profiles)
    editor.apply(batch(editor, op("works", "work-test", "delete")))
    assert "work-test" not in [w.id for w in load_dataset(editor.root).works]
    assert not (editor.runtime / "transaction.json").exists()


def test_cyclic_batch_and_no_homonym_merge(editor):
    result = editor.apply(batch(editor, *cyclic_records()))
    assert result["counts"] == {"create": 3}
    with Lexicon(editor.database) as lexicon:
        assert lexicon.lookup("Testwort")["results"][0]["id"] == "entry-test"
        assert "entry-test" in {e["id"] for e in json.loads(export_file(lexicon).content)["dataset"]["entries"]}
    record = editor.read("entries", ["entry-test"])["records"][0]
    record["senses"][0]["id"] = "sense-homonym"
    editor.apply(batch(editor, op("entries", "entry-homonym", "create", **{k: v for k, v in record.items() if k != "id"})))
    with Lexicon(editor.database) as lexicon:
        assert {r["id"] for r in lexicon.lookup("Testwort")["results"]} == {"entry-test", "entry-homonym"}


def test_reference_delete_blocked_and_repaired_in_same_batch(editor):
    editor.apply(batch(editor, *cyclic_records()))
    request = batch(editor, op("entries", "entry-test", "delete"), new_work())
    report = editor.preview(request)
    assert not report["valid"] and any(r["id"] == "att-test" for r in report["references"])
    with pytest.raises(InvalidChanges):
        editor.apply(request)
    assert editor.read()["revision"] == 1
    with pytest.raises(KeyError):
        editor.read("works", ["work-test"])
    editor.apply(batch(editor, *(op(k, i, "delete") for k, i in [
        ("entries", "entry-test"), ("concepts", "concept-test"), ("attestations", "att-test")])))
    assert editor.read()["revision"] == 2
    refs = editor.references("entries", "entry-de-wert")["references"]
    assert any(r["target"].startswith("sense-") for r in refs)


def test_version_conflict_and_immutable_id(editor):
    stale = batch(editor, new_work("work-stale"))
    editor.apply(batch(editor, new_work()))
    with pytest.raises(EditingConflict):
        editor.apply(stale)
    invalid = batch(editor, {"kind": "works", "id": "work-test", "action": "update", "values": {"id": "work-renamed"}})
    assert not editor.preview(invalid)["valid"]
    with Lexicon(editor.database) as lexicon, pytest.raises(VersionMismatch):
        lexicon.read_evidence(["att-value"], dataset_version="0.2.0")


def test_editing_drafts_stay_out_of_published_data_then_publish(editor):
    draft = editor.save_draft(DraftRequest(expected_revision=0, title="未完资料", operations=[
        op("works", "work-draft", "create", title="只有标题")]))
    assert not draft["validation"]["valid"]
    assert editor.read()["dataset_version"] == "0.2.0"
    assert "work-draft" not in {w.id for w in load_dataset(editor.root).works}
    with pytest.raises(InvalidChanges):
        editor.publish_draft(draft["id"], 1)
    complete = new_work("work-draft")
    saved = editor.save_draft(DraftRequest(expected_revision=1, id=draft["id"], operations=[complete]))
    assert saved["validation"]["valid"]
    result = editor.publish_draft(draft["id"], 2)
    assert result["dataset_version"] == "0.2.1" and editor.read("drafts")["total"] == 0


def test_stale_draft_needs_explicit_resave_and_can_be_deleted(editor):
    draft = editor.save_draft(DraftRequest(expected_revision=0, operations=[new_work()]))
    editor.apply(batch(editor, new_work("work-other")))
    with pytest.raises(EditingConflict):
        editor.publish_draft(draft["id"], 2)
    editor.delete_draft(draft["id"], 2)
    assert editor.read("drafts")["total"] == 0 and editor.read()["dataset_version"] == "0.2.1"


def test_import_notes_survive_draft_publication(editor):
    request = DraftRequest(expected_revision=0, operations=[new_work()], notes=["未核对的原始来源说明"])
    saved = editor.save_draft(request)
    result = editor.publish_draft(saved["id"], 1)
    note = editor.read("import_notes", [result["import_notes_id"]])["records"][0]
    assert note["notes"] == request.notes
    assert "import_notes" not in load_dataset(editor.root).model_dump()


def test_build_recovers_interrupted_editor_before_loading_sources(editor):
    from marx_lexicon.cli import main
    manifest = editor.root / "data/dataset.yaml"
    (editor.runtime / "transaction.json").write_text(json.dumps({"before": {
        "data/dataset.yaml": base64.b64encode(manifest.read_bytes()).decode()}, "published": True}), encoding="utf-8")
    manifest.write_text("interrupted: true", encoding="utf-8")
    assert main(['--root',str(editor.root),'build']) == 0
    assert load_dataset(editor.root).manifest.dataset_version == "0.2.0"


def test_review_is_explicit_and_semantic_edit_resets_it(editor):
    editor.apply(batch(editor, op("entries", "entry-de-wert", "review")))
    entry = editor.read("entries", ["entry-de-wert"])["records"][0]
    assert entry["senses"][0]["status"] == "reviewed"
    entry["senses"][0]["definition"] += "（修订）"
    editor.apply(batch(editor, op("entries", entry["id"], senses=entry["senses"])))
    assert editor.read("entries", [entry["id"]])["records"][0]["senses"][0]["status"] == "draft"


def test_invalid_nested_input_remains_a_draft_and_preview_does_not_mutate(editor):
    change = batch(editor, op("attestations", "att-value", passages=None, alignments=None))
    assert not editor.preview(change)["valid"]
    saved = editor.save_draft(DraftRequest(expected_revision=0, operations=change.operations))
    assert not saved["validation"]["valid"]
    request = batch(editor, new_work())
    before = request.model_dump(mode="json")
    editor.preview(request)
    assert request.model_dump(mode="json") == before


def test_mention_reordering_remaps_alignments_and_removal_is_blocked(editor):
    evidence = next(a.model_dump(mode="json") for a in load_dataset(editor.root).attestations
                    if any(len(p.mentions) > 1 for p in a.passages))
    passage = next(p for p in evidence["passages"] if len(p["mentions"]) > 1)
    second = next(p for p in evidence["passages"] if p["id"] != passage["id"])
    evidence["alignments"] = [{"passage_ids": [passage["id"], second["id"]], "level": "term", "note": "已登记对应", "status": "candidate", "mention_refs": [
        {"passage_id": passage["id"], "mention_index": 0}, {"passage_id": second["id"], "mention_index": 0}]}]
    editor.apply(batch(editor, op("attestations", evidence["id"], **{k:v for k,v in evidence.items() if k!="id"})))
    passages = copy.deepcopy(evidence["passages"])
    target = next(p for p in passages if p["id"] == passage["id"])
    original = target["mentions"][0]
    target["mentions"].reverse()
    editor.apply(batch(editor, op("attestations", evidence["id"], passages=passages)))
    record = editor.read("attestations", [evidence["id"]])["records"][0]
    index = record["alignments"][0]["mention_refs"][0]["mention_index"]
    assert next(p for p in record["passages"] if p["id"] == passage["id"])["mentions"][index] == original
    target["mentions"].remove(original)
    assert not editor.preview(batch(editor, op("attestations", evidence["id"], passages=passages)))["valid"]


def test_failed_database_rebuild_rolls_back_sources_and_revision(editor, monkeypatch):
    import marx_lexicon.editing as module
    actual = module.build_database
    calls = []
    def fail_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("测试写入失败")
        return actual(*args)
    monkeypatch.setattr(module, "build_database", fail_once)
    with pytest.raises(OSError):
        editor.apply(batch(editor, new_work()))
    assert editor.read()["revision"] == 0 and editor.read()["dataset_version"] == "0.2.0"
    assert not (editor.root / "data/sources/works/managed.yaml").exists()


def test_interrupted_transaction_recovers_and_lock_blocks_concurrent_save(editor):
    manifest = editor.root / "data/dataset.yaml"
    original = manifest.read_bytes()
    (editor.runtime / "transaction.json").write_text(json.dumps({"before": {
        "data/dataset.yaml": base64.b64encode(original).decode()}, "published": True}), encoding="utf-8")
    manifest.write_text("broken: true", encoding="utf-8")
    assert editor.read()["dataset_version"] == "0.2.0"
    competing = Editor(editor.root, editor.database)
    with FileLock(str(editor.runtime / "write.lock")):
        competing.lock.timeout = 0
        with pytest.raises(EditingConflict):
            competing.apply(ChangeBatch(expected_revision=0, operations=[new_work()]))


@pytest.mark.parametrize("format", ["json", "yaml"])
def test_native_import_snapshot_modes_and_duplicate_ids(editor, format):
    with Lexicon(editor.database) as lexicon:
        snapshot = json.loads(export_file(lexicon).content)
    content = json.dumps(snapshot) if format == "json" else yaml.safe_dump(snapshot, allow_unicode=True)
    request = ImportRequest(expected_revision=0, format=format, content=content, mode="upsert")
    preview = editor.import_records(request)
    assert preview["valid"] and preview["counts"]["update"] >= 22
    request.mode = "create"
    assert not editor.import_records(request)["valid"]
    request.mode = "update"
    request.destination = "publish"
    assert editor.import_records(request)["dataset_version"] == "0.2.1"
    request.expected_revision = 1
    request.destination = "preview"
    request.content = ('[{"id":"duplicate"},{"id":"duplicate"}]' if format == "json" else '- id: duplicate\n- id: duplicate\n')
    with pytest.raises(ValueError, match="重复 ID"):
        editor.import_records(request)


def test_csv_entry_senses_and_directional_project_conditions(editor):
    with Lexicon(editor.database) as lexicon:
        content = export_file(lexicon, "csv").content.decode("utf-8-sig")
        project = export_file(lexicon, "csv", "demo-de-en").content.decode("utf-8-sig")
    result = editor.import_records(ImportRequest(expected_revision=0, format="csv", content=content, mode="update", include_operations=True))
    assert result["valid"] and result["counts"]["update"] == 22
    yangqi = next(op for op in result["operations"] if op["id"] == "entry-zh-Hans-yangqi")
    assert len(yangqi["values"]["senses"]) > 1
    result = editor.import_records(ImportRequest(expected_revision=0, format="csv", content=project, mode="update", include_operations=True))
    assert result["valid"]
    assert all("scope" in o["values"] for o in result["operations"] if o["kind"] == "translations")
    assert any(o["kind"] == "profiles" and "selections" in o["values"] for o in result["operations"])


def test_csv_mapping_and_missing_ids_survive_in_draft(editor):
    result = editor.import_records(ImportRequest(expected_revision=0, format="csv", content="原词,语言\n外来术语,de\n",
        columns={"lemma": "原词", "language": "语言"}, destination="draft"))
    assert not result["validation"]["valid"]
    draft = editor.read("drafts", [result["id"]])["records"][0]
    assert draft["operations"][0]["id"].startswith("entry-")
    assert draft["operations"][0]["values"]["lemma"] == "外来术语"


def test_tbx_native_roundtrip_and_external_notes_are_not_evidence(editor):
    with Lexicon(editor.database) as lexicon:
        content = export_file(lexicon, "tbx").content.decode()
    result = editor.import_records(ImportRequest(expected_revision=0, format="tbx", content=content, mode="upsert", include_operations=True))
    assert result["valid"]
    assert len([o for o in result["operations"] if o["kind"] == "entries"]) == 22
    xml = f'''<tbx xmlns="{TBX_NS}" type="TBX-Basic" style="dca"><text><body><conceptEntry id="external-concept"><descrip type="definition">外部概念</descrip><langSec xml:lang="de"><termSec><term>Fremdwort</term><admin type="source">某书第十页，未核对</admin><termNote type="administrativeStatus">preferredTerm-admn-sts</termNote></termSec></langSec><langSec xml:lang="en"><termSec><term>foreign term</term></termSec></langSec></conceptEntry></body></text></tbx>'''
    result = editor.import_records(ImportRequest(expected_revision=0, format="tbx", content=xml, destination="draft"))
    assert not result["validation"]["valid"]
    record = editor.read("drafts", [result["id"]])["records"][0]
    assert "某书第十页" in "\n".join(record["notes"])
    assert not any(o["kind"] in ("translations", "attestations", "works", "editions") for o in record["operations"])


def test_article_create_reference_delete_and_path_validation(editor):
    editor.apply(batch(editor, op("articles", "articles/test.md", "create", content="# 文章\n\n<script>bad</script>"),
                       op("entries", "entry-de-wert", article="articles/test.md")))
    assert not editor.preview(batch(editor, op("articles", "articles/test.md", "delete")))["valid"]
    editor.apply(batch(editor, op("articles", "articles/test.md", "delete"), op("entries", "entry-de-wert", article=None)))
    assert not (editor.root / "articles/test.md").exists()
    assert not editor.preview(batch(editor, op("articles", "articles/../escape.md", "create", content="bad")))["valid"]


def test_http_editing_parity_readonly_and_errors(editor):
    with TestClient(create_app(editor.database)) as readonly:
        assert readonly.post('/api/editor/apply', json={}).status_code == 404
        assert '资料管理' not in readonly.get('/').text
    with TestClient(create_app(editor.database, root=editor.root, edit=True)) as client:
        assert client.get('/manage').status_code == 200
        assert '资料管理' in client.get('/entries/entry-de-wert').text
        payload = batch(editor, new_work()).model_dump(mode="json")
        assert client.post('/api/editor/preview', json=payload).json() == editor.preview(ChangeBatch(**payload))
        response = client.post('/api/editor/apply', json=payload)
        assert response.status_code == 200 and response.json()["storage"] == "local"
        assert client.get('/api/metadata').json()['dataset_version'] == '0.2.1'
        assert client.post('/api/editor/apply', json=payload).status_code == 409
        assert client.post('/api/editor/apply', json={**payload,"expected_revision":1}, headers={"Origin":"https://elsewhere.test"}).status_code == 403
        html = client.post('/api/editor/markdown', json={"content":"<script>bad</script>"}).json()['html']
        assert '<script>' not in html
        assert client.get('/api/editor/schema?kind=entries').json()['schema']['properties']['senses']


async def exercise_writer(connection, editor):
    async with connection as client:
        listing = await client.list_tools()
        tools = {t.name: t for t in listing.tools}
        assert 'apply_changes' in tools and 'retry_sync' not in tools
        assert tools['apply_changes'].annotations.destructive_hint
        assert not tools['apply_changes'].annotations.read_only_hint
        schema = await client.call_tool('get_editing_schema', {'kind':'works'})
        assert json.loads(schema.content[0].text)['schema']['properties']['title']
        request = batch(editor, new_work())
        result = await client.call_tool('preview_changes', {'batch':request.model_dump(mode="json")})
        assert json.loads(result.content[0].text) == editor.preview(request)
        result = await client.call_tool('apply_changes', {'batch':request.model_dump(mode="json")})
        assert not result.is_error and len(result.content) == 1 and result.structured_content is None
        assert json.loads(result.content[0].text)['local_saved']
        stale = await client.call_tool('apply_changes', {'batch':request.model_dump(mode="json")})
        assert stale.is_error and 'error' in json.loads(stale.content[0].text)
        saved = await client.call_tool('save_draft', {'request': {'expected_revision':1,'operations':[
            op('works','work-incomplete','create',title='草稿')]}})
        assert not json.loads(saved.content[0].text)['validation']['valid']
        metadata = await client.call_tool('get_metadata', {})
        assert json.loads(metadata.content[0].text)['dataset_version'] == '0.2.1'
        evidence = await client.call_tool('read_evidence', {'identifiers':['att-value'],'languages':['en'],'dataset_version':'0.2.1'})
        assert not evidence.is_error and len(evidence.content) == 1


def test_real_stdio_writer(editor):
    params = StdioServerParameters(command=sys.executable, args=['-m','marx_lexicon','--root',str(editor.root),
        '--database',str(editor.database),'mcp','--edit'], cwd=str(ROOT))
    asyncio.run(exercise_writer(Client(params), editor))


@pytest.mark.parametrize('command', ['serve', 'mcp'])
def test_real_http_writer(editor, command, monkeypatch):
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(key, raising=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    args = ['serve','--with-mcp'] if command == 'serve' else ['mcp','--transport','streamable-http']
    process = subprocess.Popen([sys.executable,'-m','marx_lexicon','--root',str(editor.root),
        '--database',str(editor.database),*args,'--edit','--port',str(port)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode('utf-8',errors='replace'))
            try:
                if httpx.get(url+'/api/metadata',trust_env=False,timeout=1).status_code in (200,404):
                    break
            except httpx.TransportError:
                pass
            time.sleep(.05)
        else:
            pytest.fail('HTTP 启动超时')
        asyncio.run(exercise_writer(Client(url+'/mcp'),editor))
    finally:
        process.terminate()
        process.communicate(timeout=10)
