"""
AXIS — Memory Graph System with Agent Factory and Message Router.

Entry point. Boots the system, registers agent types, optionally loads
a persisted memory snapshot, then runs the asyncio event loop.

Usage:
    python main.py                          # run forever (Ctrl-C to stop)
    python main.py --demo                   # run built-in demonstration
    python main.py --stats                  # print system stats and exit
    python main.py --task "DESCRIPTION"     # execute a natural-language task
    python main.py --load --task "..."      # load memory snapshot first
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path

import config
from core import MemoryGraph, AgentFactory, MessageRouter
from agents import MemoryAgent, TaskAgent, PlannerAgent
from utils.logger import get_logger

log = get_logger("axis.main")


class AXIS:
    """Top-level system container."""

    def __init__(self):
        self.graph = MemoryGraph()
        self.router = MessageRouter()
        self.factory = AgentFactory(self.router, self.graph)
        self._register_agent_types()
        self._tasks: list[asyncio.Task] = []

    def _register_agent_types(self):
        self.factory.register_type("memory", MemoryAgent)
        self.factory.register_type("task", TaskAgent)
        self.factory.register_type("planner", PlannerAgent)
        log.info("Agent types registered", types=["memory", "task", "planner"])

    # --------------------------------------------------------- lifecycle

    def load_snapshot(self):
        p = config.GRAPH_SNAPSHOT_PATH
        if p.exists():
            self.graph = MemoryGraph.load(str(p))
            log.info("Memory snapshot loaded", path=str(p), nodes=len(self.graph))
        else:
            log.info("No snapshot found — starting with empty graph")

    def save_snapshot(self):
        self.graph.save(str(config.GRAPH_SNAPSHOT_PATH))
        log.info("Memory snapshot saved", path=str(config.GRAPH_SNAPSHOT_PATH))

    def spawn_default_agents(self):
        """Bring up the standard agent pool."""
        mem = self.factory.spawn(
            "memory", "Mem-0",
            capabilities=["memory.write", "memory.read", "memory.link", "memory.query"],
            topics=["memory_updates"],
            autostart=True,
        )
        task = self.factory.spawn(
            "task", "Task-0",
            capabilities=["task.run", "task.status"],
            autostart=True,
        )
        planner = self.factory.spawn(
            "planner", "Planner-0",
            capabilities=["planning", "orchestration"],
            autostart=True,
        )
        log.event("agents_spawned", "Default agent pool online",
                  agents=[mem.name, task.name, planner.name])
        return mem, task, planner

    async def run_forever(self):
        """Keep event loop alive; periodically log heartbeat stats."""
        try:
            while True:
                await asyncio.sleep(30)
                self._heartbeat()
        except asyncio.CancelledError:
            pass
        finally:
            self.factory.terminate_all()
            self.save_snapshot()

    def _heartbeat(self):
        log.event("heartbeat", "System heartbeat",
                  graph=self.graph.stats(),
                  agents=self.factory.stats(),
                  router=self.router.stats())

    def stats(self) -> dict:
        return {
            "graph": self.graph.stats(),
            "agents": self.factory.stats(),
            "router": self.router.stats(),
        }

    def shutdown(self):
        self.factory.terminate_all()
        self.save_snapshot()
        log.info("AXIS shutdown complete")


# ---------------------------------------------------------------------------

async def _run_demo(axis: AXIS):
    """Built-in demonstration of all three subsystems."""
    from demo import run_demo
    await run_demo(axis)


async def _run_task(axis: AXIS, description: str):
    """Execute a natural-language task description."""
    from core.task_runner import TaskRunner
    runner = TaskRunner(graph=axis.graph, logger=log)
    result = await runner.run(description)
    if result.success:
        print(f"\n{result}")
        if result.artifacts.get("stories"):
            print(f"\nStories saved to: {result.artifacts['path']}\n")
            for i, s in enumerate(result.artifacts["stories"], 1):
                print(f"  {i}. {s['title']}")
                print(f"     {s.get('source', '')}  |  {s.get('pub_date', '')}")
    else:
        print(f"\n{result}", file=__import__("sys").stderr)
        __import__("sys").exit(1)


async def _async_main(args):
    log.info("AXIS booting…")
    axis = AXIS()

    if args.load:
        axis.load_snapshot()

    mem_agent, task_agent, planner_agent = axis.spawn_default_agents()
    await asyncio.sleep(0.1)   # let agents settle

    if args.demo:
        await _run_demo(axis)
        axis.shutdown()
        return

    if args.stats:
        print(json.dumps(axis.stats(), indent=2))
        axis.shutdown()
        return

    if args.task:
        await _run_task(axis, args.task)
        axis.shutdown()
        return

    log.info("AXIS running — Ctrl-C to stop")
    try:
        await axis.run_forever()
    except KeyboardInterrupt:
        axis.shutdown()


def main():
    parser = argparse.ArgumentParser(description="AXIS Memory Graph System")
    parser.add_argument("--demo",  action="store_true", help="Run built-in demo")
    parser.add_argument("--stats", action="store_true", help="Print stats and exit")
    parser.add_argument("--load",  action="store_true", help="Load persisted memory snapshot")
    parser.add_argument("--task",  type=str, default=None, metavar="DESCRIPTION",
                        help="Execute a natural-language task and exit")
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
