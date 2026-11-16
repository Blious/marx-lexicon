import asyncio
import csv
import io
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from lxml import etree
from mcp import Client, StdioServerParameters
from rdflib import Graph, Namespace, SKOS
from saxonche import PySaxonProcessor
from starlette.testclient import TestClient

from marx_lexicon.catalog import validate_links
from marx_lexicon.cli import main
from marx_lexicon.database import build_database
from marx_lexicon.exporting import BASE_URI, export_file, snapshot
from marx_lexicon.models import Dataset
from marx_lexicon.service import Lexicon
from marx_lexicon.web import create_app, highlighted, render_markdown

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def release_database(tmp_path_factory):
    database = tmp_path_factory.mktemp("interfaces") / "lexicon.sqlite"
    build_database(ROOT, database)
    return database


@pytest.fixture
def client(release_database):
    with TestClient(create_app(release_database)) as client:
        yield client


def test_http_browse_filter_and_service_parity(client, release_database):
    with Lexicon(release_database) as lexicon:
        assert client.get('/api/metadata').json() == lexicon.get_metadata()
        result = client.get('/api/entries', params={"query": "劳动力", "language": "zh-Hans"}).json()
        assert result == lexicon.list_entries(query="劳动力", language="zh-Hans")
        assert result['results'][0]['id'] == 'entry-zh-Hans-laodongli'
        assert client.get('/api/entries', params={"query": "Werth"}).json()['results'][0]['id'] == 'entry-de-wert'
        whole = lexicon.list_entries(limit=100)['results']
        parts = [client.get('/api/entries', params={"offset": offset, "limit": 5}).json()['results']
                 for offset in range(0, len(whole), 5)]
        assert [r for part in parts for r in part] == whole
        assert client.get('/api/concepts/concept-value').json() == lexicon.read_concept('concept-value')
        filtered = client.get('/api/entries', params={"work_id": "work-capital-1", "concept_id": "concept-value"}).json()
        assert filtered['total'] == 3


@pytest.mark.parametrize('url', ['/', '/?query=劳动力&language=zh-Hans', '/?language=&work_id=&concept_id=',
    '/entries/entry-zh-Hans-yangqi', '/concepts/concept-value', '/sources',
    '/evidence/att-abstract-labour', '/translate', '/exports'])
def test_reading_pages(client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert '数据 0.2.0' in response.text
    assert '跳至正文' in response.text
    assert '<script src="/static/site.js"' in response.text


def test_distinct_senses_and_witnesses_are_visible(client):
    html = client.get('/entries/entry-zh-Hans-yangqi').text
    assert 'Aufheben' in html and 'Aufhebung' in html
    assert 'annulment' in html and 'transcendence' in html
    assert 'sense-zh-Hans-yangqi-' in html
    assert 'github.com/Blious/marx-lexicon/blob/main/data/attestations/manuscripts.yaml' in html
    evidence = client.get('/evidence/att-abstract-labour').text
    assert '<mark>' in evidence and 'human labour in the abstract' in evidence
    assert '对应候选' in evidence and '未登记' in evidence
    assert '/sources#edition-capital-en-moore-aveling' in evidence


def test_web_escaping_and_unicode_offsets():
    assert '&lt;script&gt;' in render_markdown('<script>alert(1)</script>')
    assert 'href="javascript:' not in render_markdown('[x](javascript:alert(1))')
    assert str(highlighted('😀Wert<script>', [{"start": 1, "end": 5}])) == '😀<mark>Wert</mark>&lt;script&gt;'


def test_reference_and_errors(client, release_database):
    body = dict(text='  😀 Arbeitskraft und Wert.  ', source_language='de', target_language='en',
                profile_id='demo-de-en', edition_id='edition-capital-de-mew23')
    result = client.post('/api/translation-reference', json=body)
    assert result.status_code == 200
    with Lexicon(release_database) as lexicon:
        assert result.json() == lexicon.prepare_translation(**body)
    assert result.json()['terms'][0]['matches'][0]['spans'][0][0] == 4
    assert client.post('/api/translation-reference?format=markdown', json=body).headers['content-type'].startswith('text/markdown')
    assert client.get('/api/entries/missing').status_code == 404
    assert client.get('/api/entries?limit=0').status_code == 422
    assert client.get('/api/entries?language=unknown').status_code == 422
    assert client.post('/api/evidence', json={"identifiers": ['att-value'], "dataset_version": 'old'}).status_code == 409
    assert client.post('/api/evidence', json={"identifiers": []}).status_code == 422
    assert client.post('/api/translation-reference', json={**body, "target_language": "zh-Hans"}).status_code == 422


def test_snapshot_complete_and_csv_readable(release_database):
    with Lexicon(release_database) as lexicon:
        data = json.loads(export_file(lexicon).content)
        dataset = Dataset.model_validate(data['dataset'])
        validate_links(dataset, ROOT, {})
        assert data == snapshot(lexicon)
        assert data['articles'] == lexicon.articles
        csv_export = export_file(lexicon, 'csv')
        assert csv_export.content.startswith(b'\xef\xbb\xbf')
        rows = list(csv.DictReader(io.StringIO(csv_export.content.decode('utf-8-sig'))))
        assert len(rows) == len(lexicon.senses)
        assert json.loads(rows[0]['forms']) == [f.model_dump(mode='json') for f in dataset.entries[0].forms]
        rows = list(csv.DictReader(io.StringIO(export_file(lexicon, 'csv', 'demo-de-en').content.decode('utf-8-sig'))))
        assert len(rows) == len(lexicon.export_glossary('demo-de-en')['rows'])
        assert all('source_sense' in row and 'target_sense' in row and 'profile_choices' in row for row in rows)
        assert all(json.loads(row['profile'])['id'] == 'demo-de-en' for row in rows)


@pytest.fixture(scope='module')
def tbx_validators(tmp_path_factory):
    root = ROOT / 'tests/standards'
    rng = etree.RelaxNG(etree.parse(str(root / 'TBXcoreStructV03.rng')))
    processor = PySaxonProcessor(license=False)
    compiler = processor.new_xslt30_processor()
    schema = processor.parse_xml(xml_file_name=str(root / 'TBX-Basic_DCA.sch'))
    for name in ['iso_dsdl_include.xsl', 'iso_abstract_expand.xsl', 'iso_svrl_for_xslt2.xsl']:
        transform = compiler.compile_stylesheet(stylesheet_file=str(root / name))
        schema = transform.transform_to_value(xdm_node=schema).item_at(0)
    compiled = compiler.compile_stylesheet(stylesheet_text=str(schema))
    yield rng, processor, compiled


@pytest.mark.parametrize('profile', [None, 'demo-de-en', 'demo-de-zh'])
def test_tbx_core_and_basic_schematron(release_database, tbx_validators, profile):
    with Lexicon(release_database) as lexicon:
        data = export_file(lexicon, 'tbx', profile).content
        rng, processor, compiled = tbx_validators
        doc = etree.fromstring(data)
        assert rng.validate(doc), str(rng.error_log)
        report = compiled.transform_to_string(xdm_node=processor.parse_xml(xml_text=data.decode('utf-8')))
        svrl = etree.fromstring(report.encode('utf-8'))
        failures = svrl.findall('.//{http://purl.oclc.org/dsdl/svrl}failed-assert')
        assert not failures, '\n'.join(''.join(f.itertext()) for f in failures)
        ns = {'t': 'urn:iso:std:iso:30042:ed-2'}
        assert doc.xpath('count(//t:conceptEntry[@id="concept-aufhebung-property"])', namespaces=ns) == 1
        assert doc.xpath('count(//t:conceptEntry[@id="concept-aufhebung-religion"])', namespaces=ns) == 1
        assert not doc.xpath('//t:termNote[@type="administrativeStatus"]', namespaces=ns)
        assert b'needs_context' not in data  # 条件保留，不在无上下文的交换中虚构已匹配状态。


def test_skos_draft_and_reviewed_relationships(release_database):
    with Lexicon(release_database) as lexicon:
        ns = Namespace(BASE_URI)
        graph = Graph().parse(data=export_file(lexicon, 'skos').content, format='turtle')
        assert len(set(graph.subjects(None, SKOS.Concept))) == 8
        assert not list(graph.triples((None, SKOS.exactMatch, None)))
        assert not list(graph.triples((None, SKOS.related, None)))
        assert list(graph.triples((None, ns.status, None)))
        lexicon.concepts['concept-value'].relations[0].status = 'reviewed'
        graph = Graph().parse(data=export_file(lexicon, 'skos').content, format='turtle')
        assert (ns['concept-value'], SKOS.related, ns['concept-abstract-labour']) in graph
        assert not list(graph.triples((None, SKOS.exactMatch, None)))


@pytest.mark.parametrize('format', ['json', 'markdown', 'csv', 'tbx', 'skos'])
def test_cli_http_download_parity(client, release_database, tmp_path, format):
    output = tmp_path / ('dataset.' + format)
    assert main(['--database', str(release_database), 'export-dataset', '--format', format, '--output', str(output)]) == 0
    download = client.get('/api/export', params={"format": format})
    if format != 'skos':  # RDF 三元组相同，序列化次序不构成格式契约。
        assert output.read_bytes() == download.content
    else:
        assert set(Graph().parse(output, format='turtle')) == set(Graph().parse(data=download.content, format='turtle'))
    report = json.loads(output.with_name(output.name + '.report.json').read_text(encoding='utf-8'))
    assert report == client.get('/api/export', params={"format": format, "report": True}).json()
    assert report['complete_snapshot'] == (format == 'json')


@pytest.fixture(scope='module', params=['serve', 'mcp'])
def running_http(release_database, request):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server_args = ['serve', '--with-mcp'] if request.param == 'serve' else ['mcp', '--transport', 'streamable-http']
    process = subprocess.Popen([sys.executable, '-m', 'marx_lexicon', '--database', str(release_database),
                                *server_args, '--port', str(port)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode('utf-8', errors='replace'))
            try:
                response = httpx.get(url + '/api/metadata', timeout=1, trust_env=False)
                if response.status_code in (200, 404):
                    break
            except httpx.TransportError:
                pass
            time.sleep(.05)
        else:
            pytest.fail('HTTP server did not start')
        yield url + '/mcp'
    finally:
        process.terminate()
        process.communicate(timeout=10)


async def check_mcp(connection, database):
    async with connection as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {'get_metadata','lookup','list_entries','read_entry',
            'read_concept','read_evidence','prepare_translation','export_glossary','list_terms','read_term'}
        result = await client.call_tool('lookup', {'query': '劳动力', 'language': 'zh-Hans'})
        assert len(result.content) == 1 and result.structured_content is None
        with Lexicon(database) as lexicon:
            assert json.loads(result.content[0].text) == lexicon.lookup('劳动力', 'zh-Hans')
            metadata = await client.call_tool('get_metadata', {})
            assert json.loads(metadata.content[0].text) == lexicon.get_metadata()
            reference_args = dict(text='😀 Wert und Aufhebung.', source_language='de', target_language='en',
                                  profile_id='demo-de-en')
            result = await client.call_tool('prepare_translation', reference_args)
            assert json.loads(result.content[0].text) == lexicon.prepare_translation(**reference_args)
            evidence_args = dict(identifiers=['att-value'], languages=['de','en'], dataset_version='0.2.0')
            result = await client.call_tool('read_evidence', evidence_args)
            assert json.loads(result.content[0].text) == lexicon.read_evidence(**evidence_args)
        result = await client.call_tool('read_entry', {'entry_id': 'missing'})
        assert result.is_error
        result = await client.call_tool('read_evidence', {**evidence_args, 'dataset_version': 'old'})
        assert result.is_error


@pytest.mark.parametrize('mode', ['auto', 'legacy'])
def test_real_http_mcp(running_http, release_database, mode, monkeypatch):
    # 本机回环连接不受测试宿主的外部 SOCKS 代理配置影响。
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    asyncio.run(check_mcp(Client(running_http, mode=mode), release_database))


def test_real_stdio_mcp(release_database):
    parameters = StdioServerParameters(command=sys.executable, args=['-m','marx_lexicon',
        '--database',str(release_database),'mcp','--transport','stdio'], cwd=str(ROOT))
    asyncio.run(check_mcp(Client(parameters), release_database))
