import asyncio
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
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from starlette.testclient import TestClient

from marx_lexicon.catalog import load_dataset, validate_links
from marx_lexicon.database import build_database
from marx_lexicon.edit_models import ChangeBatch, ImportRequest, TermSaveRequest, ResourceDeletion
from marx_lexicon.editing import Editor, EditingConflict, InvalidChanges
from marx_lexicon.exporting import export_file
from marx_lexicon.models import Dataset
from marx_lexicon.service import Lexicon
from marx_lexicon.web import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def editor(tmp_path):
    for name in ('data', 'articles'):
        shutil.copytree(ROOT/name, tmp_path/name)
    db=tmp_path/'build/lexicon.sqlite'
    build_database(tmp_path,db)
    return Editor(tmp_path,db)


def edit(editor, term_id='term-aufhebung'):
    result=editor.read_editable_term(term_id)
    return TermSaveRequest(expected_revision=result['revision'], **result['document'])


def test_initial_groups_and_complete_multilingual_views(editor):
    with Lexicon(editor.database) as lexicon:
        assert len(lexicon.terms)==7 and len(lexicon.entries)==22 and len(lexicon.concepts)==8
        labour=lexicon.read_term('term-labour-power')
        assert {e['lemma'] for e in labour['sections'][0]['expressions'] if e['language']=='en'}=={'labour-power','capacity for labour'}
        assert 'attestations' not in labour and labour['witnesses']
        aufhebung=lexicon.read_term('term-aufhebung','full')
        aufheben=lexicon.read_term('term-aufheben')
        assert len(aufhebung['sections'])==2 and len(aufheben['sections'])==1
        assert any(t['id']=='term-aufhebung' for t in aufheben['related_terms'])
        assert any(t['id']=='term-aufheben' for t in aufhebung['related_terms'])
        assert aufhebung['articles']['articles/aufhebung.md']
        assert all(e['sense']['concept_id']!='concept-aufheben-dialectical' for section in aufhebung['sections'] for e in section['expressions'])
        assert {t['id'] for t in lexicon.terms_for('entries','entry-zh-Hans-yangqi')}=={'term-aufhebung','term-aufheben'}


@pytest.mark.parametrize('query,expected',[('Werth',{'term-value'}),('劳动力',{'term-labour-power'}),('力',{'term-labour-power'}),('扬弃',{'term-aufhebung','term-aufheben'})])
def test_search_collapses_before_pagination(editor,query,expected):
    with Lexicon(editor.database) as lexicon:
        result=lexicon.list_terms(query)
        assert {t['id'] for t in result['results']}==expected
        pages=[lexicon.list_terms(query,offset=i,limit=1)['results'][0]['id'] for i in range(result['total'])]
        assert len(pages)==len(set(pages))==len(expected)
        assert all(t['matches'] for t in result['results'])


def test_edit_scope_shared_records_and_sense_merge(editor):
    result=editor.read_editable_term('term-aufhebung')
    assert any(s['kind']=='entries' and s['id']=='entry-zh-Hans-yangqi' for s in result['shared_usage'])
    assert any(s['kind']=='articles' for s in result['shared_usage'])
    request=TermSaveRequest(expected_revision=result['revision'],**result['document'])
    before=editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]
    entry=next(e for e in request.resources['entries'] if e['id']==before['id'])
    assert len(entry['senses'])==2
    request.resources={'entries':[{'id':entry['id'],'senses':[{'id':entry['senses'][0]['id'],'usage':'仅修订本义项'}]}]}
    saved=editor.save_term(request)
    assert saved['local_saved'] and saved['storage']=='local'
    after=editor.read('entries',[entry['id']])['records'][0]
    assert len(after['senses'])==3
    assert after['senses'][0]['usage']=='仅修订本义项'
    assert after['senses'][0]['definition']==before['senses'][0]['definition']
    assert after['senses'][1:]==before['senses'][1:]
    with pytest.raises(EditingConflict):
        editor.save_term(request)


def test_foreign_sense_update_and_implicit_removal_are_prevented(editor):
    request=edit(editor)
    request.resources={'entries':[{'id':'entry-zh-Hans-yangqi','senses':[{
        'id':'sense-zh-Hans-yangqi-aufheben-dialectical','usage':'越界修改'}]}]}
    with pytest.raises(ValueError,match='不属于本术语'):
        editor.preview_term(request)
    request=edit(editor)
    request.resources['entries']=[]
    assert editor.preview_term(request)['valid']
    assert len(editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]['senses'])==3


def test_grouping_uniqueness_and_atomic_regrouping(editor):
    request=edit(editor)
    request.term['concept_ids'].append('concept-aufheben-dialectical')
    report=editor.preview_term(request)
    assert not report['valid'] and any('重复归组' in e['message'] for e in report['errors'])
    with pytest.raises(InvalidChanges):
        editor.save_term(request)
    editor.apply(ChangeBatch(expected_revision=0,operations=[
        {'action':'update','kind':'terms','id':'term-aufhebung','values':{'concept_ids':['concept-aufhebung-property']}},
        {'action':'update','kind':'terms','id':'term-aufheben','values':{'concept_ids':['concept-aufheben-dialectical','concept-aufhebung-religion']}}]))
    with Lexicon(editor.database) as lexicon:
        assert len(lexicon.read_term('term-aufheben')['sections'])==2
        assert len(lexicon.entries['entry-zh-Hans-yangqi'].senses)==3


def test_deleting_term_keeps_sources_and_translation_recall(editor):
    with Lexicon(editor.database) as lexicon:
        before=lexicon.prepare_translation('Wert',source_language='de',target_language='en',profile_id='demo-de-en')
    request=edit(editor,'term-value')
    request.delete=[ResourceDeletion(kind='terms',id='term-value')]
    editor.save_term(request)
    assert len(load_dataset(editor.root).entries)==22
    with Lexicon(editor.database) as lexicon:
        assert lexicon.list_unassigned()['results'][0]['concept']['id']=='concept-value'
        after=lexicon.prepare_translation('Wert',source_language='de',target_language='en',profile_id='demo-de-en')
        assert before['terms']==after['terms']


def test_explicit_sense_delete_is_reference_checked(editor):
    request=edit(editor)
    request.delete=[ResourceDeletion(kind='senses',id='sense-zh-Hans-yangqi-aufhebung-religion')]
    report=editor.preview_term(request)
    assert not report['valid'] and report['references']
    with pytest.raises(InvalidChanges):
        editor.save_term(request)
    assert len(editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]['senses'])==3


def test_shared_evidence_edited_once_and_used_by_both_terms(editor):
    request=edit(editor,'term-estrangement')
    request.resources={'attestations':[{'id':'att-alienation-pair','note':'共同语例说明修订'}]}
    editor.save_term(request)
    with Lexicon(editor.database) as lexicon:
        for tid in ('term-estrangement','term-externalization'):
            evidence=next(a for a in lexicon.read_term(tid,'full')['attestations'] if a['id']=='att-alienation-pair')
            assert evidence['note']=='共同语例说明修订'
    request=edit(editor)
    request.resources={'articles':[{'id':'articles/aufhebung.md','content':'# 共享辨析修订\n\n内容'}]}
    editor.save_term(request)
    with Lexicon(editor.database) as lexicon:
        for tid in ('term-aufhebung','term-aufheben'):
            assert lexicon.read_term(tid,'full')['articles']['articles/aufhebung.md']=='# 共享辨析修订\n\n内容'


def test_term_draft_reopens_and_publishes_with_shared_senses(editor):
    request=edit(editor)
    request.term['summary']='草稿中的概述'
    request.destination='draft'
    result=editor.save_term(request)
    draft=editor.read('drafts',[result['id']])['records'][0]
    assert draft['term_document']['term']['summary']=='草稿中的概述'
    with Lexicon(editor.database) as lexicon:
        assert lexicon.terms['term-aufhebung'].summary!='草稿中的概述'
    editor.publish_draft(result['id'],1)
    assert len(editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]['senses'])==3
    with Lexicon(editor.database) as lexicon:
        assert lexicon.terms['term-aufhebung'].summary=='草稿中的概述'


def test_new_term_draft_and_term_only_article(editor):
    request=TermSaveRequest(expected_revision=0,term={'id':'term-new','title':'新术语','concept_ids':[]},destination='draft')
    draft=editor.save_term(request)
    assert not draft['validation']['valid']
    request.expected_revision=1
    request.draft_id=draft['id']
    request.term.update(concept_ids=['concept-new'],article='articles/new-term.md')
    request.resources={'concepts':[{'id':'concept-new','labels':{'de':'Neu'},'definition':'新概念','evidence_ids':['att-value']}],
                       'articles':[{'id':'articles/new-term.md','content':'# 独立术语文章\n\n正文'}]}
    request.destination='draft'
    editor.save_term(request)
    editor.publish_draft(draft['id'],2)
    with Lexicon(editor.database) as lexicon:
        assert lexicon.read_term('term-new','full')['articles']['articles/new-term.md'].startswith('#')
        assert lexicon.list_terms('独立术语文章')['results'][0]['id']=='term-new'
        assert lexicon.list_terms('新概念',concept_id='concept-new')['results'][0]['id']=='term-new'


def test_imported_term_draft_can_resume_in_whole_form(editor):
    document=editor.read_editable_term('term-aufhebung')['document']
    document['term']['summary']='导入后继续编辑'
    result=editor.import_records(ImportRequest(expected_revision=0,format='json',content=json.dumps(document),mode='update',destination='draft'))
    draft=editor.read('drafts',[result['id']])['records'][0]
    entry=next(e for e in draft['term_document']['resources']['entries'] if e['id']=='entry-zh-Hans-yangqi')
    assert len(entry['senses'])==2
    request=TermSaveRequest(expected_revision=1,draft_id=result['id'],destination='draft',**draft['term_document'])
    editor.save_term(request)
    request.expected_revision=2
    request.destination='publish'
    saved=editor.save_term(request)
    assert saved['import_notes_id']
    assert len(editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]['senses'])==3


def test_incomplete_term_draft_and_quotation_codepoints(editor):
    request=TermSaveRequest(expected_revision=0,term={'id':'term-incomplete','title':'未完成','concept_ids':[]},
        resources={'entries':[{'id':'entry-incomplete','language':'de','lemma':'Unfertig',
                              'senses':[{'id':'sense-incomplete','concept_id':''}]}]},destination='draft')
    assert not editor.save_term(request)['validation']['valid']
    with Lexicon(editor.database) as lexicon:
        original=lexicon.evidence['att-value'].model_dump(mode='json')
    passage=original['passages'][0]
    span=passage['mentions'][0]
    original_surface=passage['text'][span['start']:span['end']]
    passage['text']=' 😀 '+passage['text']+' '
    for mention in passage['mentions']:
        mention['start']+=3
        mention['end']+=3
    request=edit(editor,'term-value')
    request.resources={'attestations':[original]}
    editor.save_term(request)
    with Lexicon(editor.database) as lexicon:
        stored=lexicon.evidence['att-value'].passages[0]
        assert stored.text.startswith(' 😀 ') and stored.text.endswith(' ')
        assert stored.text[stored.mentions[0].start:stored.mentions[0].end]==original_surface


@pytest.mark.parametrize('format',['json','yaml'])
def test_term_bundle_import_preserves_other_terms_senses(editor,format):
    document=editor.read_editable_term('term-aufhebung')['document']
    document['term']['summary']='包内修改'
    data={'format':'term-bundle/1','documents':[document]}
    content=json.dumps(data) if format=='json' else yaml.safe_dump(data,allow_unicode=True)
    result=editor.import_records(ImportRequest(expected_revision=0,format=format,content=content,mode='update',destination='publish'))
    assert result['local_saved']
    entry=editor.read('entries',['entry-zh-Hans-yangqi'])['records'][0]
    assert len(entry['senses'])==3


def test_batch_term_bundles_deduplicate_shared_records(editor):
    documents=[editor.read_editable_term(tid)['document'] for tid in ('term-aufhebung','term-aufheben')]
    request=ImportRequest(expected_revision=0,format='json',content=json.dumps(documents),mode='update')
    assert editor.import_records(request)['valid']
    documents[1]['resources']['articles'][0]['content']='冲突'
    request.content=json.dumps(documents)
    with pytest.raises(ValueError,match='冲突'):
        editor.import_records(request)


def test_incomplete_import_assigns_distinct_ids_for_draft_form(editor):
    document={'term':{'title':'未完成术语','concept_ids':[]},'resources':{'entries':[
        {'language':'de','lemma':'Erstes','senses':[]},{'language':'en','lemma':'Second','senses':[]}]}}
    saved=editor.import_records(ImportRequest(expected_revision=0,format='json',content=json.dumps(document),destination='draft'))
    draft=editor.read('drafts',[saved['id']])['records'][0]
    entries=draft['term_document']['resources']['entries']
    assert len(entries)==2 and entries[0]['id']!=entries[1]['id']
    assert not saved['validation']['valid']


def test_snapshot_and_markdown_include_term_organization(editor):
    editor.apply(ChangeBatch(expected_revision=0,operations=[{'action':'create','kind':'articles','id':'articles/unlinked.md','values':{'content':'待引用文章'}}]))
    with Lexicon(editor.database) as lexicon:
        data=json.loads(export_file(lexicon).content)
        restored=Dataset.model_validate(data['dataset'])
        validate_links(restored,editor.root,{},set(data['articles']))
        assert len(restored.terms)==7
        assert data['articles']['articles/unlinked.md']=='待引用文章'
        content=export_file(lexicon,'markdown').content.decode()
        assert '# 扬弃 · Aufhebung' in content and '# 扬弃活动 · Aufheben' in content
        assert any('术语主条目' in line for line in export_file(lexicon,'tbx').report['losses'])


def test_http_term_views_and_editing_parity(editor):
    with TestClient(create_app(editor.database,root=editor.root,edit=True)) as client, Lexicon(editor.database) as lexicon:
        assert client.get('/api/terms?query=扬弃').json()==lexicon.list_terms('扬弃')
        assert client.get('/api/terms/term-aufhebung').json()==lexicon.read_term('term-aufhebung')
        assert client.get('/api/terms?query=capacity&language=en&work_id=work-capital-1').json()['results'][0]['id']=='term-labour-power'
        assert client.get('/api/terms?concept_id=concept-aufheben-dialectical').json()['total']==1
        assert client.get('/api/terms?limit=0').status_code==422
        assert 'capacity for labour' in client.get('/terms/term-labour-power').text
        assert '整体编辑此术语' in client.get('/terms/term-aufhebung').text
        assert '/terms/term-aufheben' in client.get('/entries/entry-zh-Hans-yangqi').text
        assert '术语资料管理' in client.get('/manage').text
        assert '高级资源管理' in client.get('/manage/advanced').text
        assert client.get('/api/editor/terms/term-aufhebung').json()==editor.read_editable_term('term-aufhebung')
        request=edit(editor);request.resources={};request.term['summary']='HTTP 保存'
        assert client.post('/api/editor/terms/preview',json=request.model_dump()).json()==editor.preview_term(request)
        assert client.post('/api/editor/terms/save',json=request.model_dump()).json()['local_saved']
        assert client.post('/api/editor/terms/save',json=request.model_dump()).status_code==409


async def check_sdk(connection,editor):
    async with connection as client:
        tools={t.name:t for t in (await client.list_tools()).tools}
        assert {'list_terms','read_term','read_editable_term','preview_term','save_term','list_unassigned'} <= set(tools)
        assert tools['save_term'].annotations.destructive_hint
        result=await client.call_tool('read_term',{'term_id':'term-aufhebung'})
        assert len(result.content)==1 and result.structured_content is None
        with Lexicon(editor.database) as lexicon:
            assert json.loads(result.content[0].text)==lexicon.read_term('term-aufhebung')
        unknown=await client.call_tool('read_term',{'term_id':'term-missing'})
        assert unknown.is_error and 'error' in json.loads(unknown.content[0].text)
        result=await client.call_tool('read_editable_term',{'term_id':'term-aufhebung'})
        data=json.loads(result.content[0].text)
        request={'expected_revision':data['revision'],**data['document']}
        request['term']['summary']='MCP 整体保存'
        request['resources']={}
        preview=await client.call_tool('preview_term',{'request':request})
        assert json.loads(preview.content[0].text)['valid']
        result=await client.call_tool('save_term',{'request':request})
        assert not result.is_error and len(result.content)==1 and result.structured_content is None
        assert json.loads(result.content[0].text)['storage']=='local'
        stale=await client.call_tool('save_term',{'request':request})
        assert stale.is_error


def test_stdio_term_tools(editor):
    params=StdioServerParameters(command=sys.executable,args=['-m','marx_lexicon','--root',str(editor.root),'mcp','--edit'],cwd=str(ROOT))
    asyncio.run(check_sdk(Client(params),editor))


@pytest.mark.parametrize('command',['serve','mcp'])
def test_http_term_tools(editor,command,monkeypatch):
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(key,raising=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    args=['serve','--with-mcp'] if command=='serve' else ['mcp','--transport','streamable-http']
    process=subprocess.Popen([sys.executable,'-m','marx_lexicon','--root',str(editor.root),*args,'--edit','--port',str(port)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        url=f'http://127.0.0.1:{port}'
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if process.poll() is not None:pytest.fail(process.stderr.read().decode(errors='replace'))
            try:
                if httpx.get(url+'/api/metadata',trust_env=False,timeout=1).status_code in (200,404):break
            except httpx.TransportError:pass
            time.sleep(.05)
        asyncio.run(check_sdk(Client(url+'/mcp'),editor))
    finally:
        process.terminate();process.communicate(timeout=10)
