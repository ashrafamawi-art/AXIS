# AXIS — Memory Graph System

A Python agent framework with three core subsystems:

| Component | What it does |
|---|---|
| **MemoryGraph** | Directed weighted graph — stores typed nodes (facts, events, goals) and labelled edges with decay |
| **AgentFactory** | Spawns and manages typed agents (MemoryAgent, TaskAgent, PlannerAgent) |
| **MessageRouter** | Async pub/sub bus — direct, broadcast, `@capability`, and `#topic` routing |

## Project layout

```
AXIS/
├── server.py          ← FastAPI remote entry point (Railway)
├── main.py            ← CLI entry point
├── demo.py            ← End-to-end demonstration
├── config.py          ← System-wide constants
├── requirements.txt
├── core/
│   ├── memory_graph.py
│   ├── agent_factory.py
│   ├── message_router.py
│   └── task_runner.py
├── agents/
│   ├── memory_agent.py
│   ├── task_agent.py
│   └── planner_agent.py
├── tasks/
│   ├── news.py        ← AI news fetcher
│   ├── file_io.py
│   └── inbox.json     ← Queued tasks (fallback when core unavailable)
└── utils/
    └── logger.py
```

## API (server.py)

### `GET /health`

```json
{"status": "ok", "axis_core": true}
```

### `POST /task`

```bash
curl -X POST https://<your-app>.railway.app/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Search for the top 3 AI news stories today and save a summary to daily_brief.md"}'
```

**Response — AXIS core available (200):**
```json
{
  "id": "...",
  "task": "...",
  "status": "done",
  "message": "Saved 3 stories → /app/AXIS/daily_brief.md",
  "artifacts": {"path": "...", "story_count": 3},
  "timestamp": "2026-04-30T13:41:10+00:00"
}
```

**Response — AXIS core unavailable, queued (202):**
```json
{
  "id": "...",
  "task": "...",
  "status": "queued",
  "queued_at": "...",
  "message": "AXIS core unavailable — task queued in inbox for later processing.",
  "inbox": "/app/AXIS/tasks/inbox.json"
}
```

## Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start the API server
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# Or use the CLI
python3 main.py --demo
python3 main.py --task "Search for the top 3 AI news stories today and save a summary to daily_brief.md"
python3 main.py --stats
```

## Deploying to Railway

### Start command

```
uvicorn server:app --host 0.0.0.0 --port $PORT
```

### Steps

1. Push this directory to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo**.
3. Railway auto-detects `requirements.txt` and installs dependencies.
4. Set the start command above in **Settings → Deploy → Start Command**.
5. Railway injects `$PORT` automatically — no environment variables needed.

### Notes

- The app boots AXIS core on startup. If any import fails it degrades to inbox-queue mode automatically.
- `memory_graph.json` is written to the Railway ephemeral filesystem. For persistence across deploys, mount a Railway Volume at `/app/AXIS` or swap the snapshot path to an environment variable pointing to object storage.
