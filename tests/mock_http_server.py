#!/usr/bin/env python3
"""A tiny MCP server that speaks streamable HTTP, used by the test suite.

    python tests/mock_http_server.py --port 8000

The server hands out an ``Mcp-Session-Id`` during ``initialize`` and expects
it back on every later request.  ``tools/list`` is answered with an event
stream, ``tools/call`` with plain JSON, so both flavours of the transport are
exercised by a single session.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2025-06-18"
SESSION_ID = "mock-session-1"

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
]

RESOURCES = [{"uri": "mock://greeting", "name": "greeting", "mimeType": "text/plain"}]
PROMPTS = [{"name": "greet", "description": "Greet somebody"}]


class MCPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mock-http-server/1.0"

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        pass

    # -- helpers ----------------------------------------------------------

    def _read_message(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            return None

    def _send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_event_stream(self, payload):
        body = ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status=202):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- verbs ------------------------------------------------------------

    def do_GET(self):
        # The legacy GET based SSE transport is deliberately not implemented.
        self._send_json(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32601,
                    "message": "this server only speaks the POST transport",
                },
            },
            status=405,
        )

    def do_POST(self):
        message = self._read_message()
        if message is None:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "could not parse the request"},
                },
                status=400,
            )
            return

        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "mock-http-server", "version": "1.0.0"},
                    },
                },
                headers={"Mcp-Session-Id": SESSION_ID},
            )
            return

        session = self.headers.get("Mcp-Session-Id")
        if session != SESSION_ID:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32001,
                        "message": "missing or unknown Mcp-Session-Id",
                    },
                },
                status=400,
            )
            return

        if method in ("notifications/initialized", "initialized"):
            self._send_empty()
            return

        if method == "tools/list":
            self._send_event_stream(
                {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
            )
            return

        if method == "resources/list":
            self._send_json(
                {"jsonrpc": "2.0", "id": message_id, "result": {"resources": RESOURCES}}
            )
            return

        if method == "prompts/list":
            self._send_json(
                {"jsonrpc": "2.0", "id": message_id, "result": {"prompts": PROMPTS}}
            )
            return

        if method == "tools/call":
            self._handle_call(message_id, message.get("params") or {})
            return

        self._send_json(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32601,
                    "message": "unknown method {0!r}".format(method),
                },
            },
            status=404,
        )

    def _handle_call(self, message_id, params):
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if name == "echo":
            payload = {
                "content": [{"type": "text", "text": json.dumps(arguments, sort_keys=True)}],
                "isError": False,
                "structuredContent": arguments,
            }
        elif name == "add":
            if "a" not in arguments or "b" not in arguments:
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {"code": -32602, "message": "add needs both 'a' and 'b'"},
                    }
                )
                return
            total = arguments["a"] + arguments["b"]
            payload = {
                "content": [{"type": "text", "text": str(total)}],
                "isError": False,
                "structuredContent": {"sum": total},
            }
        else:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32602, "message": "unknown tool {0!r}".format(name)},
                }
            )
            return

        self._send_json({"jsonrpc": "2.0", "id": message_id, "result": payload})


def start_server(port=0):
    """Start the mock server in a background thread and return it."""
    server = ThreadingHTTPServer(("127.0.0.1", port), MCPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser(description="Mock MCP server for the tests")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    options = parser.parse_args()

    server = ThreadingHTTPServer((options.host, options.port), MCPRequestHandler)
    print("mock MCP server listening on http://{0}:{1}/mcp".format(
        options.host, options.port
    ))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
