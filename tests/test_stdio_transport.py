import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import unittest

from mcpassert.errors import ProtocolError, TransportError
from mcpassert.mcpclient import McpClient
from mcpassert.transports import StdioTransport

from support import MOCK_STDIO_SERVER


def start_server(timeout=10.0, env=None):
    transport = StdioTransport(
        command=[sys.executable, str(MOCK_STDIO_SERVER)],
        timeout=timeout,
        env=env,
    )
    return transport


class StdioHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.transport = start_server()
        self.addCleanup(self.transport.close)
        self.client = McpClient(self.transport, client_version="test")

    def test_initialize_reports_the_server(self):
        self.client.initialize()
        self.assertEqual(self.client.server_info["name"], "mock-stdio-server")

    def test_initialize_negotiates_a_protocol_version(self):
        self.client.initialize()
        self.assertEqual(self.client.negotiated_protocol_version, "2025-06-18")

    def test_initialize_reports_the_capabilities(self):
        self.client.initialize()
        self.assertIn("tools", self.client.capabilities)

    def test_calling_before_the_handshake_is_refused(self):
        with self.assertRaises(ProtocolError):
            self.client.list_tools()


class StdioPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.transport = start_server()
        self.addCleanup(self.transport.close)
        self.client = McpClient(self.transport, client_version="test")
        self.client.initialize()

    def test_list_tools(self):
        names = [tool["name"] for tool in self.client.list_tools()]
        self.assertEqual(
            names, ["echo", "add", "fail", "boom", "sleep", "stamp"]
        )

    def test_tool_descriptions_are_present(self):
        echo = self.client.list_tools()[0]
        self.assertTrue(echo["description"])
        self.assertIn("inputSchema", echo)

    def test_list_resources(self):
        resources = self.client.list_resources()
        self.assertEqual(resources[0]["uri"], "mock://greeting")

    def test_list_prompts(self):
        prompts = self.client.list_prompts()
        self.assertEqual(prompts[0]["name"], "greet")

    def test_call_returns_the_arguments(self):
        message = self.client.call_tool("echo", {"a": 1})
        self.assertEqual(message["result"]["structuredContent"], {"a": 1})

    def test_call_adds_two_numbers(self):
        message = self.client.call_tool("add", {"a": 2, "b": 3})
        self.assertEqual(message["result"]["structuredContent"], {"sum": 5})

    def test_tool_level_error_is_a_result(self):
        message = self.client.call_tool("fail", {})
        self.assertTrue(message["result"]["isError"])

    def test_protocol_error_is_returned_as_an_error(self):
        message = self.client.call_tool("boom", {})
        self.assertNotIn("result", message)
        self.assertEqual(message["error"]["code"], -32000)

    def test_unknown_tool_comes_back_as_an_error(self):
        message = self.client.call_tool("does-not-exist", {})
        self.assertNotIn("result", message)
        self.assertEqual(message["error"]["code"], -32602)
        self.assertIn("does-not-exist", message["error"]["message"])


class StdioFailureTests(unittest.TestCase):
    def test_a_server_that_dies_is_reported(self):
        transport = start_server(env={"MCPTEST_CRASH": "1"})
        self.addCleanup(transport.close)
        client = McpClient(transport, client_version="test")
        with self.assertRaises(TransportError):
            client.initialize()

    def test_a_slow_server_times_out(self):
        transport = start_server(timeout=0.5)
        self.addCleanup(transport.close)
        client = McpClient(transport, client_version="test")
        client.initialize()
        with self.assertRaises(TransportError) as caught:
            client.call_tool("sleep", {"seconds": 5})
        self.assertIn("timed out", str(caught.exception))

    def test_a_missing_executable_is_reported(self):
        with self.assertRaises(TransportError) as caught:
            StdioTransport(command=["this-program-does-not-exist"])
        self.assertIn("could not start server", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
