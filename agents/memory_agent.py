"""
AXIS MemoryAgent — specialised agent for graph memory operations.

Capabilities: memory.write, memory.read, memory.link, memory.query, memory.decay

Handles inbound messages of type MEMORY_UPDATE (write) and REQUEST (read/query).
Periodically applies decay and prunes stale nodes.
"""

import asyncio
import time

from core.agent_factory import Agent, AgentConfig, AgentStatus
from core.message_router import Message, MessageType


class MemoryAgent(Agent):

    DECAY_INTERVAL = 60.0   # seconds between decay cycles
    PRUNE_THRESHOLD = 0.05

    def __init__(self, config: AgentConfig, router, graph):
        super().__init__(config, router, graph)
        self._last_decay = time.time()
        self._ops: dict[str, int] = {
            "writes": 0, "reads": 0, "links": 0, "queries": 0, "prunes": 0
        }

    async def on_start(self):
        seed = self.remember(
            f"MemoryAgent '{self.name}' online",
            node_type="state",
            pinned=True,
        )
        self._seed_node_id = seed.id

    async def run(self):
        while self._running:
            msg = await self.receive(timeout=2.0)
            if msg:
                await self._dispatch(msg)
            await self._maybe_decay()

    async def _dispatch(self, msg: Message):
        op = msg.payload.get("op", "")

        if msg.msg_type == MessageType.MEMORY_UPDATE or op == "write":
            await self._handle_write(msg)

        elif op == "read":
            await self._handle_read(msg)

        elif op == "link":
            await self._handle_link(msg)

        elif op == "query":
            await self._handle_query(msg)

        elif op == "stats":
            await self._handle_stats(msg)

        elif op == "decay":
            removed = self._do_decay()
            await self._reply(msg, {"removed": removed, "op": "decay_result"})

    async def _handle_write(self, msg: Message):
        p = msg.payload
        node = self._graph.add_node(
            content=p.get("content", ""),
            node_type=p.get("node_type", "fact"),
            tags=p.get("tags", []),
            metadata={**p.get("metadata", {}), "written_by": msg.sender_id},
        )
        self._ops["writes"] += 1
        await self._reply(msg, {"node_id": node.id, "op": "write_ok"})

    async def _handle_read(self, msg: Message):
        node_id = msg.payload.get("node_id")
        node = self._graph.get_node(node_id)
        self._ops["reads"] += 1
        if node:
            await self._reply(msg, {
                "op": "read_ok",
                "node": {
                    "id": node.id, "content": node.content,
                    "type": node.node_type, "weight": node.weight,
                    "tags": node.tags, "metadata": node.metadata,
                }
            })
        else:
            await self._reply(msg, {"op": "read_miss", "node_id": node_id})

    async def _handle_link(self, msg: Message):
        p = msg.payload
        edge = self._graph.link(
            source_id=p["source_id"],
            target_id=p["target_id"],
            relationship=p.get("relationship", "related_to"),
            weight=p.get("weight", 1.0),
        )
        self._ops["links"] += 1
        await self._reply(msg, {"op": "link_ok", "success": edge is not None})

    async def _handle_query(self, msg: Message):
        p = msg.payload
        nodes = self._graph.query(
            node_type=p.get("node_type"),
            tags=p.get("tags"),
            content_contains=p.get("content_contains"),
            limit=p.get("limit", 20),
        )
        self._ops["queries"] += 1
        await self._reply(msg, {
            "op": "query_ok",
            "results": [
                {"id": n.id, "content": n.content, "type": n.node_type, "weight": n.weight}
                for n in nodes
            ],
            "count": len(nodes),
        })

    async def _handle_stats(self, msg: Message):
        await self._reply(msg, {
            "op": "stats_ok",
            "graph": self._graph.stats(),
            "agent_ops": dict(self._ops),
        })

    async def _reply(self, original: Message, payload: dict):
        if original.sender_id and original.sender_id != self.id:
            await self.send(
                recipient=original.sender_id,
                payload=payload,
                msg_type=MessageType.RESPONSE,
                reply_to=original.id,
                correlation_id=original.correlation_id,
            )

    async def _maybe_decay(self):
        if time.time() - self._last_decay >= self.DECAY_INTERVAL:
            self._do_decay()
            self._last_decay = time.time()

    def _do_decay(self) -> int:
        self._graph.decay()
        removed = self._graph.prune(self.PRUNE_THRESHOLD)
        self._ops["prunes"] += removed
        return removed
