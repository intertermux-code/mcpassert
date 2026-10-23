"""Recorded sessions that stand in for a live server.

A fixture is a JSON file holding the JSON-RPC answers one session gave.
``--record`` writes the file while the server is running, ``--fixture`` plays
it back later without starting anything:

    mcp-test call server.json echo --args '{"a": 1}' --record fixture.json
    mcp-test call --fixture fixture.json echo --args '{"a": 1}' --expect 'result.a == 1'

The recorded answers are handed back through the transport interface, so
assertions, golden files and exit codes behave exactly as they do against the
live server.
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import FixtureError
from .transports import Transport

FORMAT = "mcpassert-fixture"
FORMAT_VERSION = 1

INITIALIZE = "initialize"

#: The exchanges worth keeping. Everything else a session does - pings,
#: notifications, server initiated requests - carries no answer to replay.
RECORDED_METHODS = (
    INITIALIZE,
    "tools/list",
    "tools/call",
    "resources/list",
    "prompts/list",
)


def canonical(value: Any) -> str:
    """Render JSON the way it is compared and stored."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


@dataclass
class Interaction:
    """One request and the answer that was given to it."""

    method: str
    params: Any = None
    response: Dict[str, Any] = field(default_factory=dict)

    def answers(self, params: Any) -> bool:
        """Whether this exchange answers the same request."""
        return canonical(self.params) == canonical(params)

    def describe(self) -> str:
        """Name the exchange the way an error message shows it."""
        if self.method != "tools/call" or not isinstance(self.params, dict):
            return self.method
        arguments = self.params.get("arguments")
        if arguments in (None, {}):
            return "call {0!r}".format(self.params.get("name"))
        return "call {0!r} with {1}".format(
            self.params.get("name"), canonical(arguments)
        )


@dataclass
class Fixture:
    """The interactions of one recorded session."""

    interactions: List[Interaction] = field(default_factory=list)
    server_info: Dict[str, Any] = field(default_factory=dict)
    protocol_version: Optional[str] = None
    transport: Optional[str] = None
    server: Optional[str] = None
    source: Optional[str] = None

    # -- recording --------------------------------------------------------

    def add(self, method: str, params: Any, response: Dict[str, Any]) -> None:
        if method == INITIALIZE:
            # A fixture replays one handshake, however many sessions were
            # recorded into it with --append.
            if not self.matches(INITIALIZE, ignore_params=True):
                self.interactions.append(
                    Interaction(method=method, params=params, response=response)
                )
            self._remember_handshake(response)
            return
        self.interactions.append(
            Interaction(method=method, params=params, response=response)
        )

    def _remember_handshake(self, response: Dict[str, Any]) -> None:
        result = response.get("result") or {}
        self.server_info = dict(result.get("serverInfo") or {}) or self.server_info
        self.protocol_version = (
            result.get("protocolVersion") or self.protocol_version
        )

    # -- replaying --------------------------------------------------------

    def matches(
        self, method: str, params: Any = None, ignore_params: bool = False
    ) -> List[Interaction]:
        """Every recorded answer for this request, in the order recorded."""
        return [
            item
            for item in self.interactions
            if item.method == method and (ignore_params or item.answers(params))
        ]

    def inventory(self) -> str:
        """What the file holds, for the error message when it holds too little."""
        if not self.interactions:
            return "  (nothing)"
        return "\n".join("  " + item.describe() for item in self.interactions)

    # -- the file ---------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Any, source: Optional[str] = None) -> "Fixture":
        """Build a fixture from already-decoded JSON data."""
        if not isinstance(data, dict):
            raise FixtureError(
                "fixture {0} must be a JSON object, got {1}".format(
                    _name(source), type(data).__name__
                )
            )
        if data.get("format") != FORMAT:
            if "transport" in data:
                raise FixtureError(
                    "{0} is a server descriptor, not a fixture; drop --fixture to "
                    "run against the live server".format(_name(source))
                )
            raise FixtureError(
                "{0} is not a fixture file (field 'format' must be {1!r} at the "
                "top level)".format(_name(source), FORMAT)
            )
        raw = data.get("interactions")
        if raw is None:
            raise FixtureError(
                "fixture {0} is missing the required field 'interactions'".format(
                    _name(source)
                )
            )
        if not isinstance(raw, list):
            raise FixtureError(
                "field 'interactions' of fixture {0} must be a list, got {1}".format(
                    _name(source), type(raw).__name__
                )
            )
        interactions = [
            _read_interaction(item, index) for index, item in enumerate(raw)
        ]
        return cls(
            interactions=interactions,
            server_info=dict(data.get("serverInfo") or {}),
            protocol_version=data.get("protocolVersion"),
            transport=data.get("transport"),
            server=data.get("server"),
            source=source,
        )

    @classmethod
    def from_file(cls, path: str) -> "Fixture":
        """Read and validate a fixture from a JSON file."""
        file_path = Path(path)
        try:
            raw = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FixtureError("fixture not found: {0}".format(path))
        except OSError as exc:
            raise FixtureError("could not read fixture {0}: {1}".format(path, exc))
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise FixtureError("fixture {0} is not valid JSON: {1}".format(path, exc))
        return cls.from_dict(data, source=str(file_path))

    def to_dict(self) -> Dict[str, Any]:
        document: Dict[str, Any] = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "interactions": [
                {
                    "method": item.method,
                    "params": item.params,
                    "response": item.response,
                }
                for item in self.interactions
            ],
        }
        if self.server_info:
            document["serverInfo"] = self.server_info
        if self.protocol_version:
            document["protocolVersion"] = self.protocol_version
        if self.transport:
            document["transport"] = self.transport
        if self.server:
            document["server"] = self.server
        return document

    def write(self, path: str) -> str:
        """Write the fixture out, creating the directory when needed."""
        payload = json.dumps(
            self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"
        file_path = Path(path)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise FixtureError("could not write fixture {0}: {1}".format(path, exc))
        self.source = str(file_path)
        return payload

    def where(self) -> str:
        return self.source or "<fixture>"


def _read_interaction(data: Any, index: int) -> Interaction:
    where = "interaction {0}".format(index)
    if not isinstance(data, dict):
        raise FixtureError(
            "fixture {0} must be an object, got {1}".format(
                where, type(data).__name__
            )
        )
    method = data.get("method")
    if not isinstance(method, str) or not method:
        raise FixtureError(
            "fixture {0} needs a non-empty string field 'method'".format(where)
        )
    response = data.get("response")
    if not isinstance(response, dict):
        raise FixtureError(
            "field 'response' of fixture {0} must be a JSON-RPC message object".format(
                where
            )
        )
    return Interaction(
        method=method, params=data.get("params") or {}, response=response
    )


def _name(source: Optional[str]) -> str:
    return source or "file"


class RecordingTransport(Transport):
    """Wraps a live transport and keeps the exchanges worth replaying."""

    def __init__(
        self,
        inner: Transport,
        fixture: Fixture,
        methods: tuple = RECORDED_METHODS,
        verbose: bool = False,
    ):
        self._inner = inner
        self._fixture = fixture
        self._methods = tuple(methods)
        self._verbose = verbose

    @property
    def fixture(self) -> Fixture:
        return self._fixture

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None):
        message = self._inner.request(method, params, timeout)
        if method in self._methods:
            self._fixture.add(method, params, copy.deepcopy(message))
            if self._verbose:
                print(
                    "[recorded] {0}".format(
                        Interaction(method=method, params=params).describe()
                    ),
                    file=sys.stderr,
                )
        return message

    def notify(self, method: str, params: Any = None) -> None:
        self._inner.notify(method, params)

    def set_protocol_version(self, version: str) -> None:
        self._inner.set_protocol_version(version)

    def close(self) -> None:
        self._inner.close()


class FixtureTransport(Transport):
    """Answers from a fixture instead of from a server."""

    def __init__(self, fixture: Fixture, verbose: bool = False):
        self._fixture = fixture
        self._verbose = verbose
        self._used: Dict[str, int] = {}

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None):
        response = self._answer(method, params)
        if self._verbose:
            print("< {0}".format(canonical(response)), file=sys.stderr)
        return response

    def notify(self, method: str, params: Any = None) -> None:
        """The handshake of a recorded session is already over."""
        if self._verbose:
            print("> {0} {1}".format(method, canonical(params)), file=sys.stderr)

    def _answer(self, method: str, params: Any) -> Dict[str, Any]:
        # The handshake has no bearing on what is asserted, so it is matched
        # on its method alone: a fixture keeps working when the client version
        # or the requested protocol changes.
        ignore_params = method == INITIALIZE
        recorded = self._fixture.matches(method, params, ignore_params=ignore_params)
        if not recorded:
            raise FixtureError(
                "the fixture {0} holds no answer for {1}".format(
                    self._fixture.where(), _wanted(method, params)
                ),
                details="recorded in the fixture:\n{0}".format(
                    self._fixture.inventory()
                ),
            )
        key = method if ignore_params else "{0}\n{1}".format(method, canonical(params))
        index = min(self._used.get(key, 0), len(recorded) - 1)
        self._used[key] = index + 1
        return copy.deepcopy(recorded[index].response)


def _wanted(method: str, params: Any) -> str:
    if method != "tools/call" or not isinstance(params, dict):
        return method
    return Interaction(method=method, params=params).describe()
