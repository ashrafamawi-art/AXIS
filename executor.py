"""
AXIS Executor — translates Council decisions into real-world actions.

Tools:
  send_notification  → local Mac desktop notification via osascript
  save_task          → append to ~/AXIS/tasks.md
  http_request       → outbound HTTP call

Usage:
  results = await execute_action("Launch a 10-day sprint...")
  results = await execute_decision(council_decision)
"""

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import requests as http_lib

MODEL = "claude-sonnet-4-6"
TASKS_PATH = Path("~/AXIS/tasks.md").expanduser()

# ---------------------------------------------------------------------------
# Tool schemas (passed to Claude)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "send_notification",
        "description": (
            "Send a local Mac desktop notification. Use when the action involves "
            "alerting, reminding, or notifying the user of something important."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title":   {"type": "string", "description": "Short notification title"},
                "message": {"type": "string", "description": "Notification body (1-2 sentences)"},
            },
            "required": ["title", "message"],
        },
    },
    {
        "name": "save_task",
        "description": (
            "Save an action item or task to ~/AXIS/tasks.md. Use when the action "
            "involves scheduling work, tracking a to-do, or recording a next step."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task":     {"type": "string", "description": "The task description"},
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Task priority",
                },
                "due":      {"type": "string", "description": "Optional due date or timeframe, e.g. '2026-05-10' or 'within 48 hours'"},
            },
            "required": ["task", "priority"],
        },
    },
    {
        "name": "http_request",
        "description": (
            "Make an outbound HTTP request to an external API or webhook. Use when "
            "the action involves calling an API, sending data externally, or triggering "
            "a remote service."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url":     {"type": "string", "description": "Full URL including protocol"},
                "method":  {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                "headers": {"type": "object", "description": "HTTP headers as key-value pairs"},
                "body":    {"type": "string", "description": "Request body as a JSON string"},
            },
            "required": ["url", "method"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

class ToolResult:
    def __init__(self, tool: str, success: bool, output: Any, error: str = ""):
        self.tool    = tool
        self.success = success
        self.output  = output
        self.error   = error

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"[{self.tool}] {status}: {self.output or self.error}"


def _send_notification(title: str, message: str) -> ToolResult:
    safe_title   = title.replace('"', '\\"').replace("'", "\\'")
    safe_message = message.replace('"', '\\"').replace("'", "\\'")
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return ToolResult("send_notification", True, f"Notification sent: {title!r}")
    return ToolResult("send_notification", False, None, result.stderr.strip())


def _save_task(task: str, priority: str = "medium", due: str = "") -> ToolResult:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    priority_badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
    due_str = f" · due {due}" if due else ""

    if not TASKS_PATH.exists():
        TASKS_PATH.write_text("# AXIS Tasks\n\n")

    line = f"- [ ] {priority_badge} **[{priority.upper()}]** {task}{due_str}  _(added {now})_\n"
    with TASKS_PATH.open("a") as f:
        f.write(line)

    return ToolResult("save_task", True, f"Saved [{priority}] task → {TASKS_PATH}")


def _http_request(url: str, method: str = "GET", headers: dict = None, body: str = None) -> ToolResult:
    try:
        kwargs: dict = {"headers": headers or {}, "timeout": 15}
        if body:
            kwargs["data"] = body.encode()
            if "Content-Type" not in (headers or {}):
                kwargs["headers"]["Content-Type"] = "application/json"

        resp = http_lib.request(method.upper(), url, **kwargs)
        snippet = resp.text[:300] if resp.text else "(empty body)"
        return ToolResult(
            "http_request",
            resp.ok,
            {"status": resp.status_code, "body_preview": snippet},
            "" if resp.ok else f"HTTP {resp.status_code}",
        )
    except Exception as exc:
        return ToolResult("http_request", False, None, str(exc))


_TOOL_FNS = {
    "send_notification": _send_notification,
    "save_task":         _save_task,
    "http_request":      _http_request,
}


# ---------------------------------------------------------------------------
# Dispatch loop
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are the AXIS Executor. You receive a natural-language action directive "
    "and must call the appropriate tool(s) to carry it out. "
    "Always call at least one tool. Prefer calling multiple tools when the action "
    "involves both recording and notifying. Never respond with plain text — "
    "only tool calls."
)


async def execute_action(action: str, context: str = "") -> list[ToolResult]:
    """
    Parse `action` with Claude and dispatch to the appropriate tools.
    Returns a list of ToolResult, one per tool invoked.
    """
    client = anthropic.AsyncAnthropic()
    user_content = f"Action directive:\n{action}"
    if context:
        user_content += f"\n\nContext:\n{context}"

    results: list[ToolResult] = []
    messages = [{"role": "user", "content": user_content}]

    while True:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        # Collect any tool calls in this response
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            break

        # Execute each tool call
        tool_results_content = []
        for call in tool_calls:
            fn = _TOOL_FNS.get(call.name)
            if fn:
                result = fn(**call.input)
            else:
                result = ToolResult(call.name, False, None, "Unknown tool")
            results.append(result)
            tool_results_content.append({
                "type":        "tool_result",
                "tool_use_id": call.id,
                "content":     json.dumps({"success": result.success, "output": result.output, "error": result.error}),
            })

        # Feed results back for multi-turn if needed
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user",      "content": tool_results_content})

        if response.stop_reason == "end_turn":
            break

    return results


async def execute_decision(decision) -> list[ToolResult]:
    """Execute the action from a CouncilDecision object."""
    context = (
        f"Original task: {decision.task}\n"
        f"Confidence: {decision.confidence:.0%}\n"
        f"Reasoning: {decision.reasoning}"
    )
    return await execute_action(decision.action, context)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

def _print_results(results: list[ToolResult]):
    width = 72
    print(f"\n{'═' * width}")
    print(f"  AXIS EXECUTOR — {len(results)} tool(s) invoked")
    print(f"{'═' * width}")
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"\n  {status} [{r.tool}]")
        if r.success:
            print(f"    {r.output}")
        else:
            print(f"    ERROR: {r.error}")
    print(f"\n{'═' * width}\n")


if __name__ == "__main__":
    sample_action = (
        "Save this as a high-priority task: Appoint a Sprint Lead within 24 hours "
        "to run a 10-day market data due diligence sprint. Also send a desktop "
        "notification reminding the team that legal counsel must be engaged before "
        "any vendor evaluation begins."
    )

    print(f"Executing action:\n  {sample_action}\n")
    results = asyncio.run(
        execute_action(
            sample_action,
            context="Confidence: 87% | Triggered by AXIS Council Mode decision on market data expansion.",
        )
    )
    _print_results(results)

    # Show tasks.md tail
    if TASKS_PATH.exists():
        print(f"── {TASKS_PATH} (last 5 lines) ──")
        lines = TASKS_PATH.read_text().splitlines()
        for line in lines[-5:]:
            print(f"  {line}")
        print()
