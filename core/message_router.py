"""
AXIS Message Router — async pub/sub + direct delivery backbone.

Routing modes:
  agent_id        → direct delivery to a specific agent queue
  'broadcast'     → fan-out to all registered agents
  '@capability'   → least-loaded agent with that capability
  '#topic'        → all subscribers of a topic channel
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class MessageType(str, Enum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    EVENT = "EVENT"
    BROADCAST = "BROADCAST"
    MEMORY_UPDATE = "MEMORY_UPDATE"
    TASK = "TASK"
    RESULT = "RESULT"
    SYSTEM = "SYSTEM"
    HEARTBEAT = "HEARTBEAT"


@dataclass
class Message:
    payload: dict
    sender_id: str
    recipient: str
    msg_type: MessageType = MessageType.EVENT
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    priority: int = 0                    # higher = more urgent (not yet used for ordering)
    reply_to: Optional[str] = None       # message id this is a reply to
    correlation_id: Optional[str] = None # ties request/response pairs
    ttl: float = 60.0                    # seconds before expiry


class DeadLetterQueue:
    def __init__(self, max_size: int = 500):
        self._queue: list[tuple[Message, str]] = []   # (msg, reason)
        self._max = max_size

    def push(self, msg: Message, reason: str):
        self._queue.append((msg, reason))
        if len(self._queue) > self._max:
            self._queue.pop(0)

    def drain(self) -> list[tuple[Message, str]]:
        items, self._queue = self._queue, []
        return items

    def __len__(self) -> int:
        return len(self._queue)


class MessageRouter:
    """
    Central async message bus for AXIS agents.

    Registration:
        router.register(agent_id, capabilities=['memory', 'planning'])

    Sending:
        await router.send(Message(sender_id=..., recipient='agent123', ...))
        await router.send(Message(..., recipient='broadcast'))
        await router.send(Message(..., recipient='@memory'))   # capability routing
        await router.send(Message(..., recipient='#updates'))  # topic channel

    Receiving (from within an agent coroutine):
        msg = await router.receive(agent_id, timeout=5.0)
    """

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._capabilities: dict[str, list[str]] = {}   # capability -> [agent_ids]
        self._topics: dict[str, list[str]] = {}         # topic -> [agent_ids]
        self._middleware: list[Callable] = []
        self._log: list[Message] = []
        self._dlq = DeadLetterQueue()
        self._max_log = 2000
        self._delivery_count = 0
        self._drop_count = 0
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------- registration

    def register(self, agent_id: str, capabilities: list[str] = None, topics: list[str] = None):
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue(maxsize=512)
        for cap in (capabilities or []):
            self._capabilities.setdefault(cap, [])
            if agent_id not in self._capabilities[cap]:
                self._capabilities[cap].append(agent_id)
        for topic in (topics or []):
            self._topics.setdefault(topic, [])
            if agent_id not in self._topics[topic]:
                self._topics[topic].append(agent_id)

    def subscribe(self, agent_id: str, topic: str):
        if agent_id not in self._queues:
            raise ValueError(f"Agent {agent_id} not registered")
        self._topics.setdefault(topic, [])
        if agent_id not in self._topics[topic]:
            self._topics[topic].append(agent_id)

    def unsubscribe(self, agent_id: str, topic: str):
        if topic in self._topics:
            self._topics[topic] = [a for a in self._topics[topic] if a != agent_id]

    def unregister(self, agent_id: str):
        self._queues.pop(agent_id, None)
        for lst in self._capabilities.values():
            if agent_id in lst:
                lst.remove(agent_id)
        for lst in self._topics.values():
            if agent_id in lst:
                lst.remove(agent_id)

    # ------------------------------------------------------------- middleware

    def add_middleware(self, fn: Callable):
        """fn(message) -> Message | None.  Return None to drop the message."""
        self._middleware.append(fn)

    # --------------------------------------------------------------- sending

    async def send(self, message: Message) -> bool:
        now = time.time()
        if now - message.timestamp > message.ttl:
            self._dlq.push(message, "expired")
            self._drop_count += 1
            return False

        for mw in self._middleware:
            result = mw(message)
            if result is None:
                self._dlq.push(message, "middleware_drop")
                self._drop_count += 1
                return False
            message = result

        self._log.append(message)
        if len(self._log) > self._max_log:
            self._log = self._log[-self._max_log:]

        recipient = message.recipient
        delivered = False

        if recipient == "broadcast":
            for q in self._queues.values():
                try:
                    q.put_nowait(message)
                    delivered = True
                except asyncio.QueueFull:
                    self._dlq.push(message, "queue_full")
            self._delivery_count += 1
            return delivered

        if recipient.startswith("@"):
            cap = recipient[1:]
            agents = [a for a in self._capabilities.get(cap, []) if a in self._queues]
            if not agents:
                self._dlq.push(message, f"no_agent_for_capability:{cap}")
                self._drop_count += 1
                return False
            target = min(agents, key=lambda a: self._queues[a].qsize())
            try:
                self._queues[target].put_nowait(message)
                self._delivery_count += 1
                return True
            except asyncio.QueueFull:
                self._dlq.push(message, "queue_full")
                self._drop_count += 1
                return False

        if recipient.startswith("#"):
            topic = recipient[1:]
            subscribers = [a for a in self._topics.get(topic, []) if a in self._queues]
            for agent_id in subscribers:
                try:
                    self._queues[agent_id].put_nowait(message)
                    delivered = True
                except asyncio.QueueFull:
                    self._dlq.push(message, "queue_full")
            if delivered:
                self._delivery_count += 1
            return delivered

        if recipient in self._queues:
            try:
                self._queues[recipient].put_nowait(message)
                self._delivery_count += 1
                return True
            except asyncio.QueueFull:
                self._dlq.push(message, "queue_full")
                self._drop_count += 1
                return False

        self._dlq.push(message, f"unknown_recipient:{recipient}")
        self._drop_count += 1
        return False

    # ------------------------------------------------------------- receiving

    async def receive(self, agent_id: str, timeout: float = None) -> Optional[Message]:
        q = self._queues.get(agent_id)
        if q is None:
            return None
        try:
            if timeout is not None:
                return await asyncio.wait_for(q.get(), timeout=timeout)
            return await q.get()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None

    def try_receive(self, agent_id: str) -> Optional[Message]:
        q = self._queues.get(agent_id)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def request_reply(
        self,
        sender_id: str,
        recipient: str,
        payload: dict,
        timeout: float = 10.0,
    ) -> Optional[Message]:
        """Send a REQUEST and block until a RESPONSE with matching correlation_id arrives."""
        corr_id = str(uuid.uuid4())
        msg = Message(
            sender_id=sender_id,
            recipient=recipient,
            payload=payload,
            msg_type=MessageType.REQUEST,
            correlation_id=corr_id,
        )
        await self.send(msg)
        deadline = time.time() + timeout
        while time.time() < deadline:
            reply = await self.receive(sender_id, timeout=min(1.0, deadline - time.time()))
            if reply and reply.correlation_id == corr_id:
                return reply
        return None

    # ----------------------------------------------------------- introspection

    def get_log(self, limit: int = 100, msg_type: MessageType = None) -> list[Message]:
        log = self._log[-limit:]
        if msg_type:
            log = [m for m in log if m.msg_type == msg_type]
        return log

    def queue_size(self, agent_id: str) -> int:
        q = self._queues.get(agent_id)
        return q.qsize() if q else 0

    def stats(self) -> dict:
        return {
            "registered_agents": len(self._queues),
            "capabilities": {k: len(v) for k, v in self._capabilities.items()},
            "topics": {k: len(v) for k, v in self._topics.items()},
            "total_delivered": self._delivery_count,
            "total_dropped": self._drop_count,
            "dlq_size": len(self._dlq),
            "log_size": len(self._log),
            "queue_sizes": {aid: q.qsize() for aid, q in self._queues.items()},
        }
