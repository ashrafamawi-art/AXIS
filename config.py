"""AXIS system-wide configuration."""

from pathlib import Path

AXIS_DIR = Path.home() / "AXIS"

# Persistence
GRAPH_SNAPSHOT_PATH = AXIS_DIR / "memory_graph.json"
LOG_PATH = AXIS_DIR / "status.log"

# Memory Graph
DECAY_FACTOR = 0.995
PRUNE_THRESHOLD = 0.05
DECAY_INTERVAL_SECONDS = 60

# Message Router
QUEUE_MAXSIZE = 512
MESSAGE_LOG_MAX = 2000
DEFAULT_TTL_SECONDS = 60.0

# Agent defaults
DEFAULT_MAX_MEMORY_NODES = 200
DEFAULT_HEARTBEAT_INTERVAL = 30.0

# Planner
DEFAULT_STEP_TIMEOUT = 30.0
