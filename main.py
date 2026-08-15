from enum import Enum
import logging
import os
from typing import Any, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


class EventType(str, Enum):
    login = "login"
    logout = "logout"
    heartbeat = "heartbeat"
    other = "other"


class LogEntry(BaseModel):
    hostname: str
    tailscale_ip: str
    event_type: EventType


# Initialize Supabase client at startup and validate required env vars
@app.on_event("startup")
def startup_event() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables")
        # Fail fast so deployment problems are obvious
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    app.state.supabase: Client = create_client(url, key)
    logger.info("Supabase client initialized")


@app.on_event("shutdown")
def shutdown_event() -> None:
    # supabase-py doesn't currently provide a close method for the client,
    # but if you use other resources that require cleanup, do it here.
    logger.info("Shutting down app")


@app.post("/api/log")
async def create_log(entry: LogEntry) -> Any:
    supabase: Client = app.state.supabase
    payload = {
        "hostname": entry.hostname,
        "tailscale_ip": entry.tailscale_ip,
        "event_type": entry.event_type.value,
    }

    try:
        def db_insert():
            # Return the inserted row(s) by chaining .select("*")
            return supabase.table("activity_logs").insert(payload).select("*").execute()

        result = await run_in_threadpool(db_insert)
        # result.data is usually the inserted rows
        return {"status": "success", "data": result.data}
    except Exception as exc:
        logger.exception("Failed to insert log")
        raise HTTPException(status_code=500, detail="Failed to create log")


@app.get("/api/logs")
async def get_logs(limit: int = Query(50, ge=1, le=200)) -> List[Any]:
    """
    Returns the latest `limit` logs (default 50). Limit is capped for safety.
    """
    supabase: Client = app.state.supabase

    try:
        def db_query():
            return (
                supabase.table("activity_logs")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

        result = await run_in_threadpool(db_query)
        return result.data or []
    except Exception:
        logger.exception("Failed to fetch logs")
        raise HTTPException(status_code=500, detail="Failed to fetch logs")


if __name__ == "__main__":
    import uvicorn

    # For local development. In Render or production, use your process manager or platform config.
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
