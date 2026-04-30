"""
AXIS TaskAgent — executes callable tasks dispatched via messages.

Capabilities: task.run, task.status

A task is a dict with:
  name      : str
  fn        : importable dotted path OR a key in the agent's task registry
  args      : list   (optional)
  kwargs    : dict   (optional)

Results are sent back to the requester as RESULT messages.
"""

import asyncio
import importlib
import time
import traceback

from core.agent_factory import Agent, AgentConfig, AgentStatus
from core.message_router import Message, MessageType


class TaskAgent(Agent):

    def __init__(self, config: AgentConfig, router, graph):
        super().__init__(config, router, graph)
        self._task_registry: dict[str, callable] = {}
        self._history: list[dict] = []     # last N task records
        self._max_history = 100
        self._running_task: dict = {}

    def register_task(self, name: str, fn: callable):
        """Register a local callable under a name agents can invoke by name."""
        self._task_registry[name] = fn

    async def on_start(self):
        self.remember(
            f"TaskAgent '{self.name}' online, registry={list(self._task_registry.keys())}",
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

        if msg.msg_type in (MessageType.TASK, MessageType.REQUEST) or op == "run":
            await self._execute(msg)
        elif op == "status":
            await self._report_status(msg)
        elif op == "list_tasks":
            await self._reply(msg, {"op": "list_ok", "tasks": list(self._task_registry.keys())})
        elif op == "history":
            await self._reply(msg, {"op": "history_ok", "history": self._history[-20:]})

    async def _execute(self, msg: Message):
        p = msg.payload
        task_name = p.get("name", "unnamed")
        fn_path = p.get("fn")
        args = p.get("args", [])
        kwargs = p.get("kwargs", {})

        record = {
            "task_name": task_name,
            "started_at": time.time(),
            "sender_id": msg.sender_id,
            "status": "running",
            "result": None,
            "error": None,
        }
        self._running_task = record
        prev_status = self.status
        self.status = AgentStatus.BUSY

        try:
            fn = self._resolve_fn(fn_path or task_name)
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: fn(*args, **kwargs)
                )
            record["status"] = "done"
            record["result"] = result
            record["finished_at"] = time.time()

            mem_node = self.remember(
                f"Task '{task_name}' completed successfully",
                node_type="event",
                tags=["task", "success"],
            )

            await self._reply(msg, {
                "op": "task_result",
                "task_name": task_name,
                "status": "done",
                "result": result,
                "duration": record["finished_at"] - record["started_at"],
                "memory_node_id": mem_node.id,
            })

        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            record["traceback"] = traceback.format_exc()
            record["finished_at"] = time.time()

            self.remember(
                f"Task '{task_name}' failed: {exc}",
                node_type="event",
                tags=["task", "error"],
            )

            await self._reply(msg, {
                "op": "task_result",
                "task_name": task_name,
                "status": "error",
                "error": str(exc),
                "duration": record["finished_at"] - record["started_at"],
            })

        finally:
            self._history.append(record)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._running_task = {}
            self.status = AgentStatus.IDLE if self._running else AgentStatus.TERMINATED

    def _resolve_fn(self, fn_path: str) -> callable:
        if fn_path in self._task_registry:
            return self._task_registry[fn_path]
        if "." in fn_path:
            module_path, fn_name = fn_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            return getattr(module, fn_name)
        raise ValueError(f"Cannot resolve function: {fn_path!r}")

    async def _report_status(self, msg: Message):
        await self._reply(msg, {
            "op": "status_ok",
            "status": self.status.value,
            "running_task": self._running_task.get("task_name"),
            "history_count": len(self._history),
        })

    async def _reply(self, original: Message, payload: dict):
        if original.sender_id and original.sender_id != self.id:
            await self.send(
                recipient=original.sender_id,
                payload=payload,
                msg_type=MessageType.RESULT,
                reply_to=original.id,
                correlation_id=original.correlation_id,
            )
