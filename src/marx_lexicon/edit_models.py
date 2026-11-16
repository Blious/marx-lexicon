"""网页与 MCP 共用的编辑请求；学术数据模型保持独立。"""

from typing import Any, Literal

from pydantic import Field

from .models import Model

Kind = Literal["terms", "entries", "concepts", "translations", "attestations", "works", "editions", "profiles", "articles"]


class Operation(Model):
    action: Literal["create", "update", "delete", "review"]
    kind: Kind
    id: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)


class ChangeBatch(Model):
    expected_revision: int = Field(ge=0)
    operations: list[Operation] = Field(min_length=1)
    message: str = "更新术语资料"


class DraftRequest(Model):
    expected_revision: int = Field(ge=0)
    id: str | None = None
    title: str = "未完成的编辑"
    operations: list[Operation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    term_document: dict[str, Any] | None = None


class RevisionRequest(Model):
    expected_revision: int = Field(ge=0)


class ArticlePreviewRequest(Model):
    model_config = {"extra": "forbid", "str_strip_whitespace": False}
    content: str


class ImportRequest(Model):
    # 文件正文保留空白，尤其是引文及 CSV 的单元格内容。
    model_config = {"extra": "forbid", "str_strip_whitespace": False}
    expected_revision: int = Field(ge=0)
    format: Literal["json", "yaml", "csv", "tbx"]
    content: str
    kind: Kind = "entries"
    mode: Literal["create", "update", "upsert"] = "create"
    columns: dict[str, str] = Field(default_factory=dict)
    destination: Literal["preview", "draft", "publish"] = "preview"
    title: str = "导入资料"
    include_operations: bool = False


class ResourceDeletion(Model):
    kind: Literal["terms", "entries", "senses", "concepts", "translations", "attestations", "works", "editions", "articles"]
    id: str


class TermSaveRequest(Model):
    expected_revision: int = Field(ge=0)
    term: dict[str, Any]
    resources: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    delete: list[ResourceDeletion] = Field(default_factory=list)
    destination: Literal["publish", "draft"] = "publish"
    draft_id: str | None = None
    message: str = "编辑术语"
