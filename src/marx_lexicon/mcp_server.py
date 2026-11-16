"""只读 MCP 工具；协议输出不重复承载 JSON。"""

import sqlite3
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import __version__
from .exporting import compact
from .service import Lexicon


def create_mcp(database: Path, *, editor=None):
    server = MCPServer("marx-lexicon", version=__version__, instructions=(
        "先读 get_metadata 确认范围和方案。prepare_translation 默认精简；按证据 ID、语言和数据版本"
        "调用 read_evidence 展开。整体查阅优先 list_terms/read_term，整体编辑用 read_editable_term/save_term。"
        "候选与项目选择不代表学术审定；未命中不代表不存在术语。"))

    def call(method, *args, **kwargs):
        try:
            with Lexicon(database) as lexicon:
                result = getattr(lexicon, method)(*args, **kwargs)
            return CallToolResult(content=[TextContent(type="text", text=compact(result))])
        except (KeyError, ValueError, OSError, sqlite3.Error) as error:
            return CallToolResult(is_error=True, content=[TextContent(type="text", text=compact({"error": str(error)}))])

    def tool():
        return server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
                           structured_output=False)

    @tool()
    def get_metadata() -> CallToolResult:
        """读取版本、范围与可用语言、作品、版本和方案。"""
        return call("get_metadata")

    @tool()
    def list_terms(query: str | None = None, language: str | None = None, work_id: str | None = None,
                   concept_id: str | None = None, offset: int = 0, limit: int = 20) -> CallToolResult:
        """汇总多语言术语后筛选分页，精确词形优先。"""
        return call("list_terms", query, language, work_id, concept_id, offset, limit)

    @tool()
    def read_term(term_id: str, detail: str = "compact") -> CallToolResult:
        """整体读取义项、语言表达、译法和证据引用；full 展开引文与文章。"""
        return call("read_term", term_id, detail)

    @tool()
    def lookup(query: str, language: str | None = None, limit: int = 20) -> CallToolResult:
        """按原词、译词、词形或说明检索。"""
        return call("lookup", query, language, limit)

    @tool()
    def list_entries(query: str | None = None, language: str | None = None,
                     work_id: str | None = None, concept_id: str | None = None,
                     offset: int = 0, limit: int = 20) -> CallToolResult:
        """按语言、作品、概念筛选并分页浏览词条。"""
        return call("list_entries", query, language, work_id, concept_id, offset, limit)

    @tool()
    def read_entry(entry_id: str) -> CallToolResult:
        """读取完整词条、义项、辨析与证据。"""
        return call("read_entry", entry_id)

    @tool()
    def read_concept(concept_id: str) -> CallToolResult:
        """读取概念、关联词条及关系依据。"""
        return call("read_concept", concept_id)

    @tool()
    def read_evidence(identifiers: list[str], languages: list[str] | None = None,
                      dataset_version: str | None = None) -> CallToolResult:
        """按证据 ID 展开完整引文；可限定语言及数据版本。"""
        return call("read_evidence", identifiers, languages, dataset_version)

    @tool()
    def prepare_translation(text: str, source_language: str, target_language: str, profile_id: str,
                            work_id: str | None = None, edition_id: str | None = None,
                            sense_id: str | None = None, detail: str = "compact") -> CallToolResult:
        """为原文准备全部已登记术语参考，默认精简；不生成译文。"""
        return call("prepare_translation", text, source_language=source_language, target_language=target_language,
                    profile_id=profile_id, work_id=work_id, edition_id=edition_id, sense_id=sense_id, detail=detail)

    @tool()
    def export_glossary(profile_id: str) -> CallToolResult:
        """按需读取项目术语表，含备选、适用范围与证据。"""
        return call("export_glossary", profile_id)

    if editor:
        from .mcp_editing import add_edit_tools
        add_edit_tools(server, editor)
    return server
