import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import json
import unittest
import urllib.error
import urllib.request

from mcpassert.errors import ProtocolError, TransportError
from mcpassert.mcpclient import McpClient
from mcpassert.transports import HttpTransport

import mock_http_server


class HttpServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock_http_server.start_server(0)
        cls.port = cls.server.server_address[1]
        cls.url = "http://127.0.0.1:{0}/mcp".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class HttpHandshakeTests(HttpServerTestCase):
    def setUp(self):
        self.transport = HttpTransport(self.url, timeout=10)
        self.addCleanup(self.transport.close)
        self.client = McpClient(self.transport, client_version="test")

    def test_initialize_reports_the_server(self):
        self.client.initialize()
        self.assertEqual(self.client.server_info["name"], "mock-http-server")

    def test_the_session_id_is_kept(self):
        self.client.initialize()
        self.assertEqual(self.transport._session_id, mock_http_server.SESSION_ID)

    def test_list_tools_arrives_as_an_event_stream(self):
        self.client.initialize()
        names = [tool["name"] for tool in self.client.list_tools()]
        self.assertEqual(names, ["echo", "add"])

    def test_call_returns_the_arguments(self):
        self.client.initialize()
        message = self.client.call_tool("echo", {"a": 1})
        self.assertEqual(message["result"]["structuredContent"], {"a": 1})

    def test_call_adds_two_numbers(self):
        self.client.initialize()
        message = self.client.call_tool("add", {"a": 2, "b": 3})
        self.assertEqual(message["result"]["structuredContent"], {"sum": 5})

    def test_unknown_tool_comes_back_as_an_error(self):
        self.client.initialize()
        message = self.client.call_tool("nope", {})
        self.assertNotIn("result", message)
        self.assertEqual(message["error"]["code"], -32602)

    def test_calling_before_the_handshake_is_refused(self):
        with self.assertRaises(ProtocolError):
            self.client.list_tools()


class HttpSessionTests(HttpServerTestCase):
    def test_a_request_without_a_session_id_is_refused(self):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_the_transport_reports_a_missing_session_id(self):
        transport = HttpTransport(self.url, timeout=5)
        self.addCleanup(transport.close)
        message = transport.request("tools/list", {})
        self.assertIn("error", message)
        self.assertEqual(message["error"]["code"], -32001)

    def test_the_session_id_is_sent_with_later_requests(self):
        transport = HttpTransport(self.url, timeout=5)
        self.addCleanup(transport.close)
        client = McpClient(transport, client_version="test")
        client.initialize()
        self.assertEqual(transport._session_id, mock_http_server.SESSION_ID)
        # The server refuses requests without the session id, so a working
        # tools/list proves the header was sent.
        self.assertEqual(len(client.list_tools()), 2)

    def test_a_dead_server_is_reported(self):
        transport = HttpTransport("http://127.0.0.1:1/mcp", timeout=2)
        self.addCleanup(transport.close)
        with self.assertRaises(TransportError) as caught:
            transport.request("initialize", {})
        self.assertIn("could not reach", str(caught.exception))

    def test_the_legacy_get_transport_is_not_supported(self):
        request = urllib.request.Request(self.url, method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
