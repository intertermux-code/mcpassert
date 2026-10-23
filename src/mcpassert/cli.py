"""Command line interface for mcp-test."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
from .fixtures import Fixture, FixtureTransport, RecordingTransport
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

Records an answer once and replays it later without the server running:

  mcp-test call server.json echo --args '{"a": 1}' --record echo.json
  mcp-test call --fixture echo.json echo --args '{"a": 1}' --expect 'result.a == 1'

Checks several servers in one run:

  mcp-test list server.json other.json
  mcp-test list --servers servers.json
"""

EPILOG = """\
exit codes:
  0  every check passed
  1  an assertion or a snapshot did not match
  2  the command could not run (bad descriptor, no connection, protocol error,
     fixture that holds nothing for the call)
"""


@dataclass
class Target:
    """One server, or the recording of one, that the checks run against."""

    label: str
    descriptor: Optional[str] = None
    fixture: Optional[str] = None


def _add_server_arguments(parser: argparse.ArgumentParser) -> None:
    """Accept either a list of descriptors or the path of a recorded session."""
    parser.add_argument(
        "server_paths",
        nargs="*",
        metavar="SERVER",
        help="path to a server descriptor, repeatable to check several servers",
    )
    parser.add_argument(
        "--servers",
        metavar="FILE",
        help="JSON file listing the servers to check",
    )


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
    common.add_argument(
        "--record",
        metavar="PATH",
        help="write the exchanges of this run to a fixture file",
    )
    common.add_argument(
        "--append",
        action="store_true",
        help="with --record: add to the fixture file instead of replacing it",
    )
    common.add_argument(
        "--fixture",
        metavar="PATH",
        help="replay a recorded session from a fixture file instead of "
        "connecting to a server",
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
        help="list the tools of one or more servers",
        description="Connect to one or more servers and list the tools they expose.",
    )
    _add_server_arguments(list_parser)
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
        description="Connect to one or more servers, call a tool and check the answer.",
    )
    _add_server_arguments(call_parser)
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
    if args.record and args.fixture:
        raise UsageError(
            "--record writes a fixture and --fixture replays one, give only one of them"
        )
    if args.append and not args.record:
        raise UsageError("--append only makes sense together with --record")

    targets = _targets(args)
    if args.record and len(targets) > 1:
        raise UsageError("--record captures the session of one server, give one server")
    if len(targets) == 1:
        return _execute(args, targets[0])
    return _execute_each(args, targets)


def _execute(args: argparse.Namespace, target: Target) -> int:
    """Run one server (or one recording) through the checks of the command."""
    recording: Optional[Fixture] = None
    already = 0
    if target.fixture:
        transport = FixtureTransport(
            Fixture.from_file(target.fixture), verbose=args.verbose
        )
    else:
        descriptor = ServerDescriptor.from_file(target.descriptor)
        if args.timeout is not None:
            descriptor.timeout = args.timeout
        transport = build_transport(descriptor, verbose=args.verbose)
        if args.record:
            recording = _open_recording(args)
            already = len(recording.interactions)
            recording.transport = descriptor.transport
            recording.server = target.descriptor
            transport = RecordingTransport(transport, recording, verbose=args.verbose)

    client = McpClient(transport, client_version=__version__)
    try:
        client.initialize()
        if args.command == "list":
            return _run_list(args, client)
        return _run_call(args, client)
    finally:
        client.close()
        if recording is not None:
            _write_recording(args, recording, len(recording.interactions) - already)


def _write_recording(args: argparse.Namespace, recording: Fixture, new: int) -> None:
    if not new:
        # A server that never answered has nothing worth keeping, and an empty
        # file would only hide the failure from the next run.
        return
    recording.write(args.record)
    if not args.quiet:
        print(
            "{0}: wrote fixture {1} ({2} interaction(s))".format(
                PROGRAM, args.record, len(recording.interactions)
            ),
            file=sys.stderr,
        )


def _open_recording(args: argparse.Namespace) -> Fixture:
    if args.append and os.path.exists(args.record):
        return Fixture.from_file(args.record)
    return Fixture()


# -- servers of one run ---------------------------------------------------


def _targets(args: argparse.Namespace) -> List[Target]:
    """Every server this invocation was pointed at."""
    if args.fixture:
        if args.server_paths:
            raise UsageError(
                "--fixture replays a recorded session instead of connecting to a "
                "server, so there is no descriptor to give"
            )
        return [Target(label=args.fixture, fixture=args.fixture)]

    servers = [Target(label=path, descriptor=path) for path in args.server_paths]
    if args.servers:
        servers.extend(_read_server_list(args.servers))
    if not servers:
        raise UsageError(
            "no server given: pass a descriptor, --servers FILE or --fixture FILE"
        )
    return servers


def _read_server_list(path: str) -> List[Target]:
    """Read a servers file: a list of descriptors, optionally with a name each."""
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError("servers file not found: {0}".format(path))
    except OSError as exc:
        raise UsageError("could not read servers file {0}: {1}".format(path, exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise UsageError("servers file {0} is not valid JSON: {1}".format(path, exc))

    entries = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise UsageError(
            "servers file {0} must hold a non-empty list of servers".format(path)
        )
    return [_read_server_entry(entry, file_path.parent) for entry in entries]


def _read_server_entry(entry: Any, base: Path) -> Target:
    if isinstance(entry, str):
        return Target(label=entry, descriptor=str(base / entry))
    if not isinstance(entry, dict):
        raise UsageError(
            "a server of the list must be a path or an object, got {0}".format(
                type(entry).__name__
            )
        )
    kinds = [kind for kind in ("descriptor", "fixture") if entry.get(kind)]
    if not kinds:
        raise UsageError(
            "a server of the list needs a 'descriptor' or a 'fixture' field: {0}".format(
                json.dumps(entry, sort_keys=True, ensure_ascii=False)
            )
        )
    if len(kinds) > 1:
        raise UsageError(
            "a server of the list is either a descriptor or a fixture, not both"
        )
    kind = kinds[0]
    given = entry[kind]
    if not isinstance(given, str):
        raise UsageError("field {0!r} of a server must be a path".format(kind))
    name = entry.get("name")
    label = name if isinstance(name, str) and name else given
    return Target(label=label, **{kind: str(base / given)})


def _execute_each(args: argparse.Namespace, targets: List[Target]) -> int:
    """Check every server in turn and report them together."""
    results = []
    for target in targets:
        if not args.quiet:
            print("{0}: {1}".format(PROGRAM, target.label), file=sys.stderr)
        try:
            code = _execute(args, target)
        except McpTestError as exc:
            print("{0}: error: {1}".format(PROGRAM, exc), file=sys.stderr)
            if exc.details:
                print(exc.details, file=sys.stderr)
            code = exc.exit_code
        results.append((target.label, code))
    return _summarize(results)


def _summarize(results: List[Tuple[str, int]]) -> int:
    width = max(len(label) for label, _ in results)
    print("{0}: summary".format(PROGRAM), file=sys.stderr)
    failed = 0
    for label, code in results:
        if code != EXIT_SUCCESS:
            failed += 1
        print(
            "  {0}  {1}".format(label.ljust(width), _status(code)),
            file=sys.stderr,
        )
    if failed:
        print(
            "{0}: {1} of {2} server(s) failed".format(PROGRAM, failed, len(results)),
            file=sys.stderr,
        )
    else:
        print(
            "{0}: all {1} server(s) passed".format(PROGRAM, len(results)),
            file=sys.stderr,
        )
    return max(code for _, code in results)


def _status(code: int) -> str:
    if code == EXIT_SUCCESS:
        return "PASS"
    if code == EXIT_ASSERTION:
        return "FAIL"
    return "ERROR"


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
