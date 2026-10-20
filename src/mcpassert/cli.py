"""Command line interface for mcp-test."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from typing import Any, Dict, List, Optional

from . import __version__, snapshot
from .descriptor import ServerDescriptor
from .documents import call_document, list_document
from .errors import (
    EXIT_ASSERTION,
    EXIT_SETUP,
    EXIT_SUCCESS,
    CheckFailed,
    McpTestError,
    UsageError,
)
from .expectations import Expectation, evaluate_all, parse_expectations
from .mcpclient import McpClient
from .snapshot import DEFAULT_DIRECTORY
from .transports import build_transport
from .validation import validate_arguments, validate_output

PROGRAM = "mcp-test"

DESCRIPTION = """\
Assertion harness for Model Context Protocol servers.

Runs a server, calls it and checks the answer with small expressions:

  mcp-test list server.json
  mcp-test call server.json echo --args '{"a": 1}' --expect 'result.a == 1'
"""

EPILOG = """\
exit codes:
  0  every check passed
  1  an assertion or a snapshot did not match
  2  the command could not run (bad descriptor, no connection, protocol error)
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="how long to wait for a server answer (default: 30)",
    )
    common.add_argument(
        "--verbose",
        action="store_true",
        help="trace the JSON-RPC traffic on stderr",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        help="only report failures",
    )
    common.add_argument(
        "--snapshot-dir",
        default=DEFAULT_DIRECTORY,
        metavar="DIR",
        help="directory holding the snapshot files (default: {0})".format(
            DEFAULT_DIRECTORY
        ),
    )
    common.add_argument(
        "--ignore-paths",
        default="",
        metavar="PATHS",
        help="comma separated paths to drop before comparing snapshots",
    )
    common.add_argument(
        "--update",
        action="store_true",
        help="rewrite the snapshot file instead of comparing against it",
    )

    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="{0} {1}".format(PROGRAM, __version__)
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    list_parser = subparsers.add_parser(
        "list",
        parents=[common],
        help="list the tools of a server",
        description="Connect to a server and list the tools it exposes.",
    )
    list_parser.add_argument("server", help="path to the server descriptor")
    list_parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="EXPR",
        help="assertion to run against the answer (repeatable)",
    )
    list_parser.add_argument(
        "--snapshot", metavar="NAME", help="compare the answer with a snapshot"
    )
    list_parser.add_argument(
        "--include-resources",
        action="store_true",
        help="also list the resources of the server",
    )
    list_parser.add_argument(
        "--include-prompts",
        action="store_true",
        help="also list the prompts of the server",
    )
    list_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="print the raw response document instead of a readable list",
    )

    call_parser = subparsers.add_parser(
        "call",
        parents=[common],
        help="call a tool and check the answer",
        description="Connect to a server, call a tool and check the answer.",
    )
    call_parser.add_argument("server", help="path to the server descriptor")
    call_parser.add_argument("tool", help="name of the tool to call")
    call_parser.add_argument(
        "--args",
        dest="arguments",
        default="{}",
        metavar="JSON",
        help="arguments of the tool call as a JSON object (default: {})",
    )
    call_parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="EXPR",
        help="assertion to run against the answer (repeatable)",
    )
    call_parser.add_argument(
        "--snapshot", metavar="NAME", help="compare the answer with a snapshot"
    )
    call_parser.add_argument(
        "--validate-schema",
        action="store_true",
        help="check the arguments against the inputSchema of the tool",
    )
    call_parser.add_argument(
        "--validate-output",
        action="store_true",
        help="check the output against the outputSchema of the tool",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except McpTestError as exc:
        print("{0}: error: {1}".format(PROGRAM, exc), file=sys.stderr)
        if exc.details:
            print(exc.details, file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("{0}: interrupted".format(PROGRAM), file=sys.stderr)
        return 130
    except BrokenPipeError:  # pragma: no cover - depends on the consumer
        return EXIT_SETUP


def run(args: argparse.Namespace) -> int:
    if not args.command:
        raise UsageError("no command given, try 'mcp-test --help'")
    if args.update and not args.snapshot:
        raise UsageError("--update only makes sense together with --snapshot")

    descriptor = ServerDescriptor.from_file(args.server)
    if args.timeout is not None:
        descriptor.timeout = args.timeout

    transport = build_transport(descriptor, verbose=args.verbose)
    client = McpClient(transport, client_version=__version__)
    try:
        client.initialize()
        if args.command == "list":
            return _run_list(args, client)
        return _run_call(args, client)
    finally:
        client.close()


# -- commands ------------------------------------------------------------


def _run_list(args: argparse.Namespace, client: McpClient) -> int:
    tools = client.list_tools()
    result: Dict[str, Any] = {"tools": tools}
    if args.include_resources:
        result["resources"] = _optional_list(client, "resources")
    if args.include_prompts:
        result["prompts"] = _optional_list(client, "prompts")

    document = list_document(result)
    if args.as_json:
        _print_json(document)
    else:
        _print_tool_list(result)

    failures = _check(args, document)
    if failures:
        return EXIT_ASSERTION
    return EXIT_SUCCESS


def _optional_list(client: McpClient, kind: str) -> List[Dict[str, Any]]:
    """List a primitive, tolerating servers that do not implement it."""
    try:
        if kind == "resources":
            return client.list_resources()
        return client.list_prompts()
    except McpTestError:
        return []


def _run_call(args: argparse.Namespace, client: McpClient) -> int:
    arguments = _parse_arguments(args.arguments)
    schema: Dict[str, Any] = {}
    if args.validate_schema or args.validate_output:
        schema = _find_tool(client, args.tool)

    if args.validate_schema:
        validate_arguments(schema.get("inputSchema"), arguments)

    message = client.call_tool(args.tool, arguments)
    document = call_document(message)

    if args.validate_output:
        output = document.get("result", {}).get("structuredContent")
        if output is None:
            raise CheckFailed(
                "the tool {0!r} did not return any structured output to validate".format(
                    args.tool
                )
            )
        validate_output(schema.get("outputSchema"), output)

    _print_json(document)

    error = document.get("error")
    if error:
        print(
            "{0}: the server reported an error: {1}".format(
                PROGRAM, _error_text(error)
            ),
            file=sys.stderr,
        )

    failures = _check(args, document)
    if failures:
        return EXIT_ASSERTION
    if not args.expect and "error" in document:
        return EXIT_SETUP
    return EXIT_SUCCESS


# -- checks --------------------------------------------------------------


def _check(args: argparse.Namespace, document: Dict[str, Any]) -> int:
    """Run the assertions and the snapshot comparison.

    Returns the number of failures.
    """
    expectations: List[Expectation] = parse_expectations(args.expect)
    outcomes = evaluate_all(expectations, document)

    failures = 0
    for outcome in outcomes:
        if outcome.passed:
            if not args.quiet:
                print(
                    "PASS  {0}".format(outcome.expectation.describe()),
                    file=sys.stderr,
                )
        else:
            failures += 1
            print(outcome.report(), file=sys.stderr)
            print(_value_diff(outcome), file=sys.stderr)

    if args.snapshot:
        failures += _check_snapshot(args, document)

    if failures:
        print(
            "{0}: {1} check(s) failed".format(PROGRAM, failures),
            file=sys.stderr,
        )
    return failures


def _check_snapshot(args: argparse.Namespace, document: Dict[str, Any]) -> int:
    path = snapshot.snapshot_path(args.snapshot, args.snapshot_dir)
    ignored = snapshot.resolve_paths(args.ignore_paths)
    current = snapshot.normalize(document, ignored)
    stored = snapshot.read(path)

    if args.update or stored is None:
        snapshot.write(path, current)
        action = "updated" if stored is not None else "recorded"
        print(
            "{0}: {1} snapshot {2!r} in {3}".format(PROGRAM, action, args.snapshot, path),
            file=sys.stderr,
        )
        return 0

    rendered = snapshot.serialize(current)
    if rendered == stored:
        if not args.quiet:
            print(
                "{0}: snapshot {1!r} matches {2}".format(PROGRAM, args.snapshot, path),
                file=sys.stderr,
            )
        return 0

    print(
        "{0}: snapshot {1!r} does not match {2}".format(PROGRAM, args.snapshot, path),
        file=sys.stderr,
    )
    print(snapshot.diff(stored, rendered, path), file=sys.stderr)
    return 1


def _value_diff(outcome) -> str:
    """A tiny unified diff between the expected and the actual value."""
    expected = outcome.expected_display
    actual = outcome.actual_display
    lines = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile="expected",
        tofile="actual",
        lineterm="\n",
    )
    return "".join(line.rstrip("\n") + "\n" for line in lines)


# -- helpers -------------------------------------------------------------


def _error_text(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    code = error.get("code")
    message = str(error.get("message", ""))
    if code is None:
        return message
    return "[{0}] {1}".format(code, message)


def _parse_arguments(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise UsageError("--args is not valid JSON: {0}".format(exc))
    if not isinstance(parsed, dict):
        raise UsageError("--args must be a JSON object")
    return parsed


def _find_tool(client: McpClient, name: str) -> Dict[str, Any]:
    for tool in client.list_tools():
        if tool.get("name") == name:
            return tool
    raise UsageError("the server does not expose a tool called {0!r}".format(name))


def _print_json(document: Any) -> None:
    print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))


def _print_tool_list(result: Dict[str, Any]) -> None:
    tools = result.get("tools") or []
    if not tools:
        print("the server exposes no tools")
    else:
        width = max(len(str(tool.get("name", ""))) for tool in tools)
        print("tools ({0}):".format(len(tools)))
        for tool in tools:
            name = str(tool.get("name", ""))
            description = str(tool.get("description", "") or "")
            line = "  {0}".format(name.ljust(width))
            if description:
                line += "  {0}".format(description)
            print(line)
    for kind in ("resources", "prompts"):
        entries = result.get(kind)
        if entries:
            print("{0} ({1}):".format(kind, len(entries)))
            for entry in entries:
                name = str(entry.get("name") or entry.get("uri") or "")
                description = str(entry.get("description", "") or "")
                line = "  {0}".format(name)
                if description:
                    line += "  {0}".format(description)
                print(line)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
