"""Building the documents that assertions are evaluated against.

Every command produces one JSON document. ``--expect`` expressions are
evaluated against that document, and the snapshot files store it verbatim, so
what you assert on and what you record are always the same thing.

For ``call`` the document looks like this::

    {"result": {"content": [...], "isError": false, "structuredContent": {...}}}

The document mirrors what the server sent.  The fields of
``structuredContent`` can also be reached directly under ``result``, so
``result.total`` finds the same value as ``result.structuredContent.total``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def call_document(message: Dict[str, Any]) -> Dict[str, Any]:
    """Build the document for a ``tools/call`` response."""
    if "error" in message:
        return {"error": _error_view(message.get("error"))}
    result = message.get("result")
    if not isinstance(result, dict):
        result = {}
    document: Dict[str, Any] = {"result": result}
    if result.get("isError") is True:
        document["error"] = {
            "message": _error_text(result),
            "isError": True,
        }
    return document


def list_document(result: Dict[str, Any]) -> Dict[str, Any]:
    """Build the document for a ``tools/list`` response."""
    return {"result": dict(result)}


def _error_view(error: Any) -> Dict[str, Any]:
    if isinstance(error, dict):
        return dict(error)
    return {"message": str(error)}


def _error_text(result: Dict[str, Any]) -> str:
    pieces: List[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            pieces.append(str(item))
            continue
        if item.get("type") == "text":
            pieces.append(str(item.get("text", "")))
        else:
            pieces.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    text = " ".join(piece for piece in pieces if piece)
    return text or "the tool reported an error"
