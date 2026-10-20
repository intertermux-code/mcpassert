"""Snapshot recording, verification and diffing.

A snapshot is a golden file holding the normalised response document of one
command. The files live in ``snapshots/<name>.json`` next to the project that
owns them and are meant to be committed, so a change in the server output
shows up as a reviewable diff.

Normalisation drops the paths listed in ``--ignore-paths`` so that volatile
values (timestamps, request ids, ...) do not make every run fail.
"""

from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
from typing import Any, List, Optional

from .errors import UsageError
from .jsonpath import MISSING, child, parse_path

DEFAULT_DIRECTORY = "snapshots"


def normalize(document: Any, ignore_paths: Optional[List[str]] = None) -> Any:
    """Return a copy of ``document`` without the ignored paths."""
    normalized = copy.deepcopy(document)
    for path in ignore_paths or []:
        _remove(normalized, path)
    return normalized


def _remove(document: Any, path: str) -> None:
    try:
        segments = parse_path(path)
    except ValueError:
        return
    parent = document
    for segment in segments[:-1]:
        parent = child(parent, segment)
        if parent is MISSING:
            return
    last = segments[-1]
    if isinstance(parent, dict):
        if last in parent:
            del parent[last]
            return
        structured = parent.get("structuredContent")
        if isinstance(structured, dict) and last in structured:
            del structured[last]
    elif isinstance(parent, list):
        try:
            index = int(last)
        except ValueError:
            return
        if -len(parent) <= index < len(parent):
            del parent[index]


def serialize(document: Any) -> str:
    """Render a document the way snapshots are stored."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def snapshot_path(name: str, directory: str = DEFAULT_DIRECTORY) -> Path:
    """Return the file a snapshot with this name lives in."""
    if not name or not name.strip():
        raise UsageError("a snapshot name must not be empty")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise UsageError(
            "snapshot name {0!r} must not contain path separators".format(name)
        )
    if not name.endswith(".json"):
        name = name + ".json"
    return Path(directory) / name


def read(path: Path) -> Optional[str]:
    """Read a golden file, or return None when it does not exist yet."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UsageError("could not read snapshot {0}: {1}".format(path, exc))


def write(path: Path, document: Any) -> str:
    """Write a golden file, creating the directory when needed."""
    payload = serialize(document)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        raise UsageError("could not write snapshot {0}: {1}".format(path, exc))
    return payload


def diff(expected: str, actual: str, path: Path) -> str:
    """Unified diff between the stored snapshot and the current output."""
    lines = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=str(path),
        tofile="current output",
    )
    return "".join(lines)


def resolve_paths(value: str) -> List[str]:
    """Split a comma separated ``--ignore-paths`` value."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
