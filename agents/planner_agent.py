"""
AXIS PlannerAgent — decomposes goals into ordered task steps and
dispatches them to available TaskAgents via capability routing.

Capabilities: planning, orchestration

Message protocol:
  Inbound  { op: 'plan', goal: str, steps: [{name, fn, args, kwargs}] }
  Outbound { op: 'plan_result', goal, completed, failed, results }
"""

import asyncio
import time

from core.agent_factory import Agent, AgentConfig, AgentStatus
from core.message_router import Message, MessageType


class PlannerAgent(Agent):

    def __init__(self, config: AgentConfig, router, graph):
        super().__init__(config, router, graph)
        self._plans: list[dict] = []

    async def on_start(self):
        self.remember(
            f"PlannerAgent '{self.name}' online",
            node_type="state",
            pinned=True,
        )

    async def run(self):
        while self._running:
            msg = await self.receive(timeout=2.0)
            if msg:
                await self._dispatch(msg)

    async def _dispatch(self, msg: Message):
        op = msg.payload.get("op", "")
        if op == "plan" or msg.msg_type == MessageType.TASK:
            await self._execute_plan(msg)
        elif op == "list_plans":
            await self._reply(msg, {"op": "plans_ok", "plans": self._plans[-10:]})

    async def _execute_plan(self, msg: Message):
        goal = msg.payload.get("goal", "unnamed goal")
        steps: list[dict] = msg.payload.get("steps", [])
        parallel = msg.payload.get("parallel", False)

        plan_record = {
            "goal": goal,
            "started_at": time.time(),
            "steps": len(steps),
            "completed": 0,
            "failed": 0,
            "results": [],
        }
        self._plans.append(plan_record)

        plan_node = self.remember(
            f"Plan started: {goal}",
            node_type="goal",
            tags=["plan", "active"],
        )

        if parallel:
            results = await self._run_parallel(steps)
        else:
            results = await self._run_sequential(steps)

        completed = sum(1 for r in results if r.get("status") == "done")
        failed = len(results) - completed
        plan_record.update({
            "completed": completed,
            "failed": failed,
            "results": results,
            "finished_at": time.time(),
        })

        status_content = f"Plan '{goal}': {completed}/{len(steps)} steps done, {failed} failed"
        result_node = self.remember(status_content, node_type="event", tags=["plan", "result"])
        self._graph.link(plan_node.id, result_node.id, "led_to")

        await self._reply(msg, {
            "op": "plan_result",
            "goal": goal,
            "completed": completed,
            "failed": failed,
            "total": len(steps),
            "results": results,
        })

    async def _run_sequential(self, steps: list[dict]) -> list[dict]:
        results = []
        for step in steps:
            result = await self._dispatch_step(step)
            results.append(result)
            if result.get("status") == "error" and step.get("required", True):
                for remaining in steps[len(results):]:
                    results.append({
                        "task_name": remaining.get("name", "?"),
                        "status": "skipped",
                        "reason": "prior step failed",
                    })
                break
        return results

    async def _run_parallel(self, steps: list[dict]) -> list[dict]:
        tasks = [asyncio.create_task(self._dispatch_step(step)) for step in steps]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _dispatch_step(self, step: dict) -> dict:
        reply = await self._router.request_reply(
            sender_id=self.id,
            recipient="@task.run",
            payload={"op": "run", **step},
            timeout=step.get("timeout", 30.0),
        )
        if reply:
            return reply.payload
        return {"task_name": step.get("name", "?"), "status": "error", "error": "timeout"}

    async def _reply(self, original: Message, payload: dict):
        if original.sender_id and original.sender_id != self.id:
            await self.send(
                recipient=original.sender_id,
                payload=payload,
                msg_type=MessageType.RESULT,
                reply_to=original.id,
                correlation_id=original.correlation_id,
            )
