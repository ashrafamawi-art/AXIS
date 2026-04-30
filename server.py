"""
AXIS remote entry point — FastAPI server for Railway deployment.

Endpoints:
  GET  /health   → {"status": "ok"}
  POST /task     → run or queue a natural-language task
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# AXIS integration — attempt to import; degrade gracefully if env is minimal
# ---------------------------------------------------------------------------

AXIS_DIR = Path(__file__).parent
INBOX_PATH = AXIS_DIR / "tasks" / "inbox.json"

_axis_available = False
_graph = None
_runner = None

def _boot_axis():
    global _axis_available, _graph, _runner
    try:
        sys.path.insert(0, str(AXIS_DIR))
        from core.memory_graph import MemoryGraph
        from core.task_runner import TaskRunner
        from utils.logger import get_logger

        snap = AXIS_DIR / "memory_graph.json"
        _graph = MemoryGraph.load(str(snap)) if snap.exists() else MemoryGraph()
        _runner = TaskRunner(graph=_graph, logger=get_logger("axis.server"))
        _axis_available = True
    except Exception as exc:
        print(f"[AXIS] Core import failed — falling back to inbox queue: {exc}", flush=True)

_boot_axis()

# ---------------------------------------------------------------------------
# Inbox helpers (fallback when AXIS core unavailable)
# ---------------------------------------------------------------------------

def _read_inbox() -> list[dict]:
    if INBOX_PATH.exists():
        return json.loads(INBOX_PATH.read_text())
    return []

def _write_inbox(items: list[dict]):
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INBOX_PATH.write_text(json.dumps(items, indent=2))

def _enqueue(task_str: str) -> dict:
    record = {
        "id":         str(uuid.uuid4()),
        "task":       task_str,
        "status":     "queued",
        "queued_at":  datetime.now(timezone.utc).isoformat(),
    }
    items = _read_inbox()
    items.append(record)
    _write_inbox(items)
    return record

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="AXIS Agent Service", version="1.0.0")


class TaskRequest(BaseModel):
    task: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/task")
async def run_task(body: TaskRequest):
    task_str = body.task.strip()
    if not task_str:
        raise HTTPException(status_code=400, detail="task must be a non-empty string")

    if _axis_available and _runner is not None:
        try:
            result = await _runner.run(task_str)
            return {
                "id":        str(uuid.uuid4()),
                "task":      task_str,
                "status":    "done" if result.success else "error",
                "message":   result.message,
                "artifacts": result.artifacts,
                "timestamp": result.timestamp,
            }
        except Exception as exc:
            # Don't crash the server — fall through to inbox
            print(f"[AXIS] Runner error: {exc}", flush=True)

    # Fallback: store in inbox
    record = _enqueue(task_str)
    return JSONResponse(status_code=202, content={
        **record,
        "message": "AXIS core unavailable — task queued in inbox for later processing.",
        "inbox":   str(INBOX_PATH),
    })


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
