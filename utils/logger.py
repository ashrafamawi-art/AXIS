"""
AXIS structured logger — writes JSON-L lines to status.log and
optionally prints coloured output to the terminal.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

AXIS_DIR = Path.home() / "AXIS"
DEFAULT_LOG_PATH = AXIS_DIR / "status.log"

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

COLOURS = {
    "DEBUG":    "\033[36m",    # cyan
    "INFO":     "\033[32m",    # green
    "WARNING":  "\033[33m",    # yellow
    "ERROR":    "\033[31m",    # red
    "CRITICAL": "\033[35m",    # magenta
    "RESET":    "\033[0m",
}


class AxisLogger:
    def __init__(
        self,
        name: str,
        log_path: Path = DEFAULT_LOG_PATH,
        min_level: str = "INFO",
        colour: bool = True,
    ):
        self.name = name
        self.log_path = log_path
        self.min_level = min_level
        self.colour = colour and sys.stdout.isatty()
        self._file = open(log_path, "a", buffering=1, encoding="utf-8")

    def _write(self, level: str, message: str, extra: dict = None):
        if LEVEL_ORDER.get(level, 0) < LEVEL_ORDER.get(self.min_level, 0):
            return
        ts = datetime.now(timezone.utc).isoformat()
        record = {"ts": ts, "level": level, "logger": self.name, "msg": message}
        if extra:
            record.update(extra)
        self._file.write(json.dumps(record) + "\n")

        if self.colour:
            col = COLOURS.get(level, "")
            reset = COLOURS["RESET"]
            print(f"{col}[{level:8s}] {ts} [{self.name}] {message}{reset}")
        else:
            print(f"[{level:8s}] {ts} [{self.name}] {message}")

    def debug(self, msg: str, **extra):    self._write("DEBUG", msg, extra or None)
    def info(self, msg: str, **extra):     self._write("INFO", msg, extra or None)
    def warning(self, msg: str, **extra):  self._write("WARNING", msg, extra or None)
    def error(self, msg: str, **extra):    self._write("ERROR", msg, extra or None)
    def critical(self, msg: str, **extra): self._write("CRITICAL", msg, extra or None)

    def event(self, event_type: str, msg: str, **extra):
        self._write("INFO", msg, {"event": event_type, **extra})

    def close(self):
        self._file.close()

    def __del__(self):
        try:
            self._file.close()
        except Exception:
            pass


_loggers: dict[str, AxisLogger] = {}


def get_logger(name: str = "axis", **kwargs) -> AxisLogger:
    if name not in _loggers:
        _loggers[name] = AxisLogger(name=name, **kwargs)
    return _loggers[name]
