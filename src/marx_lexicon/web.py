"""本地查阅页面与 HTTP 接口。"""

import json
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import quote, urljoin

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from pydantic import Field

from .exporting import BASE_URI, export_file
from .models import Model
from .render import markdown, scope_label
from .service import Lexicon, VersionMismatch

PACKAGE = Path(__file__).parent
STATUS_LABELS = {"draft": "工作稿", "reviewed": "已审阅", "unreviewed": "待审阅", "candidate": "对应候选"}


class EvidenceRequest(Model):
    identifiers: list[str] = Field(min_length=1)
    languages: list[str] | None = None
    dataset_version: str | None = None


class TranslationRequest(Model):
    # 原文不去首尾空白，保证引用位置基于调用者原始输入。
    model_config = {"extra": "forbid", "str_strip_whitespace": False}
    text: str
    source_language: str
    target_language: str
    profile_id: str
    work_id: str | None = None
    edition_id: str | None = None
    sense_id: str | None = None
    detail: str = "compact"


def render_markdown(text, article=None):
    renderer = MarkdownIt("commonmark", {"html": False})
    tokens = renderer.parse(text or "")
    for block in tokens:
        for token in block.children or []:
            if token.type == "link_open":
                href = token.attrGet("href") or ""
                if article and not href.startswith(("http:", "https:", "mailto:", "#")):
                    href = urljoin("https://github.com/Blious/marx-lexicon/blob/main/" + article, href)
                    token.attrSet("href", href)
    return Markup(renderer.renderer.render(tokens, renderer.options, {}))


def highlighted(text, mentions):
    # 重叠标注合并显示；原标注与索引仍由 API 完整返回。
    spans = []
    for mention in sorted(mentions, key=lambda m: (m["start"], m["end"])):
        if spans and mention["start"] <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], mention["end"])
        else:
            spans.append([mention["start"], mention["end"]])
    parts, position = [], 0
    for start, end in spans:
        parts.extend([escape(text[position:start]), "<mark>" + escape(text[start:end]) + "</mark>"])
        position = end
    parts.append(escape(text[position:]))
    return Markup("".join(parts))


def create_app(database: Path, with_mcp=False, *, root: Path | None = None, edit=False):
    # 启动时仅验证数据库可读；各请求有自己的连接。
    with Lexicon(database):
        pass
    editor = None
    if edit:
        from .editing import Editor
        editor = Editor(root or Path.cwd(), database)
    mcp = None
    mcp_app = None
    if with_mcp:
        from .mcp_server import create_mcp
        mcp = create_mcp(database, editor=editor)
        mcp_app = mcp.streamable_http_app(json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        if mcp:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title="马克思多语术语库", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    templates.env.filters.update(md=render_markdown, highlight=highlighted, scope=scope_label,
                                 status=lambda value: STATUS_LABELS.get(value, value))

    async def service():
        with Lexicon(database) as lexicon:
            yield lexicon

    def page(request, template, lexicon, **context):
        return templates.TemplateResponse(request=request, name=template,
            context={"meta": lexicon.get_metadata(), "editing": edit, **context})

    for exception in (KeyError, ValueError, VersionMismatch):
        async def error_response(request: Request, error: Exception):
            code = 409 if isinstance(error, VersionMismatch) else 404 if isinstance(error, KeyError) else 422
            message = str(error)
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": message}, status_code=code)
            return HTMLResponse(f'<html lang="zh-Hans"><meta charset="utf-8"><title>无法读取</title>'
                                f'<main><h1>无法读取</h1><p>{escape(message)}</p><a href="/">返回查阅</a></main></html>',
                                status_code=code)
        app.add_exception_handler(exception, error_response)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, query: str = "", language: str | None = None,
             work_id: str | None = None, concept_id: str | None = None, offset: int = 0,
             lexicon=Depends(service)):
        result = lexicon.list_terms(query, language or None, work_id or None, concept_id or None, offset)
        return page(request, "index.html", lexicon, result=result,
                    filters=dict(query=query, language=language, work_id=work_id, concept_id=concept_id),
                    previous=str(request.url.include_query_params(offset=max(0, offset - 20))),
                    next=str(request.url.include_query_params(offset=offset + 20)))

    @app.get("/terms/{term_id}", response_class=HTMLResponse)
    async def term(request: Request, term_id: str, lexicon=Depends(service)):
        result = lexicon.read_term(term_id, "full")
        return page(request, "term.html", lexicon, result=result,
                    editions={e["id"]: e for e in result["editions"]})

    @app.get("/api/terms")
    async def terms(query: str | None = None, language: str | None = None, work_id: str | None = None,
                    concept_id: str | None = None, offset: int = 0, limit: int = 20, lexicon=Depends(service)):
        return lexicon.list_terms(query, language or None, work_id or None, concept_id or None, offset, limit)

    @app.get("/api/terms/{term_id}")
    async def api_term(term_id: str, detail: str = "compact", lexicon=Depends(service)):
        return lexicon.read_term(term_id, detail)

    @app.get("/entries/{entry_id}", response_class=HTMLResponse)
    async def entry(request: Request, entry_id: str, lexicon=Depends(service)):
        result = lexicon.read_entry(entry_id)
        translation_ids = {t["id"] for t in result["translations"]}
        choices = [{"profile": p.title, "profile_scope": p.scope.model_dump(mode="json"),
                    "target_sense_id": lexicon.translations[s.translation_id].target_sense_id,
                    **s.model_dump(mode="json")} for p in lexicon.profiles.values()
                   for s in p.selections if s.translation_id in translation_ids]
        return page(request, "entry.html", lexicon, result=result, choices=choices, owners=lexicon.terms_for("entries", entry_id),
                    sense_map={s.id: {"entry_id": e.id, "lemma": e.lemma, "language": e.language}
                               for e in lexicon.dataset.entries for s in e.senses})

    @app.get("/concepts/{concept_id}", response_class=HTMLResponse)
    async def concept(request: Request, concept_id: str, lexicon=Depends(service)):
        return page(request, "concept.html", lexicon, result=lexicon.read_concept(concept_id), owners=lexicon.terms_for("concepts", concept_id))

    @app.get("/sources", response_class=HTMLResponse)
    async def sources(request: Request, lexicon=Depends(service)):
        return page(request, "sources.html", lexicon)

    @app.get("/evidence/{identifier}", response_class=HTMLResponse)
    async def evidence(request: Request, identifier: str, dataset_version: str | None = None,
                 lexicon=Depends(service)):
        result = lexicon.read_evidence([identifier], dataset_version=dataset_version)
        return page(request, "evidence.html", lexicon, result=result,
                    owners=lexicon.terms_for("attestations", identifier),
                    editions={e["id"]: e for e in result["editions"]})

    @app.get("/translate", response_class=HTMLResponse)
    async def translate(request: Request, lexicon=Depends(service)):
        return page(request, "translate.html", lexicon)

    @app.get("/exports", response_class=HTMLResponse)
    async def exports(request: Request, lexicon=Depends(service)):
        return page(request, "exports.html", lexicon)

    @app.get("/api/metadata")
    async def metadata(lexicon=Depends(service)):
        return lexicon.get_metadata()

    @app.get("/api/entries")
    async def entries(query: str | None = None, language: str | None = None, work_id: str | None = None,
                concept_id: str | None = None, offset: int = 0, limit: int = 20, lexicon=Depends(service)):
        return lexicon.list_entries(query, language or None, work_id or None, concept_id or None, offset, limit)

    @app.get("/api/entries/{entry_id}")
    async def api_entry(entry_id: str, lexicon=Depends(service)):
        return lexicon.read_entry(entry_id)

    @app.get("/api/concepts/{concept_id}")
    async def api_concept(concept_id: str, lexicon=Depends(service)):
        return lexicon.read_concept(concept_id)

    @app.post("/api/evidence")
    async def api_evidence(body: EvidenceRequest, lexicon=Depends(service)):
        return lexicon.read_evidence(**body.model_dump())

    @app.post("/api/translation-reference")
    async def reference(body: TranslationRequest, format: str = "json", lexicon=Depends(service)):
        result = lexicon.prepare_translation(**body.model_dump())
        if format == "markdown":
            return Response(markdown(result), media_type="text/markdown")
        if format != "json":
            raise ValueError("format 须为 json 或 markdown")
        return result

    @app.get("/api/export")
    async def api_export(format: str = "json", profile_id: str | None = None, report: bool = False,
                   base_uri: str = BASE_URI, lexicon=Depends(service)):
        result = export_file(lexicon, format, profile_id, base_uri)
        content = json.dumps(result.report, ensure_ascii=False, indent=2).encode("utf-8") if report else result.content
        filename = result.filename + ".report.json" if report else result.filename
        return Response(content, media_type="application/json" if report else result.media_type,
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})

    if editor:
        from .web_editing import mount_editor
        def editor_metadata():
            with Lexicon(database) as lexicon:
                return lexicon.get_metadata()
        mount_editor(app, editor, templates, editor_metadata)

    if mcp_app:
        # SDK 应用自带 /mcp 路由，挂载到根并排在网页路由之后。
        app.mount("/", mcp_app)
    return app
