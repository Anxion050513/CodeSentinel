"""Observability API router — LangFuse trace queries and eval endpoints."""
import logging

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["observability"])


@router.get("/traces")
async def get_traces(
    session_id: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Query LangFuse traces (if observability is enabled)."""
    try:
        from server.observability.langfuse_client import get_langfuse_client
        mgr = get_langfuse_client()
        if not mgr.enabled or not mgr.client:
            return {"enabled": False, "traces": [], "message": "LangFuse is not configured"}
    except Exception:
        return {"enabled": False, "traces": [], "message": "LangFuse module not available"}

    try:
        client = mgr.client
        if session_id:
            trace = client.get_trace(session_id)
            return {"enabled": True, "trace": trace.__dict__ if trace else None}
        else:
            traces = client.fetch_traces(limit=limit)
            return {"enabled": True, "traces_count": len(traces.data), "traces": [
                {"id": t.id, "name": t.name, "timestamp": str(t.timestamp)}
                for t in traces.data
            ]}
    except Exception as e:
        return {"enabled": True, "error": str(e)}
