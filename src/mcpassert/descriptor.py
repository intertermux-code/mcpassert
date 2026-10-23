"""Parsing and validation of the server descriptor.

A descriptor is a small JSON document that tells the tool how to reach an MCP
server. Two shapes are supported::

    {"transport": "stdio", "command": ["python", "my_server.py"], "env": {...}}
    {"transport": "http", "url": "http://localhost:8000/mcp"}

Unknown keys are ignored so that descriptors written for other MCP clients
keep working; missing or malformed required keys are reported with the field
name and the expected shape.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import DescriptorError

STDIO = "stdio"
HTTP = "http"
SUPPORTED_TRANSPORTS = (STDIO, HTTP)

DEFAULT_TIMEOUT = 30.0


@dataclass
class ServerDescriptor:
    """A validated description of how to reach one MCP server."""

    transport: str
    command: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    inherit_env: bool = True
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Any, source: Optional[str] = None) -> "ServerDescriptor":
        """Build a descriptor from already-decoded JSON data."""
        if not isinstance(data, dict):
            raise DescriptorError(
                "server descriptor must be a JSON object, got {0}".format(
                    _type_name(data)
                )
            )
        if "servers" in data and "transport" not in data:
            raise DescriptorError(
                "{0} lists several servers, it is not a descriptor; pass it with "
                "--servers".format(source or "file")
            )

        transport = _read_transport(data)
        timeout = _read_timeout(data)
        inherit_env = _read_bool(data, "inheritEnv", True)

        if transport == STDIO:
            command = _read_command(data)
            args = _read_string_list(data, "args")
            env = _read_string_map(data, "env")
            cwd = _read_optional_string(data, "cwd")
            return cls(
                transport=transport,
                command=command,
                args=args,
                env=_expand_values(env),
                cwd=cwd,
                timeout=timeout,
                inherit_env=inherit_env,
                source=source,
            )

        url = _read_url(data)
        headers = _read_string_map(data, "headers")
        return cls(
            transport=transport,
            url=url,
            headers=_expand_values(headers),
            timeout=timeout,
            inherit_env=inherit_env,
            source=source,
        )

    @classmethod
    def from_file(cls, path: str) -> "ServerDescriptor":
        """Read and validate a descriptor from a JSON file."""
        file_path = Path(path)
        try:
            raw = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DescriptorError("server descriptor not found: {0}".format(path))
        except OSError as exc:
            raise DescriptorError(
                "could not read server descriptor {0}: {1}".format(path, exc)
            )
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise DescriptorError(
                "server descriptor {0} is not valid JSON: {1}".format(path, exc)
            )
        descriptor = cls.from_dict(data, source=str(file_path))
        descriptor.resolve_against(file_path.parent)
        return descriptor

    def resolve_against(self, base: "Path") -> None:
        """Resolve relative paths against the directory of the descriptor.

        A descriptor usually sits next to the server it starts, so shipping
        the two together keeps working no matter where the command is run
        from.  Program names without a path separator are left alone and
        looked up in PATH.
        """
        self.command = [_resolve_path(element, base) for element in self.command]
        self.args = [_resolve_path(element, base) for element in self.args]
        if self.cwd:
            self.cwd = _resolve_path(self.cwd, base, always=True)


def _type_name(value: Any) -> str:
    return {
        dict: "object",
        list: "array",
        str: "string",
        bool: "boolean",
        int: "number",
        float: "number",
        type(None): "null",
    }.get(type(value), type(value).__name__)


def _resolve_path(value: str, base: Path, always: bool = False) -> str:
    """Make a relative path in a descriptor point at the descriptor.

    A path with a separator is always resolved.  A bare name is only
    resolved when something with that name sits next to the descriptor,
    otherwise it stays a program name and is looked up in PATH.
    """
    if not value or os.path.isabs(value):
        return value
    if os.sep in value or "/" in value:
        return os.path.normpath(os.path.join(str(base), value))
    candidate = os.path.join(str(base), value)
    if always or os.path.exists(candidate):
        return candidate
    return value


def _read_transport(data: Dict[str, Any]) -> str:
    raw = data.get("transport", data.get("type"))
    if raw is None:
        raise DescriptorError(
            "missing required field 'transport' (expected one of: {0})".format(
                ", ".join(SUPPORTED_TRANSPORTS)
            )
        )
    if not isinstance(raw, str):
        raise DescriptorError(
            "field 'transport' must be a string, got {0}".format(_type_name(raw))
        )
    if raw not in SUPPORTED_TRANSPORTS:
        raise DescriptorError(
            "unsupported transport {0!r} (supported: {1})".format(
                raw, ", ".join(SUPPORTED_TRANSPORTS)
            )
        )
    return raw


def _read_command(data: Dict[str, Any]) -> List[str]:
    raw = data.get("command")
    if raw is None:
        raise DescriptorError(
            "missing required field 'command' for the 'stdio' transport "
            '(expected a list of strings, e.g. ["python", "my_server.py"])'
        )
    if isinstance(raw, str):
        parts = shlex.split(raw)
    elif isinstance(raw, list):
        parts = raw
    else:
        raise DescriptorError(
            "field 'command' must be a string or a list of strings, got {0}".format(
                _type_name(raw)
            )
        )
    if not parts:
        raise DescriptorError("field 'command' must not be empty")
    for part in parts:
        if not isinstance(part, str):
            raise DescriptorError(
                "field 'command' must only contain strings, got {0}".format(
                    _type_name(part)
                )
            )
    return list(parts)


def _read_string_list(data: Dict[str, Any], key: str) -> List[str]:
    raw = data.get(key, [])
    if raw is None:
        return []
    if isinstance(raw, str):
        return shlex.split(raw)
    if not isinstance(raw, list):
        raise DescriptorError(
            "field {0!r} must be a list of strings, got {1}".format(
                key, _type_name(raw)
            )
        )
    for part in raw:
        if not isinstance(part, str):
            raise DescriptorError(
                "field {0!r} must only contain strings, got {1}".format(
                    key, _type_name(part)
                )
            )
    return list(raw)


def _read_string_map(data: Dict[str, Any], key: str) -> Dict[str, str]:
    raw = data.get(key, {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DescriptorError(
            "field {0!r} must be an object of string values, got {1}".format(
                key, _type_name(raw)
            )
        )
    result = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise DescriptorError(
                "field {0!r} must only contain string keys and string values".format(key)
            )
        result[name] = value
    return result


def _expand_values(values: Dict[str, str]) -> Dict[str, str]:
    """Replace ``${NAME}`` with the value of that environment variable.

    Secrets belong in the environment, not in a file that gets committed.
    A variable that is not set is left as it is.
    """
    return {name: os.path.expandvars(value) for name, value in values.items()}


def _read_optional_string(data: Dict[str, Any], key: str) -> Optional[str]:
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DescriptorError(
            "field {0!r} must be a string, got {1}".format(key, _type_name(raw))
        )
    return raw


def _read_url(data: Dict[str, Any]) -> str:
    raw = data.get("url")
    if raw is None:
        raise DescriptorError(
            "missing required field 'url' for the 'http' transport "
            '(expected e.g. "http://localhost:8000/mcp")'
        )
    if not isinstance(raw, str):
        raise DescriptorError(
            "field 'url' must be a string, got {0}".format(_type_name(raw))
        )
    if not raw.startswith(("http://", "https://")):
        raise DescriptorError(
            "field 'url' must start with http:// or https://, got {0!r}".format(raw)
        )
    return raw


def _read_timeout(data: Dict[str, Any]) -> float:
    raw = data.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise DescriptorError(
            "field 'timeout' must be a number of seconds, got {0}".format(
                _type_name(raw)
            )
        )
    if raw <= 0:
        raise DescriptorError("field 'timeout' must be greater than zero")
    return float(raw)


def _read_bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    raw = data.get(key, default)
    if not isinstance(raw, bool):
        raise DescriptorError(
            "field {0!r} must be true or false, got {1}".format(key, _type_name(raw))
        )
    return raw
