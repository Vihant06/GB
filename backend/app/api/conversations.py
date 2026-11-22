"""
RepoMind Conversations API router.
Provides CRUD endpoints for chat conversations and messages,
backed by Supabase with Row-Level Security (RLS).
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.deps import get_actor_identity
from app.config import SUPABASE_ANON_KEY, SUPABASE_URL
from app.schemas import (
    ConversationCreate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _get_supabase_client(authorization: str | None = None):
    """
    Create a Supabase client and, when a user JWT is supplied, set the
    Authorization header so that Postgres RLS policies are evaluated
    against the calling user.
    """
    from supabase import create_client

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured on this server.",
        )

    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    if authorization:
        client.postgrest.auth(authorization.replace("Bearer ", ""))

    return client


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get("", response_model=List[ConversationResponse])
def list_conversations(
    user_id: str = Depends(get_actor_identity),
    authorization: str | None = Header(None),
):
    """List the calling user's conversations, newest first."""
    sb = _get_supabase_client(authorization)
    result = (
        sb.table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return result.data


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    body: ConversationCreate,
    user_id: str = Depends(get_actor_identity),
    authorization: str | None = Header(None),
):
    """Create a new conversation for the calling user."""
    sb = _get_supabase_client(authorization)
    result = (
        sb.table("conversations")
        .insert(
            {
                "user_id": user_id,
                "title": body.title,
                "repo_name": body.repo_name,
                "repo_url": body.repo_url,
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create conversation.")
    return result.data[0]


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    user_id: str = Depends(get_actor_identity),
    authorization: str | None = Header(None),
):
    """Delete a conversation (RLS ensures ownership)."""
    sb = _get_supabase_client(authorization)
    sb.table("conversations").delete().eq("id", conversation_id).eq("user_id", user_id).execute()
    return None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
def list_messages(
    conversation_id: str,
    user_id: str = Depends(get_actor_identity),
    authorization: str | None = Header(None),
):
    """Get all messages for a conversation, ordered chronologically."""
    sb = _get_supabase_client(authorization)
    result = (
        sb.table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    conversation_id: str,
    body: MessageCreate,
    user_id: str = Depends(get_actor_identity),
    authorization: str | None = Header(None),
):
    """Add a message to a conversation."""
    sb = _get_supabase_client(authorization)
    row = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "sender": body.sender,
        "text": body.text,
        "citations": body.citations or [],
        "trace_logs": body.trace_logs or [],
    }
    result = sb.table("messages").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create message.")

    # Touch the parent conversation's updated_at timestamp
    sb.table("conversations").update({"updated_at": "now()"}).eq("id", conversation_id).execute()

    return result.data[0]
