"""
AXIS Demo — exercises the Memory Graph, Agent Factory, and Message Router.

Sections:
  1. Memory Graph — direct API (nodes, edges, BFS, query, decay)
  2. Agent Factory — spawn types, list, introspect
  3. Message Router — direct delivery, capability routing, topic pub/sub
  4. Multi-agent workflow — PlannerAgent dispatches tasks via TaskAgent
  5. Memory persistence — save & reload snapshot
"""

import asyncio
import time
from pathlib import Path

from core import MemoryGraph, MessageRouter, AgentFactory
from core.agent_factory import Agent, AgentConfig, AgentStatus
from core.message_router import Message, MessageType
from agents import MemoryAgent, TaskAgent, PlannerAgent
from utils.logger import get_logger

log = get_logger("axis.demo")

SEP = "─" * 60


def section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


# ---------------------------------------------------------------------------
# 1. Memory Graph — standalone
# ---------------------------------------------------------------------------

def demo_memory_graph():
    section("1. Memory Graph")

    g = MemoryGraph()

    # Add nodes
    paris     = g.add_node("Paris is the capital of France",   "fact",    tags=["geography"])
    france    = g.add_node("France is in Western Europe",       "fact",    tags=["geography"])
    eiffel    = g.add_node("Eiffel Tower is in Paris",          "fact",    tags=["landmark"])
    visit     = g.add_node("Tourist visited Eiffel Tower",      "event",   tags=["event"])
    plan      = g.add_node("Plan trip to France",               "goal",    tags=["goal"], pinned=True)

    # Link them
    g.link(paris.id,  france.id,  "part_of",     weight=1.0)
    g.link(eiffel.id, paris.id,   "located_in",  weight=1.0)
    g.link(visit.id,  eiffel.id,  "involves",    weight=0.9)
    g.link(plan.id,   france.id,  "targets",     weight=1.0)
    g.link(plan.id,   eiffel.id,  "includes",    weight=0.8)

    log.info("Graph built", **g.stats())

    # BFS from plan
    print("\nBFS from 'Plan trip to France' (depth=2):")
    for n in g.bfs(plan.id, max_depth=2):
        print(f"  [{n.node_type}] {n.content[:60]}")

    # Query
    print("\nQuery: geography facts")
    for n in g.query(node_type="fact", tags=["geography"]):
        print(f"  w={n.weight:.2f}  {n.content}")

    # Hub detection
    print("\nTop hubs by out-degree:")
    for node, deg in g.find_hubs(top_k=3):
        print(f"  {deg} edges  {node.content[:50]}")

    # Decay + prune
    for _ in range(10):
        g.decay(factor=0.7)
    removed = g.prune(min_weight=0.1)
    print(f"\nAfter 10× heavy decay: {removed} nodes pruned (pinned nodes survive)")
    log.info("After decay", **g.stats())

    # Persistence
    snap = str(Path.home() / "AXIS" / "_demo_snapshot.json")
    g.save(snap)
    g2 = MemoryGraph.load(snap)
    log.info("Snapshot round-trip OK", original=len(g), reloaded=len(g2))


# ---------------------------------------------------------------------------
# 2 & 3. Agent Factory + Message Router
# ---------------------------------------------------------------------------

async def demo_agents_and_router():
    section("2 & 3. Agent Factory + Message Router")

    graph = MemoryGraph()
    router = MessageRouter()
    factory = AgentFactory(router, graph)

    factory.register_type("memory",  MemoryAgent)
    factory.register_type("task",    TaskAgent)
    factory.register_type("planner", PlannerAgent)

    # Spawn agents
    mem = factory.spawn("memory", "Mem-A",
                        capabilities=["memory.write", "memory.read", "memory.query"],
                        topics=["mem_updates"])
    task_a = factory.spawn("task", "Task-A", capabilities=["task.run", "task.status"])
    task_b = factory.spawn("task", "Task-B", capabilities=["task.run", "task.status"])
    probe  = factory.spawn("base", "Probe",  capabilities=[])

    # Register built-in tasks
    def greet(name: str) -> str:
        return f"Hello, {name}! (from Task-A)"

    async def slow_add(x: int, y: int) -> int:
        await asyncio.sleep(0.05)
        return x + y

    task_a_agent: TaskAgent = task_a  # type: ignore
    task_b_agent: TaskAgent = task_b  # type: ignore
    task_a_agent.register_task("greet",    greet)
    task_a_agent.register_task("slow_add", slow_add)
    task_b_agent.register_task("greet",    greet)
    task_b_agent.register_task("slow_add", slow_add)

    # Start all agents
    for a in [mem, task_a, task_b, probe]:
        a.start()
    await asyncio.sleep(0.05)

    print(f"\nFactory: {factory.stats()}")
    print(f"Router:  {router.stats()}")

    # --- Direct message delivery ---
    print("\n[direct] Probe → MemoryAgent: write")
    await router.send(Message(
        sender_id=probe.id,
        recipient=mem.id,
        msg_type=MessageType.MEMORY_UPDATE,
        payload={"op": "write", "content": "AXIS demo ran successfully",
                 "node_type": "event", "tags": ["demo"]},
    ))
    await asyncio.sleep(0.1)
    reply = router.try_receive(probe.id)
    print(f"  Reply: {reply.payload if reply else 'none yet'}")

    # --- Capability routing ---
    print("\n[@task.run] Probe → capability 'task.run': greet task")
    await router.send(Message(
        sender_id=probe.id,
        recipient="@task.run",
        msg_type=MessageType.TASK,
        payload={"op": "run", "name": "greet", "args": ["AXIS"]},
    ))
    await asyncio.sleep(0.2)
    reply = router.try_receive(probe.id)
    print(f"  Reply: {reply.payload if reply else 'none yet'}")

    # --- Topic pub/sub ---
    print("\n[#mem_updates] Broadcast to topic 'mem_updates'")
    await router.send(Message(
        sender_id=probe.id,
        recipient="#mem_updates",
        msg_type=MessageType.EVENT,
        payload={"event": "graph_updated", "nodes": len(graph)},
    ))
    await asyncio.sleep(0.05)

    # --- Broadcast ---
    print("\n[broadcast] System announcement")
    await router.send(Message(
        sender_id=probe.id,
        recipient="broadcast",
        msg_type=MessageType.BROADCAST,
        payload={"announcement": "AXIS demo in progress"},
    ))
    await asyncio.sleep(0.05)

    print(f"\nRouter stats after messaging: {router.stats()}")
    print(f"Factory stats:                {factory.stats()}")

    factory.terminate_all()
    return graph, router, factory


# ---------------------------------------------------------------------------
# 4. Multi-agent workflow via PlannerAgent
# ---------------------------------------------------------------------------

async def demo_planner_workflow():
    section("4. Multi-agent Workflow — PlannerAgent")

    graph = MemoryGraph()
    router = MessageRouter()
    factory = AgentFactory(router, graph)
    factory.register_type("memory",  MemoryAgent)
    factory.register_type("task",    TaskAgent)
    factory.register_type("planner", PlannerAgent)

    mem  = factory.spawn("memory",  "Mem-W",     capabilities=["memory.write", "memory.read"])
    task = factory.spawn("task",    "Task-W",    capabilities=["task.run"])
    plan = factory.spawn("planner", "Planner-W", capabilities=["planning"])
    ctrl = factory.spawn("base",    "Controller", capabilities=[])

    # Register tasks
    def compute_fibonacci(n: int) -> list:
        a, b, result = 0, 1, [0]
        for _ in range(n - 1):
            a, b = b, a + b
            result.append(b)
        return result

    async def fetch_status() -> dict:
        await asyncio.sleep(0.02)
        return {"graph_nodes": len(graph), "status": "healthy"}

    task_agent: TaskAgent = task  # type: ignore
    task_agent.register_task("fibonacci",    compute_fibonacci)
    task_agent.register_task("fetch_status", fetch_status)

    for a in [mem, task, plan, ctrl]:
        a.start()
    await asyncio.sleep(0.05)

    # Submit plan to planner
    print("\nController → Planner: execute 3-step plan")
    reply = await router.request_reply(
        sender_id=ctrl.id,
        recipient=plan.id,
        payload={
            "op": "plan",
            "goal": "Bootstrap AXIS with diagnostics",
            "steps": [
                {"name": "fetch_status",  "fn": "fetch_status",  "required": True},
                {"name": "fibonacci(10)", "fn": "fibonacci",     "args": [10]},
                {"name": "fibonacci(15)", "fn": "fibonacci",     "args": [15]},
            ],
            "parallel": False,
        },
        timeout=15.0,
    )

    if reply:
        p = reply.payload
        print(f"  Goal:      {p.get('goal')}")
        print(f"  Completed: {p.get('completed')}/{p.get('total')}")
        for r in p.get("results", []):
            print(f"    [{r.get('status')}] {r.get('task_name')}")
            if r.get("result"):
                val = r["result"]
                if isinstance(val, list):
                    val = val[:5]
                print(f"            result = {val}")
    else:
        print("  No reply received (timeout)")

    print(f"\nGraph after workflow: {graph.stats()}")
    factory.terminate_all()


# ---------------------------------------------------------------------------
# 5. Memory persistence round-trip
# ---------------------------------------------------------------------------

async def demo_persistence():
    section("5. Memory Persistence — Save & Reload")

    graph = MemoryGraph()
    router = MessageRouter()
    factory = AgentFactory(router, graph)
    factory.register_type("memory", MemoryAgent)

    mem = factory.spawn("memory", "Mem-P", capabilities=["memory.write"])
    ctrl = factory.spawn("base",  "Ctrl-P", capabilities=[])

    mem.start()
    await asyncio.sleep(0.05)

    # Write a few nodes via MemoryAgent
    for i in range(5):
        await router.send(Message(
            sender_id=ctrl.id, recipient=mem.id,
            msg_type=MessageType.MEMORY_UPDATE,
            payload={"op": "write", "content": f"Persisted fact #{i}", "node_type": "fact"},
        ))
    await asyncio.sleep(0.2)

    snap_path = str(Path.home() / "AXIS" / "memory_graph.json")
    graph.save(snap_path)
    before = len(graph)

    g2 = MemoryGraph.load(snap_path)
    after = len(g2)
    print(f"  Nodes before save: {before}, nodes after reload: {after}")
    assert before == after, "Round-trip mismatch!"
    print("  Round-trip OK")

    factory.terminate_all()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_demo(axis=None):
    log.info("=== AXIS Demo starting ===")

    demo_memory_graph()
    await demo_agents_and_router()
    await demo_planner_workflow()
    await demo_persistence()

    section("Demo complete")
    print("  All subsystems verified.")
    log.info("=== AXIS Demo complete ===")


if __name__ == "__main__":
    asyncio.run(run_demo())
