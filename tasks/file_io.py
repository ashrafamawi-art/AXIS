"""AXIS file I/O helpers for task outputs."""

from pathlib import Path


def save_markdown(content: str, path: str) -> str:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def read_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")
