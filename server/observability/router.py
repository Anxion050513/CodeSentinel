"""Observability API router — LangFuse trace queries via REST API (v4.x compatible)."""
import base64
import logging

import httpx
from fastapi import APIRouter, Query

from server.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["observability"])


def _langfuse_headers() -> dict | None:
    """Build auth headers for LangFuse REST API, or None if not configured."""
    pk = settings.langfuse_public_key
    sk = settings.langfuse_secret_key
    if not pk or not sk:
        return None
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


@router.get("/traces")
async def get_traces(
    session_id: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Query LangFuse traces via REST API (langfuse SDK v4.x removed fetch_traces)."""
    headers = _langfuse_headers()
    if not headers:
        return {"enabled": False, "traces": [], "message": "LangFuse is not configured"}

    host = settings.langfuse_host.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if session_id:
                # UUID → 32-char hex for langfuse trace_id
                clean_id = session_id.replace("-", "")
                resp = await client.get(
                    f"{host}/api/public/traces/{clean_id}",
                    headers=headers,
                )
                if resp.status_code == 404:
                    return {"enabled": True, "trace": None, "message": "Trace not found"}
                resp.raise_for_status()
                return {"enabled": True, "trace": resp.json()}
            else:
                resp = await client.get(
                    f"{host}/api/public/traces",
                    headers=headers,
                    params={"limit": limit, "orderBy": "timestamp.desc"},
                )
                resp.raise_for_status()
                data = resp.json()
                traces = data.get("data", [])
                return {
                    "enabled": True,
                    "traces_count": len(traces),
                    "traces": [
                        {"id": t.get("id"), "name": t.get("name"), "timestamp": t.get("timestamp")}
                        for t in traces
                    ],
                }
    except Exception as e:
        logger.warning("LangFuse API query failed: %s", e)
        return {"enabled": True, "error": str(e)}
