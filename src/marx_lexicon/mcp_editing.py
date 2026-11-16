"""可选写入工具：按需读取模型，每次调用只返回一份 JSON。"""

import sqlite3

from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .edit_models import ChangeBatch, DraftRequest, ImportRequest
from .editing import InvalidChanges
from .exporting import compact


def add_edit_tools(server, editor):
    def call(method, *args):
        try:
            result = getattr(editor, method)(*args)
            failed = False
        except (ValueError, KeyError, OSError, sqlite3.Error) as error:
            result = {"error": str(error)}
            if isinstance(error, InvalidChanges):
                result["report"] = error.report
            failed = True
        return CallToolResult(is_error=failed, content=[TextContent(type="text", text=compact(result))])

    def tool(read=False, destructive=False):
        return server.tool(structured_output=False, annotations=ToolAnnotations(
            read_only_hint=read, destructive_hint=destructive, open_world_hint=False))

    @tool(read=True)
    def get_editing_schema(kind: str | None = None) -> CallToolResult:
        """按需读取一种资源模型，省略 kind 时读取批次模型。"""
        return call("schema", kind)

    @tool(read=True)
    def read_editable_records(kind: str = "entries", identifiers: list[str] | None = None,
                              offset: int = 0, limit: int = 20) -> CallToolResult:
        """读取规范源记录及编辑版本；kind=drafts 读取独立草稿。"""
        return call("read", kind, identifiers, offset, limit)

    @tool(read=True)
    def preview_changes(batch: ChangeBatch) -> CallToolResult:
        """校验整批修改，列出错误与删除引用，不保存。"""
        return call("preview", batch)

    @tool(destructive=True)
    def apply_changes(batch: ChangeBatch) -> CallToolResult:
        """原子保存整批修改到本地；需当前 expected_revision。"""
        return call("apply", batch)

    @tool(destructive=True)
    def import_records(request: ImportRequest) -> CallToolResult:
        """导入 JSON/YAML/CSV/TBX；默认预览，可存草稿或发布。"""
        return call("import_records", request)

    @tool()
    def save_draft(request: DraftRequest) -> CallToolResult:
        """保存本地编辑草稿，不进入查阅与翻译数据。"""
        return call("save_draft", request)

    @tool(destructive=True)
    def publish_draft(identifier: str, expected_revision: int) -> CallToolResult:
        """重新校验草稿并发布；资料变化时需先修订重存。"""
        return call("publish_draft", identifier, expected_revision)

    @tool(destructive=True)
    def delete_draft(identifier: str, expected_revision: int) -> CallToolResult:
        """删除本地编辑草稿。"""
        return call("delete_draft", identifier, expected_revision)
