"""本地编辑页面与接口，仅在 --edit 模式注册。"""

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from .edit_models import ArticlePreviewRequest, ChangeBatch, DraftRequest, ImportRequest, RevisionRequest, TermSaveRequest
from .editing import EditingConflict, InvalidChanges
from .web import render_markdown


def mount_editor(app, editor, templates, metadata):
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    async def invalid(request, error):
        return JSONResponse({"detail": str(error), "report": error.report}, status_code=422)

    app.add_exception_handler(EditingConflict, conflict)
    app.add_exception_handler(InvalidChanges, invalid)

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if request.url.path.startswith("/api/editor") and request.method != "GET":
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "编辑请求须来自当前本地网页"}, status_code=403)
        return await call_next(request)

    @app.get("/manage/advanced")
    def manage(request: Request):
        return templates.TemplateResponse(request=request, name="manage.html",
                                          context={"meta": metadata(), "editing": True})

    @app.get("/manage")
    def term_management(request: Request):
        return templates.TemplateResponse(request=request, name="term_editor.html",
                                          context={"meta": metadata(), "editing": True})

    @app.get("/api/editor/terms/{term_id}")
    def editable_term(term_id: str):
        return editor.read_editable_term(term_id)

    @app.get("/api/editor/catalog")
    def reference_catalog():
        return editor.reference_catalog()

    @app.post("/api/editor/terms/assemble")
    def assemble_term(body: dict):
        return editor.assemble_term(body)

    @app.post("/api/editor/terms/preview")
    def preview_term(body: TermSaveRequest):
        return editor.preview_term(body)

    @app.post("/api/editor/terms/save")
    def save_term(body: TermSaveRequest):
        return editor.save_term(body)

    @app.get("/api/editor/unassigned")
    def unassigned(offset: int = 0, limit: int = 20):
        return editor.list_unassigned(offset, limit)

    @app.get("/api/editor/schema")
    def schema(kind: str | None = None):
        return editor.schema(kind)

    @app.get("/api/editor/records")
    def records(kind: str = "entries", identifiers: list[str] | None = Query(None), offset: int = 0, limit: int = 20):
        return editor.read(kind, identifiers, offset, limit)

    @app.get("/api/editor/references/{kind}/{identifier:path}")
    def references(kind: str, identifier: str):
        return editor.references(kind, identifier)

    @app.post("/api/editor/preview")
    def preview(body: ChangeBatch):
        return editor.preview(body)

    @app.post("/api/editor/apply")
    def apply(body: ChangeBatch):
        return editor.apply(body)

    @app.post("/api/editor/import")
    def import_records(body: ImportRequest):
        return editor.import_records(body)

    @app.post("/api/editor/drafts")
    def save_draft(body: DraftRequest):
        return editor.save_draft(body)

    @app.post("/api/editor/drafts/{identifier}/publish")
    def publish_draft(identifier: str, body: RevisionRequest):
        return editor.publish_draft(identifier, body.expected_revision)

    @app.delete("/api/editor/drafts/{identifier}")
    def delete_draft(identifier: str, body: RevisionRequest):
        return editor.delete_draft(identifier, body.expected_revision)

    @app.post("/api/editor/markdown")
    def markdown(body: ArticlePreviewRequest):
        return {"html": str(render_markdown(body.content))}
