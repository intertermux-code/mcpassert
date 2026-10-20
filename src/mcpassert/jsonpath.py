"""Resolving dotted paths inside a response document.

Supported syntax:

``result.name``          dictionary lookup
``result.items.0``       list index, also written ``result.items[0]``
``result.tools.*.name``  projects the ``name`` field over every entry of a
                         list, producing a list of names

A path that cannot be resolved yields :data:`MISSING` rather than an
exception, so that ``exists`` can tell "not there" from "there and null".
"""

from __future__ import annotations

import re
from typing import Any, List

MISSING = object()

_SEGMENT_RE = re.compile(r"[^.\[\]]+|\[([0-9]+)\]")


def parse_path(path: str) -> List[str]:
    """Split a path into its segments."""
    if not path or not path.strip():
        raise ValueError("path must not be empty")
    segments: List[str] = []
    for match in _SEGMENT_RE.finditer(path):
        index = match.group(1)
        if index is not None:
            segments.append(index)
        else:
            segments.append(match.group(0))
    if not segments:
        raise ValueError("path {0!r} does not contain any segments".format(path))
    return segments


def resolve(document: Any, path: str) -> Any:
    """Resolve ``path`` inside ``document``; returns :data:`MISSING` if absent."""
    try:
        segments = parse_path(path)
    except ValueError:
        return MISSING

    current: Any = document
    index = 0
    while index < len(segments):
        segment = segments[index]
        if segment == "*":
            if isinstance(current, dict):
                current = list(current.values())
            if not isinstance(current, list):
                return MISSING
            if index + 1 < len(segments):
                following = segments[index + 1]
                values = []
                for element in current:
                    found = child(element, following)
                    if found is not MISSING:
                        values.append(found)
                current = values
                index += 2
                continue
            current = list(current)
            index += 1
            continue
        current = child(current, segment)
        if current is MISSING:
            return MISSING
        index += 1
    return current


def child(value: Any, segment: str) -> Any:
    """Look up one path segment inside ``value``.

    Tool results keep their payload in ``structuredContent``; when a key is
    not found on the result itself it is looked up there as well, so that
    ``result.total`` and ``result.structuredContent.total`` both work.
    """
    if isinstance(value, dict):
        if segment in value:
            return value[segment]
        structured = value.get("structuredContent")
        if isinstance(structured, dict) and segment in structured:
            return structured[segment]
        return MISSING
    if isinstance(value, list):
        try:
            position = int(segment)
        except ValueError:
            return MISSING
        if -len(value) <= position < len(value):
            return value[position]
        return MISSING
    return MISSING


def path_exists(document: Any, path: str) -> bool:
    """Whether ``path`` points at something that is actually there."""
    return resolve(document, path) is not MISSING
