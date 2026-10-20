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

