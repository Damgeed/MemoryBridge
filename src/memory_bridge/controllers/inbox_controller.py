"""Agent inbox endpoints — async agent-to-agent messaging.

Agents can send messages to each other, check their inbox,
and acknowledge messages. This enables loosely-coupled
agent coordination without explicit handoff.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..dependencies import get_storage
from ..models import InboxMessage, InboxSendRequest
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbox")


@router.post("", response_model=InboxMessage)
async def send_inbox_message(
    payload: InboxSendRequest,
    repo: MemoryRepository = Depends(get_storage),
):
    """Send an inbox message from one agent to another.

    The recipient can poll GET /inbox/{agent_id} to receive it.
    """
    msg = InboxMessage(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        subject=payload.subject,
        body=payload.body,
        priority=payload.priority,
        project=payload.project,
    )
    return await repo.send_inbox_message(msg)


@router.get("/{agent_id}")
async def get_inbox(
    agent_id: str,
    unread_only: bool = Query(False, description="Only return unread messages"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Optional[str] = Query(None),
    repo: MemoryRepository = Depends(get_storage),
):
    """Get inbox messages for a specific agent.

    Returns messages sorted by most recent first, with total count.
    """
    messages, total = await repo.get_inbox_messages(
        to_agent_id=agent_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
        project=project,
    )
    return {"messages": messages, "total": total, "count": len(messages)}


@router.post("/{message_id}/acknowledge")
async def acknowledge_message(
    message_id: str,
    repo: MemoryRepository = Depends(get_storage),
):
    """Mark an inbox message as read.

    Returns {"acknowledged": true} if the message was found and marked.
    """
    success = await repo.acknowledge_inbox_message(message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"acknowledged": True, "message_id": message_id}


@router.get("/{agent_id}/unread/count")
async def count_unread(
    agent_id: str,
    project: Optional[str] = Query(None),
    repo: MemoryRepository = Depends(get_storage),
):
    """Get the count of unread inbox messages for an agent."""
    count = await repo.count_unread_inbox(to_agent_id=agent_id, project=project)
    return {"agent_id": agent_id, "unread_count": count}
