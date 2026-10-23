"""Helpers shared by the test suite."""

import json
import pathlib
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
MOCK_STDIO_SERVER = TESTS_DIR / "mock_stdio_server.py"
MOCK_HTTP_SERVER = TESTS_DIR / "mock_http_server.py"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def stdio_descriptor(**overrides):
    """A descriptor that starts the mock stdio server."""
    descriptor = {
        "transport": "stdio",
        "command": [sys.executable, str(MOCK_STDIO_SERVER)],
    }
    descriptor.update(overrides)
    return descriptor


def http_descriptor(port, **overrides):
    """A descriptor that points at the mock HTTP server."""
    descriptor = {
        "transport": "http",
        "url": "http://127.0.0.1:{0}/mcp".format(port),
    }
    descriptor.update(overrides)
    return descriptor


def write_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def response(result, message_id=1, error=None):
    """A JSON-RPC answer as it is stored in a fixture."""
    message = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def fixture_payload(interactions):
    """A fixture holding ``interactions`` plus the handshake every session starts with."""
    initialize = {
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-test", "version": "0.1.0"},
        },
        "response": response(
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fixture-server", "version": "1.0.0"},
            }
        ),
    }
    recorded = [initialize]
    for index, item in enumerate(interactions, start=2):
        recorded.append(
            {
                "method": item["method"],
                "params": item.get("params") or {},
                "response": item["response"],
            }
        )
    return {
        "format": "mcpassert-fixture",
        "version": 1,
        "protocolVersion": "2025-06-18",
        "serverInfo": {"name": "fixture-server", "version": "1.0.0"},
        "interactions": recorded,
    }


def summary_lines(stderr):
    """The per-server results of a combined report, as (label, status) pairs."""
    lines = stderr.splitlines()
    try:
        start = lines.index("mcp-test: summary") + 1
    except ValueError:
        return []
    results = []
    for line in lines[start:]:
        if not line.startswith("  "):
            break
        parts = line.split()
        results.append((" ".join(parts[:-1]), parts[-1]))
    return results


def run_cli(argv):
    """Run the command line in process and capture what it writes.

    Returns the exit code together with stdout and stderr.
    """
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from mcpassert import cli

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exit_request:
            code = exit_request.code
    return code, out.getvalue(), err.getvalue()

