"""
AXIS Council Mode — five specialist roles analyze a task in parallel,
then AXIS synthesizes their perspectives into a single structured decision.

Roles:  Planner · Analyst · Critic · Optimizer · Executor
Model:  claude-sonnet-4-6
Output: CouncilDecision (Pydantic)
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import anthropic
from pydantic import BaseModel

MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class CouncilDecision(BaseModel):
    task: str
    timestamp: str
    council: dict[str, str]      # role -> perspective
    synthesis: str                # narrative summary of all perspectives
    action: str                   # recommended action (imperative sentence)
    reasoning: str                # why this action was chosen
    confidence: float             # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

ROLES: dict[str, str] = {
    "Planner": (
        "You are the Planner on the AXIS Council. "
        "Your role is to break any task into clear, sequenced steps. "
        "Identify dependencies, milestones, and the critical path. "
        "Be concrete and time-aware. Avoid vagueness. "
        "Output a short, structured plan — bullet points preferred."
    ),
    "Analyst": (
        "You are the Analyst on the AXIS Council. "
        "Your role is to examine the task from a data and evidence perspective. "
        "Identify what information is known, what is uncertain, and what metrics "
        "matter. Surface hidden assumptions. Be precise and quantitative where possible. "
        "Output a concise analysis with key findings."
    ),
    "Critic": (
        "You are the Critic on the AXIS Council. "
        "Your role is to challenge the task, surface risks, failure modes, and "
        "blind spots that others might miss. Ask hard questions. Identify what "
        "could go wrong and why. Be constructive — criticism should drive improvement, "
        "not paralysis. Output a focused critique with the top concerns."
    ),
    "Optimizer": (
        "You are the Optimizer on the AXIS Council. "
        "Your role is to find the most efficient path to success. Look for shortcuts, "
        "leverage, automation, and ways to reduce cost, time, or effort without "
        "sacrificing quality. Prioritize ruthlessly. Output actionable optimizations."
    ),
    "Executor": (
        "You are the Executor on the AXIS Council. "
        "Your role is to translate intent into action. Define the first concrete step "
        "that should be taken right now. Identify the resources, tools, and people needed. "
        "Remove ambiguity — the output of your analysis should be something that can be "
        "acted on immediately. Output a clear, direct execution brief."
    ),
}


# ---------------------------------------------------------------------------
# Single role consultation
# ---------------------------------------------------------------------------

async def _consult_role(
    client: anthropic.AsyncAnthropic,
    task: str,
    role: str,
    system_prompt: str,
) -> tuple[str, str]:
    """Call Claude as a specific council role. Returns (role, perspective)."""
    response = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": f"Council task:\n\n{task}"}
        ],
    )
    text = response.content[0].text if response.content else ""
    return role, text.strip()


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = (
    "You are AXIS, an AI orchestration system. "
    "You have just received perspectives from five council members — "
    "Planner, Analyst, Critic, Optimizer, and Executor — on a given task. "
    "Your job is to synthesize these into a single authoritative decision. "
    "Be decisive. Weigh all perspectives fairly. Output valid JSON only."
)

_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "synthesis":   {"type": "string", "description": "Narrative summary integrating all five perspectives"},
        "action":      {"type": "string", "description": "The single recommended action in one imperative sentence"},
        "reasoning":   {"type": "string", "description": "Why this action was chosen over alternatives"},
        "confidence":  {"type": "number", "description": "Confidence score between 0.0 and 1.0"},
    },
    "required": ["synthesis", "action", "reasoning", "confidence"],
}


async def _synthesize(
    client: anthropic.AsyncAnthropic,
    task: str,
    perspectives: dict[str, str],
) -> dict:
    """Synthesize five role perspectives into a structured decision."""
    council_block = "\n\n".join(
        f"### {role}\n{text}" for role, text in perspectives.items()
    )
    user_message = (
        f"Original task:\n{task}\n\n"
        f"Council perspectives:\n\n{council_block}\n\n"
        "Synthesize these into a JSON decision object."
    )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _SYNTHESIS_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": user_message}
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _DECISION_SCHEMA,
            }
        },
    )
    raw = response.content[0].text if response.content else "{}"
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def council_mode(task: str) -> CouncilDecision:
    """
    Run AXIS Council Mode on a task.

    Fires all five role consultations in parallel, then synthesizes the
    results into a single CouncilDecision.
    """
    client = anthropic.AsyncAnthropic()
    timestamp = datetime.now(timezone.utc).isoformat()

    # Parallel role consultations
    role_tasks = [
        _consult_role(client, task, role, prompt)
        for role, prompt in ROLES.items()
    ]
    results = await asyncio.gather(*role_tasks)
    perspectives = dict(results)  # {role: perspective}

    # Synthesis
    decision_data = await _synthesize(client, task, perspectives)

    return CouncilDecision(
        task=task,
        timestamp=timestamp,
        council=perspectives,
        synthesis=decision_data["synthesis"],
        action=decision_data["action"],
        reasoning=decision_data["reasoning"],
        confidence=float(decision_data["confidence"]),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

def _print_decision(d: CouncilDecision):
    width = 72
    sep = "─" * width

    print(f"\n{'═' * width}")
    print(f"  AXIS COUNCIL DECISION")
    print(f"{'═' * width}")
    print(f"  Task : {d.task}")
    print(f"  Time : {d.timestamp}")
    print(f"  Conf : {d.confidence:.0%}")
    print(sep)

    for role, perspective in d.council.items():
        print(f"\n[{role.upper()}]")
        for line in perspective.splitlines():
            print(f"  {line}")

    print(f"\n{sep}")
    print(f"\n[SYNTHESIS]")
    for line in d.synthesis.splitlines():
        print(f"  {line}")

    print(f"\n[ACTION]")
    print(f"  {d.action}")

    print(f"\n[REASONING]")
    for line in d.reasoning.splitlines():
        print(f"  {line}")

    print(f"\n{'═' * width}\n")


if __name__ == "__main__":
    sample_task = (
        "AXIS needs to expand its capabilities to handle real-time market data "
        "and generate automated trading signals. Should we build this in-house, "
        "use a third-party API, or partner with a fintech firm?"
    )

    print(f"Consulting AXIS Council on task…\n  {sample_task}\n")
    decision = asyncio.run(council_mode(sample_task))
    _print_decision(decision)
