"""Transports that carry JSON-RPC 2.0 messages to an MCP server.

Two transports are implemented, both on top of the standard library only:

``stdio``
    The server runs as a child process and exchanges newline-delimited JSON
    messages on stdin/stdout, which is what MCP stdio servers speak.

``http``
    The streamable-HTTP flavour: every message is POSTed to a single URL.
    If the server hands out an ``Mcp-Session-Id`` during ``initialize`` the
    session id is echoed back on every later request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from queue import Empty, Queue
from typing import Any, Deque, Dict, Optional

from .descriptor import ServerDescriptor
from .errors import ProtocolError, TransportError

JSONRPC_VERSION = "2.0"

METHOD_NOT_FOUND = -32601


class Transport:
    """Common interface used by the MCP client."""

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None):
        """Send a request and return the raw JSON-RPC response message."""
        raise NotImplementedError

    def notify(self, method: str, params: Any = None) -> None:
        """Send a notification; the server does not answer these."""
        raise NotImplementedError

    def set_protocol_version(self, version: str) -> None:
        """Remember the negotiated protocol version, when the transport uses it."""

    def close(self) -> None:
        """Release the underlying resources."""


class StdioTransport(Transport):
    """JSON-RPC over a child process' stdin/stdout."""

    def __init__(
        self,
        command,
        args=None,
        env=None,
        cwd=None,
        timeout: float = 30.0,
        inherit_env: bool = True,
        verbose: bool = False,
    ):
        self._command = list(command) + list(args or [])
        self._timeout = timeout
        self._verbose = verbose
        self._next_id = 0
        self._inbox: "Queue[Any]" = Queue()
        self._stderr: Deque[str] = deque(maxlen=40)
        self._closed = False

        child_env = None
        if env:
            child_env = dict(os.environ) if inherit_env else {}
            child_env.update(env)
        elif not inherit_env:
            child_env = {}

        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise TransportError(
                "could not start server {0!r}: {1}".format(" ".join(self._command), exc)
            )

        self._reader = threading.Thread(
            target=self._read_stdout, name="mcp-stdout", daemon=True
        )
        self._reader.start()
        self._error_reader = threading.Thread(
            target=self._read_stderr, name="mcp-stderr", daemon=True
        )
        self._error_reader.start()

    # -- plumbing ---------------------------------------------------------

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        try:
            while True:
                line = stream.readline()
                if line == "":
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    # Servers that print banners on stdout break the protocol;
                    # keep the line around for diagnostics and carry on.
                    self._stderr.append("[unparsable stdout] {0}".format(text))
                    continue
                self._inbox.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._inbox.put(None)

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        try:
            for line in stream:
                text = line.rstrip("\n")
                if not text:
                    continue
                self._stderr.append(text)
                if self._verbose:
                    print("[server stderr] {0}".format(text), file=sys.stderr)
        except (OSError, ValueError):
            pass

    def _write(self, message: Dict[str, Any]) -> None:
        if self._process.stdin is None or self._process.poll() is not None:
            raise TransportError(self._exited_message())
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            self._process.stdin.write(payload + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TransportError(self._exited_message(details=str(exc)))
        if self._verbose:
            print("> {0}".format(payload), file=sys.stderr)

    def _exited_message(self, details: Optional[str] = None) -> str:
        parts = ["server process is not running"]
        code = self._process.poll()
        if code is not None:
            parts.append("it exited with status {0}".format(code))
        stderr = list(self._stderr)
        if stderr:
            parts.append("last output: {0}".format(" | ".join(stderr[-5:])))
        if details:
            parts.append(details)
        return "; ".join(parts)

    # -- transport API ----------------------------------------------------

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None):
        self._next_id += 1
        request_id = self._next_id
        message: Dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write(message)

        deadline = time.monotonic() + (timeout if timeout is not None else self._timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(
                    "timed out after {0:.1f}s waiting for a response to {1!r}".format(
                        timeout if timeout is not None else self._timeout, method
                    )
                )
            try:
                incoming = self._inbox.get(timeout=remaining)
            except Empty:
                continue
            if incoming is None:
                raise TransportError(self._exited_message())
            if not isinstance(incoming, dict):
                continue
            if incoming.get("id") == request_id and (
                "result" in incoming or "error" in incoming
            ):
                if self._verbose:
                    print(
                        "< {0}".format(json.dumps(incoming, ensure_ascii=False)),
                        file=sys.stderr,
                    )
                return incoming
            self._handle_foreign(incoming)

    def notify(self, method: str, params: Any = None) -> None:
        message: Dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write(message)

    def _handle_foreign(self, message: Dict[str, Any]) -> None:
        """Deal with notifications and server initiated requests.

        Servers may ping the client or ask it for roots. Nothing here needs a
        real answer, but staying silent would leave the server waiting, so an
        explicit "method not found" is sent back.
        """
        if "method" in message and "id" in message:
            self._write(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": message["id"],
                    "error": {
                        "code": METHOD_NOT_FOUND,
                        "message": "mcp-test does not implement {0!r}".format(
                            message.get("method")
                        ),
                    },
                }
            )
        elif self._verbose:
            print(
                "[server notification] {0}".format(message.get("method")),
                file=sys.stderr,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        for stream in (self._process.stdout, self._process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class HttpTransport(Transport):
    """Streamable-HTTP transport built on urllib."""

    def __init__(
        self,
        url: str,
        headers=None,
        timeout: float = 30.0,
        verbose: bool = False,
    ):
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._verbose = verbose
        self._session_id: Optional[str] = None
        self._protocol_version: Optional[str] = None
        self._next_id = 0

    def set_protocol_version(self, version: str) -> None:
        self._protocol_version = version

    # -- transport API ----------------------------------------------------

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None):
        self._next_id += 1
        request_id = self._next_id
        message: Dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        response = self._post(message, expect_response=True)
        if response is None:
            raise ProtocolError(
                "server accepted {0!r} without answering it".format(method)
            )
        return response

    def notify(self, method: str, params: Any = None) -> None:
        message: Dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._post(message, expect_response=False)

    # -- plumbing ---------------------------------------------------------

    def _post(self, message: Dict[str, Any], expect_response: bool) -> Optional[dict]:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version

        request = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        if self._verbose:
            print("> POST {0} {1}".format(self._url, body.decode("utf-8")),
                  file=sys.stderr)

        try:
            response = urllib.request.urlopen(
                request, timeout=self._timeout
            )
        except urllib.error.HTTPError as exc:
            return self._handle_http_error(exc, message)
        except urllib.error.URLError as exc:
            raise TransportError(
                "could not reach {0}: {1}".format(self._url, exc.reason)
            )
        except OSError as exc:
            raise TransportError("could not reach {0}: {1}".format(self._url, exc))

        with response:
            self._remember_session(response)
            if not expect_response:
                response.read()
                return None
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in content_type:
                return self._read_event_stream(response, message.get("id"))
            payload = response.read()
        if not payload:
            return None
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(
                "server answered {0!r} with a body that is not JSON: {1}".format(
                    message.get("method"), exc
                )
            )
        if self._verbose:
            print("< {0}".format(json.dumps(decoded, ensure_ascii=False)),
                  file=sys.stderr)
        return decoded

    def _remember_session(self, response) -> None:
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id and not self._session_id:
            self._session_id = session_id
            if self._verbose:
                print("[session] {0}".format(session_id), file=sys.stderr)

    def _handle_http_error(
        self, exc: urllib.error.HTTPError, message: Dict[str, Any]
    ) -> Optional[dict]:
        payload = b""
        try:
            payload = exc.read()
        except OSError:
            pass
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            decoded = None
        if isinstance(decoded, dict) and ("error" in decoded or "result" in decoded):
            if self._verbose:
                print("< HTTP {0} {1}".format(exc.code, payload.decode("utf-8", "replace")),
                      file=sys.stderr)
            return decoded
        raise TransportError(
            "server returned HTTP {0} for {1!r}{2}".format(
                exc.code,
                message.get("method"),
                ": " + payload.decode("utf-8", "replace").strip() if payload else "",
            )
        )

    def _read_event_stream(self, response, request_id) -> Optional[dict]:
        """Collect frames until the one answering our request shows up."""
        data_lines: list = []
        while True:
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if not text:
                if data_lines:
                    joined = "\n".join(data_lines)
                    data_lines = []
                    message = self._decode_event(joined, request_id)
                    if message is not None:
                        return message
                continue
            if text.startswith(":"):
                continue
            field_name, _, value = text.partition(":")
            if field_name == "data":
                data_lines.append(value[1:] if value.startswith(" ") else value)
        if data_lines:
            message = self._decode_event("\n".join(data_lines), request_id)
            if message is not None:
                return message
        raise ProtocolError(
            "event stream closed before the response to {0!r} arrived".format(request_id)
        )

    def _decode_event(self, payload: str, request_id) -> Optional[dict]:
        if payload.strip() == "[DONE]":
            return None
        try:
            decoded = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(decoded, dict):
            return None
        if request_id is None or decoded.get("id") == request_id:
            if self._verbose:
                print("< {0}".format(payload), file=sys.stderr)
            return decoded
        return None


def build_transport(descriptor: ServerDescriptor, verbose: bool = False) -> Transport:
    """Create the transport described by a validated descriptor."""
    if descriptor.transport == "stdio":
        return StdioTransport(
            command=descriptor.command,
            args=descriptor.args,
            env=descriptor.env,
            cwd=descriptor.cwd,
            timeout=descriptor.timeout,
            inherit_env=descriptor.inherit_env,
            verbose=verbose,
        )
    return HttpTransport(
        url=descriptor.url,
        headers=descriptor.headers,
        timeout=descriptor.timeout,
        verbose=verbose,
    )
