from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderLiteral = Literal["ollama", "anthropic"]
ModeLiteral = Literal["auto", "qa", "essay", "artifact_md", "artifact_html"]


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    provider: ProviderLiteral | None = None


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    provider: ProviderLiteral | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    provider: str
    model: str | None = None
    created_at: datetime
    updated_at: datetime


class SourceOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    guest: str
    title: str
    url: str | None = None
    timestamp: str | None = None
    quote: str | None = None
    n: int | None = None
    cited: bool = True


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    seq: int
    role: str
    content: str
    route: str | None = None
    sources: list[dict] = []
    artifact_ids: list[str] = []
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    meta: dict = {}
    created_at: datetime


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: Literal["markdown", "html"]
    title: str
    content: str
    sanitized: bool
    report: dict = {}
    skill: str | None = None
    message_id: str | None = None
    created_at: datetime


class SessionDetail(SessionOut):
    messages: list[MessageOut]
    artifacts: list[ArtifactOut]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    mode: ModeLiteral = "auto"
    provider: ProviderLiteral | None = Field(default=None, description="Override the session's provider for this turn")


class ProviderOut(BaseModel):
    name: str
    kind: str
    model: str
    configured: bool
    available: bool
    detail: str
    is_default: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = {}
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
