"""A very small MCP client.

Only the parts of the protocol a test harness needs are implemented: the
initialise handshake, listing primitives and calling a tool. Every call goes
through the same transport, so stdio and HTTP servers behave identically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import ProtocolError
from .transports import Transport

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcp-test"


class McpClient:
    """Drives the MCP lifecycle on top of a transport."""

    def __init__(
        self,
        transport: Transport,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        client_version: str = "0.0.0",
    ):
        self._transport = transport
        self._protocol_version = protocol_version
        self._client_version = client_version
        self._initialized = False
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.negotiated_protocol_version = protocol_version

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    def initialize(self) -> Dict[str, Any]:
        """Run the ``initialize`` / ``notifications/initialized`` handshake."""
        params = {
            "protocolVersion": self._protocol_version,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": self._client_version},
        }
        message = self._transport.request("initialize", params)
        error = message.get("error")
        if error:
            raise ProtocolError(
                "server rejected the initialize handshake: {0}".format(
                    _format_error(error)
                )
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("server did not return an initialize result")
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.negotiated_protocol_version = (
            result.get("protocolVersion") or self._protocol_version
        )
        self._transport.set_protocol_version(self.negotiated_protocol_version)
        self._transport.notify("notifications/initialized")
        self._initialized = True
        return result

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ProtocolError("the MCP handshake has not been completed")

    # -- primitives -------------------------------------------------------

    def list_tools(self) -> List[Dict[str, Any]]:
        self._require_initialized()
        result = self._request("tools/list", {})
        tools = result.get("tools")
        if tools is None:
            raise ProtocolError("tools/list did not return a 'tools' array")
        return tools

    def list_resources(self) -> List[Dict[str, Any]]:
        self._require_initialized()
        result = self._request("resources/list", {})
        return result.get("resources") or []

    def list_prompts(self) -> List[Dict[str, Any]]:
        self._require_initialized()
        result = self._request("prompts/list", {})
        return result.get("prompts") or []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None):
        """Call a tool and return the raw JSON-RPC response message.

        Unlike the listing helpers this does not raise on a JSON-RPC error:
        a test usually wants to assert on the error itself.
        """
        self._require_initialized()
        params: Dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self._transport.request("tools/call", params)

    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        message = self._transport.request(method, params)
        error = message.get("error")
        if error:
            raise ProtocolError(
                "server returned an error for {0!r}: {1}".format(
                    method, _format_error(error)
                )
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("{0} did not return a result object".format(method))
        return result


def _format_error(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    code = error.get("code")
    message = error.get("message", "")
    if code is None:
        return str(message)
    return "[{0}] {1}".format(code, message)
