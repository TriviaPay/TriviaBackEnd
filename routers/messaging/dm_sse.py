from typing import Optional

from fastapi import APIRouter, Query, Request

from .service import dm_sse_stream as service_dm_sse_stream

router = APIRouter(prefix="/dm", tags=["DM SSE"])


@router.options("/sse")
async def dm_sse_options():
    """CORS preflight handler for SSE endpoint."""
    return {"status": "ok"}


@router.get("/sse")
async def dm_sse(request: Request, token: Optional[str] = Query(default=None)):
    """
    SSE endpoint for real-time DM message delivery.
    Accepts token via query param ?token=... or Authorization header.
    """
    return await service_dm_sse_stream(request, token)
