"""
RepoMind API Pydantic schemas.
Defines models for HTTP queries, health responses, and streaming SSE event payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from app.config import MAX_QUERY_CHARS


class QueryRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="The developer investigation query.",
    )
    repo_url: Optional[str] = Field(
        default=None,
        description="Optional GitHub repository URL (e.g. 'https://github.com/owner/repo') to target.",
    )
    history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Optional conversation history messages.",
    )
    repo_revision: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    response_style: Optional[Literal["balanced", "concise", "detailed"]] = Field(
        default="balanced",
        description="Preferred level of response detail.",
    )


class RepoIndexRequest(BaseModel):
    repo_url: str = Field(
        ...,
        min_length=3,
        description="GitHub repository URL or 'owner/repo' slug (e.g. 'https://github.com/owner/repository').",
    )


class RepoIndexResponse(BaseModel):
    status: str
    repo_name: str
    repo_url: str
    repo_revision: str
    python_files_count: int
    docs_files_count: int
    total_files_count: int
    sample_files: List[str] = Field(default_factory=list)
    manifests: List[str] = Field(default_factory=list)


class UsageMetrics(BaseModel):
    exa_calls: int = 0
    context7_calls: int = 0
    firecrawl_pages: int = 0
    cache_hits: Dict[str, int] = Field(default_factory=dict)
    total_exa_usd: float = 0.0


class QueryResponse(BaseModel):
    query: str
    repo_revision: Optional[str] = None
    report: str
    trace_logs: List[str]
    usage: UsageMetrics
    status: Literal["completed", "rate_limited", "blocked", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    repo_name: str
    repo_available: bool
    database_ready: bool
    version: str = "1.0.0"


class SSEEventData(BaseModel):
    event_type: Literal["status", "progress", "token", "complete", "error"]
    message: Optional[str] = None
    node: Optional[str] = None
    token: Optional[str] = None
    text: Optional[str] = None
    report: Optional[str] = None
    trace_logs: Optional[List[str]] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Chat History & Conversations (Supabase Auth)
# ---------------------------------------------------------------------------

class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    repo_name: str
    repo_url: str


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str
    repo_name: str
    repo_url: str
    created_at: str
    updated_at: str


class MessageCreate(BaseModel):
    sender: Literal["user", "assistant"]
    text: str
    citations: Optional[List[Any]] = None
    trace_logs: Optional[List[str]] = None


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    sender: str
    text: str
    citations: Optional[List[Any]] = None
    trace_logs: Optional[List[str]] = None
    created_at: str
