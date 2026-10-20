#!/usr/bin/env python3
"""A tiny MCP server used by the test suite.

It speaks JSON-RPC 2.0 over stdin/stdout the way MCP stdio servers do:
one JSON message per line.

    python tests/mock_stdio_server.py

Setting ``MCPTEST_CRASH=1`` makes the server exit before answering anything,
which is handy to test how the client reports a server that never shows up.
"""

import json
import os
import sys
import time

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "echo",
        "description": "Return the arguments unchanged",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        },
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail",
        "description": "Fail with a tool level error",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "boom",
        "description": "Answer with a JSON-RPC error",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sleep",
        "description": "Wait for a while before answering",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
    },
    {
        "name": "stamp",
        "description": "Return a value that changes on every call",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

RESOURCES = [
    {"uri": "mock://greeting", "name": "greeting", "mimeType": "text/plain"}
]

PROMPTS = [{"name": "greet", "description": "Greet somebody"}]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def error(message_id, code, text, data=None):
    payload = {"code": code, "message": text}
    if data is not None:
        payload["data"] = data
    send({"jsonrpc": "2.0", "id": message_id, "error": payload})


def result(message_id, value):
    send({"jsonrpc": "2.0", "id": message_id, "result": value})


def text_content(text):
    return [{"type": "text", "text": text}]


def handle_initialize(message_id, params):
    result(
        message_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mock-stdio-server", "version": "1.0.0"},
        },
    )


def handle_call(message_id, params):
    name = params.get("name")
    arguments = params.get("arguments") or {}

    if name == "echo":
        result(
            message_id,
            {
                "content": text_content(json.dumps(arguments, sort_keys=True)),
                "isError": False,
                "structuredContent": arguments,
            },
        )
        return

    if name == "add":
        if "a" not in arguments or "b" not in arguments:
            error(message_id, -32602, "add needs both 'a' and 'b'")
            return
        total = arguments["a"] + arguments["b"]
        result(
            message_id,
            {
                "content": text_content(str(total)),
                "isError": False,
                "structuredContent": {"sum": total},
            },
        )
        return

    if name == "fail":
        result(
            message_id,
            {
                "content": text_content("the tool refused to do it"),
                "isError": True,
            },
        )
        return

    if name == "boom":
        error(message_id, -32000, "the tool exploded")
        return

    if name == "sleep":
        time.sleep(float(arguments.get("seconds", 1)))
        result(
            message_id,
            {
                "content": text_content("slept"),
                "isError": False,
                "structuredContent": {"slept": True},
            },
        )
        return

    if name == "stamp":
        result(
            message_id,
            {
                "content": text_content("stamped"),
                "isError": False,
                "structuredContent": {"now": time.time(), "value": "constant"},
            },
        )
        return

    error(message_id, -32602, "unknown tool {0!r}".format(name))


def handle(message):
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        handle_initialize(message_id, message.get("params") or {})
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "tools/list":
        result(message_id, {"tools": TOOLS})
    elif method == "resources/list":
        result(message_id, {"resources": RESOURCES})
    elif method == "prompts/list":
        result(message_id, {"prompts": PROMPTS})
    elif method == "tools/call":
        handle_call(message_id, message.get("params") or {})
    elif message_id is not None:
        error(message_id, -32601, "unknown method {0!r}".format(method))


def main():
    if os.environ.get("MCPTEST_CRASH") == "1":
        sys.stderr.write("mock server is going down on purpose\n")
        return 3

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        handle(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
