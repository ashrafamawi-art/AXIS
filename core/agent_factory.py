"""
AXIS Agent Factory — lifecycle management for all agent instances.

The factory owns the canonical registry of running agents, wires each
new agent to the shared MemoryGraph and MessageRouter, and exposes
spawn / get / list / terminate as the single control plane.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Type


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class AgentConfig:
    name: str
    agent_type: str
    capabilities: list = field(default_factory=list)
    topics: list = field(default_factory=list)            # topic channels to subscribe
    memory_context: list = field(default_factory=list)    # seed memory node IDs
    metadata: dict = field(default_factory=dict)
    max_memory_nodes: int = 200
    heartbeat_interval: float = 30.0


class Agent:
    """
    Base agent. Subclass and override `run()` to implement behaviour.

    Every agent automatically has:
      - A unique ID and named slot in the MessageRouter
      - Read/write access to the shared MemoryGraph
      - `remember()` / `recall()` helpers scoped to this agent
      - `send()` / `receive()` wrappers around the router
    """

    def __init__(self, config: AgentConfig, router, graph):
        self.id: str = str(uuid.uuid4())
        self.config = config
        self.name: str = config.name
        self.agent_type: str = config.agent_type
        self.capabilities: list[str] = list(config.capabilities)
        self.status: AgentStatus = AgentStatus.IDLE
        self.memory_context: list[str] = list(config.memory_context)
        self.created_at: float = time.time()
        self.last_active: float = time.time()
        self.message_count: int = 0
        self.error: Optional[str] = None

        self._router = router
        self._graph = graph
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    # ----------------------------------------------------------- memory helpers

    def remember(
        self,
        content: str,
        node_type: str = "fact",
        tags: list = None,
        metadata: dict = None,
        pinned: bool = False,
    ):
        node = self._graph.add_node(
            content=content,
            node_type=node_type,
            tags=(tags or []) + [self.agent_type, self.name],
            metadata={**(metadata or {}), "agent_id": self.id, "agent_name": self.name},
            pinned=pinned,
        )
        self.memory_context.append(node.id)
        if len(self.memory_context) > self.config.max_memory_nodes:
            self.memory_context = self.memory_context[-self.config.max_memory_nodes:]
        return node

    def recall(
        self,
        content_contains: str = None,
        node_type: str = None,
        tags: list = None,
        limit: int = 10,
    ):
        return self._graph.query(
            node_type=node_type,
            tags=tags or [self.name],
            content_contains=content_contains,
            limit=limit,
        )

    def link_memories(self, source_id: str, target_id: str, relationship: str, weight: float = 1.0):
        return self._graph.link(source_id, target_id, relationship, weight)

    def get_memory_context(self, depth: int = 2) -> list:
        """BFS from every node in this agent's context."""
        visited: set[str] = set()
        result = []
        for nid in self.memory_context:
            for node in self._graph.bfs(nid, max_depth=depth):
                if node.id not in visited:
                    visited.add(node.id)
                    result.append(node)
        return result

    # --------------------------------------------------------- messaging helpers

    async def send(
        self,
        recipient: str,
        payload: dict,
        msg_type=None,
        reply_to: str = None,
        correlation_id: str = None,
        priority: int = 0,
    ):
        from core.message_router import Message, MessageType
        msg = Message(
            sender_id=self.id,
            recipient=recipient,
            payload=payload,
            msg_type=msg_type or MessageType.EVENT,
            reply_to=reply_to,
            correlation_id=correlation_id,
            priority=priority,
        )
        self.message_count += 1
        self.last_active = time.time()
        return await self._router.send(msg)

    async def receive(self, timeout: float = 5.0):
        msg = await self._router.receive(self.id, timeout=timeout)
        if msg:
            self.last_active = time.time()
            self.message_count += 1
        return msg

    async def broadcast(self, payload: dict, msg_type=None):
        from core.message_router import MessageType
        return await self.send("broadcast", payload, msg_type=msg_type or MessageType.BROADCAST)

    async def publish(self, topic: str, payload: dict):
        from core.message_router import MessageType
        return await self.send(f"#{topic}", payload, msg_type=MessageType.EVENT)

    async def request(self, recipient: str, payload: dict, timeout: float = 10.0):
        return await self._router.request_reply(self.id, recipient, payload, timeout=timeout)

    # -------------------------------------------------------------- lifecycle

    async def on_start(self):
        """Called once before run(). Override for setup."""
        pass

    async def on_stop(self):
        """Called once after run() exits. Override for teardown."""
        pass

    async def run(self):
        """Main agent loop. Override in subclasses."""
        pass

    def start(self) -> asyncio.Task:
        if self._running:
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._lifecycle(), name=f"agent:{self.name}")
        return self._task

    async def _lifecycle(self):
        try:
            self.status = AgentStatus.BUSY
            await self.on_start()
            await self.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.status = AgentStatus.ERROR
            self.error = str(exc)
            raise
        finally:
            self._running = False
            if self.status != AgentStatus.ERROR:
                self.status = AgentStatus.TERMINATED
            try:
                await self.on_stop()
            except Exception:
                pass

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self.status = AgentStatus.TERMINATED

    def is_alive(self) -> bool:
        return self._running and (self._task is None or not self._task.done())

    # ----------------------------------------------------------- serialisation

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "agent_type": self.agent_type,
            "capabilities": self.capabilities,
            "status": self.status.value,
            "memory_context_size": len(self.memory_context),
            "message_count": self.message_count,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "error": self.error,
        }

    def __repr__(self) -> str:
        return f"<Agent {self.name!r} ({self.agent_type}) [{self.status.value}]>"


# ---------------------------------------------------------------------------
# Agent Factory
# ---------------------------------------------------------------------------

class AgentFactory:
    """
    Creates, tracks, and terminates agents.

    Usage:
        factory = AgentFactory(router, graph)
        factory.register_type('memory', MemoryAgent)
        agent = factory.spawn('memory', name='Mem-1', capabilities=['storage'])
        agent.start()
    """

    def __init__(self, router, graph):
        self._router = router
        self._graph = graph
        self._registry: dict[str, Type[Agent]] = {"base": Agent}
        self._agents: dict[str, Agent] = {}

    def register_type(self, agent_type: str, cls: Type[Agent]):
        self._registry[agent_type] = cls

    def spawn(
        self,
        agent_type: str,
        name: str,
        capabilities: list = None,
        topics: list = None,
        metadata: dict = None,
        autostart: bool = False,
        **kwargs,
    ) -> Agent:
        cls = self._registry.get(agent_type, Agent)
        config = AgentConfig(
            name=name,
            agent_type=agent_type,
            capabilities=capabilities or [],
            topics=topics or [],
            metadata=metadata or {},
            **kwargs,
        )
        agent = cls(config=config, router=self._router, graph=self._graph)
        self._agents[agent.id] = agent
        self._router.register(agent.id, capabilities=agent.capabilities, topics=topics or [])
        if autostart:
            agent.start()
        return agent

    def get(self, agent_id: str) -> Optional[Agent]:
        return self._agents.get(agent_id)

    def get_by_name(self, name: str) -> Optional[Agent]:
        for a in self._agents.values():
            if a.name == name:
                return a
        return None

    def list(
        self,
        agent_type: str = None,
        status: AgentStatus = None,
        capability: str = None,
    ) -> list[Agent]:
        agents = list(self._agents.values())
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]
        if status:
            agents = [a for a in agents if a.status == status]
        if capability:
            agents = [a for a in agents if capability in a.capabilities]
        return agents

    def terminate(self, agent_id: str) -> bool:
        agent = self._agents.pop(agent_id, None)
        if not agent:
            return False
        agent.stop()
        self._router.unregister(agent_id)
        return True

    def terminate_all(self):
        for agent_id in list(self._agents):
            self.terminate(agent_id)

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for a in self._agents.values():
            by_type[a.agent_type] = by_type.get(a.agent_type, 0) + 1
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        return {
            "total": len(self._agents),
            "by_type": by_type,
            "by_status": by_status,
            "registered_types": list(self._registry.keys()),
        }
