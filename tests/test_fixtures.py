import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from mcpassert import validation
from mcpassert.errors import EXIT_ASSERTION, EXIT_SETUP, EXIT_SUCCESS, FixtureError
from mcpassert.fixtures import FORMAT, Fixture, FixtureTransport, RecordingTransport
from mcpassert.mcpclient import McpClient
from mcpassert.transports import StdioTransport

from support import (
    MOCK_STDIO_SERVER,
    fixture_payload,
    response,
    run_cli,
    stdio_descriptor,
    write_json,
)

ECHO_ARGS = {"a": 1}
ECHO_CALL = {
    "method": "tools/call",
    "params": {"name": "echo", "arguments": ECHO_ARGS},
    "response": response(
        {
            "content": [{"type": "text", "text": '{"a": 1}'}],
            "isError": False,
            "structuredContent": {"a": 1},
        },
        message_id=2,
    ),
}

TOOLS_LIST = {
    "method": "tools/list",
    "params": {},
    "response": response(
        {"tools": [{"name": "echo", "description": "Return the arguments unchanged"}]},
        message_id=2,
    ),
}


def stamped(value):
    """A recorded ``stamp`` call whose answer carries ``value``.

    Two of these are the same request, which is what the tool does: the
    arguments do not change, only the answer.
    """
    return {
        "method": "tools/call",
        "params": {"name": "stamp", "arguments": {}},
        "response": response(
            {"content": [], "isError": False, "structuredContent": {"now": value}},
            message_id=3,
        ),
    }


class FixtureFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_a_fixture_round_trips_through_the_file(self):
        fixture = Fixture.from_dict(fixture_payload([ECHO_CALL]))
        path = os.path.join(self.directory.name, "nested", "session.json")
        fixture.write(path)
        loaded = Fixture.from_file(path)

        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["format"], FORMAT)
        self.assertEqual(len(loaded.interactions), len(fixture.interactions))
        self.assertEqual(loaded.interactions[1].params, ECHO_CALL["params"])
        self.assertEqual(loaded.interactions[1].response, ECHO_CALL["response"])
        self.assertEqual(
            loaded.server_info, {"name": "fixture-server", "version": "1.0.0"}
        )

    def test_a_server_descriptor_is_not_a_fixture(self):
        with self.assertRaises(FixtureError) as raised:
            Fixture.from_dict({"transport": "stdio", "command": ["server"]})
        self.assertIn("descriptor", str(raised.exception))

    def test_a_file_without_the_marker_is_not_a_fixture(self):
        with self.assertRaises(FixtureError) as raised:
            Fixture.from_dict({"interactions": []})
        self.assertIn("format", str(raised.exception))

    def test_an_interaction_without_an_answer_is_reported(self):
        payload = fixture_payload([])
        payload["interactions"].append({"method": "tools/call"})
        with self.assertRaises(FixtureError) as raised:
            Fixture.from_dict(payload)
        self.assertIn("response", str(raised.exception))

    def test_a_missing_fixture_is_reported(self):
        with self.assertRaises(FixtureError):
            Fixture.from_file("no-such-fixture.json")


class ReplayTransportTests(unittest.TestCase):
    def transport(self, interactions):
        return FixtureTransport(Fixture.from_dict(fixture_payload(interactions)))

    def request(self, interactions, method, params=None):
        return self.transport(interactions).request(method, params)

    def test_the_handshake_is_answered_from_the_fixture(self):
        message = self.request(
            [],
            "initialize",
            {"protocolVersion": "2025-06-18", "clientInfo": {"name": "other"}},
        )
        self.assertEqual(message["result"]["serverInfo"]["name"], "fixture-server")

    def test_a_recorded_call_is_answered(self):
        message = self.request([ECHO_CALL], "tools/call", ECHO_CALL["params"])
        self.assertEqual(message["result"]["structuredContent"], {"a": 1})

    def test_several_answers_are_handed_out_in_order(self):
        transport = self.transport([stamped(1), stamped(2)])
        seen = [
            transport.request("tools/call", stamped(1)["params"])["result"][
                "structuredContent"
            ]
            for _ in range(3)
        ]
        self.assertEqual(seen, [{"now": 1}, {"now": 2}, {"now": 2}])

    def test_a_call_that_was_not_recorded_is_reported(self):
        transport = self.transport([ECHO_CALL])
        with self.assertRaises(FixtureError) as raised:
            transport.request("tools/call", {"name": "echo", "arguments": {"a": 2}})
        self.assertIn("holds no answer", str(raised.exception))
        self.assertIn("call 'echo' with", str(raised.exception))
        self.assertIn("recorded in the fixture", raised.exception.details)

    def test_a_method_that_was_not_recorded_is_reported(self):
        with self.assertRaises(FixtureError):
            self.request([ECHO_CALL], "prompts/list", {})

    def test_a_hand_written_entry_needs_no_params(self):
        payload = fixture_payload([])
        payload["interactions"].append(
            {"method": "tools/list", "response": response({"tools": []}, message_id=2)}
        )
        transport = FixtureTransport(Fixture.from_dict(payload))
        message = transport.request("tools/list", {})
        self.assertEqual(message["result"]["tools"], [])

    def test_notifications_are_dropped(self):
        self.assertIsNone(self.transport([]).notify("notifications/initialized"))


class RecordingTransportTests(unittest.TestCase):
    def record_session(self, recording, calls):
        """Talk to the mock server through a recorder and return what it saw."""
        live = StdioTransport(command=[sys.executable, str(MOCK_STDIO_SERVER)])
        recorder = RecordingTransport(live, recording)
        client = McpClient(recorder)
        client.initialize()
        try:
            for name, arguments in calls:
                client.call_tool(name, arguments)
        finally:
            client.close()
        return recorder

    def test_the_exchanges_of_a_session_are_collected(self):
        recorder = self.record_session(Fixture(), [("add", {"a": 2, "b": 3})])
        methods = [item.method for item in recorder.fixture.interactions]

        self.assertEqual(methods, ["initialize", "tools/call"])
        self.assertEqual(recorder.fixture.server_info["name"], "mock-stdio-server")
        self.assertEqual(recorder.fixture.protocol_version, "2025-06-18")

    def test_a_recorded_session_replays_itself(self):
        recorder = self.record_session(Fixture(), [("add", {"a": 2, "b": 3})])
        answer = recorder.fixture.interactions[-1].response

        replay = McpClient(FixtureTransport(recorder.fixture))
        replay.initialize()
        try:
            self.assertEqual(replay.call_tool("add", {"a": 2, "b": 3}), answer)
        finally:
            replay.close()

    def test_a_second_session_needs_no_second_handshake(self):
        recording = Fixture.from_dict(fixture_payload([ECHO_CALL]))
        recorder = self.record_session(recording, [("add", {"a": 2, "b": 3})])
        methods = [item.method for item in recorder.fixture.interactions]
        self.assertEqual(methods, ["initialize", "tools/call", "tools/call"])

        replay = McpClient(FixtureTransport(recorder.fixture))
        replay.initialize()
        try:
            self.assertEqual(
                replay.call_tool("echo", ECHO_ARGS)["result"]["structuredContent"],
                {"a": 1},
            )
            self.assertEqual(
                replay.call_tool("add", {"a": 2, "b": 3})["result"]["structuredContent"],
                {"sum": 5},
            )
        finally:
            replay.close()


class RecordCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.descriptor = write_json(
            os.path.join(self.directory.name, "server.json"), stdio_descriptor()
        )
        self.fixture = os.path.join(self.directory.name, "session.json")

    def record(self, *argv):
        return run_cli(list(argv) + ["--record", self.fixture])

    def stored(self):
        with open(self.fixture, encoding="utf-8") as handle:
            return json.load(handle)

    def test_a_call_records_the_answer(self):
        code, out, err = self.record(
            "call", self.descriptor, "echo", "--args", '{"a": 1}'
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("wrote fixture", err)

        stored = self.stored()
        self.assertEqual(stored["format"], FORMAT)
        self.assertEqual(stored["transport"], "stdio")
        self.assertEqual(
            [item["method"] for item in stored["interactions"]],
            ["initialize", "tools/call"],
        )
        call = stored["interactions"][1]
        self.assertEqual(call["params"], {"name": "echo", "arguments": {"a": 1}})
        self.assertEqual(call["response"]["result"]["structuredContent"], {"a": 1})

    def test_a_list_records_the_tools(self):
        code, listed, err = self.record("list", self.descriptor, "--json")
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(
            [item["method"] for item in self.stored()["interactions"]],
            ["initialize", "tools/list"],
        )

        code, replayed, err = run_cli(["list", "--fixture", self.fixture, "--json"])
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(json.loads(replayed), json.loads(listed))

    def test_the_replay_answers_like_the_server(self):
        code, live, err = run_cli(
            [
                "call",
                self.descriptor,
                "add",
                "--args", '{"a": 2, "b": 3}',
                "--expect",
                "result.sum == 5",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.record("call", self.descriptor, "add", "--args", '{"a": 2, "b": 3}')

        code, replayed, err = run_cli(
            [
                "call",
                "--fixture",
                self.fixture,
                "add",
                "--args", '{"a": 2, "b": 3}',
                "--expect",
                "result.sum == 5",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(json.loads(replayed), json.loads(live))

    def test_append_adds_to_the_recording(self):
        self.record("call", self.descriptor, "echo", "--args", '{"a": 1}')
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "add",
                "--args", '{"a": 2, "b": 3}',
                "--record",
                self.fixture,
                "--append",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("3 interaction(s)", err)
        self.assertEqual(
            [item["method"] for item in self.stored()["interactions"]],
            ["initialize", "tools/call", "tools/call"],
        )

        code, out, err = run_cli(
            [
                "call",
                "--fixture",
                self.fixture,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 1",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS, err)
        code, out, err = run_cli(
            [
                "call",
                "--fixture",
                self.fixture,
                "add",
                "--args", '{"a": 2, "b": 3}',
                "--expect",
                "result.sum == 5",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS, err)

    def test_the_tools_are_recorded_for_the_schema_checks(self):
        if not validation.is_available():
            self.skipTest("jsonschema is not installed")
        code, out, err = self.record(
            "call", self.descriptor, "echo", "--args", '{"a": 1}', "--validate-schema"
        )
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertIn(
            "tools/list", [item["method"] for item in self.stored()["interactions"]]
        )

        code, out, err = run_cli(
            [
                "call",
                "--fixture",
                self.fixture,
                "echo",
                "--args", '{"a": 1}',
                "--validate-schema",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS, err)

    def test_a_server_that_never_answered_leaves_no_file(self):
        broken = write_json(
            os.path.join(self.directory.name, "broken.json"),
            stdio_descriptor(command=[sys.executable, "-c", "raise SystemExit(3)"]),
        )
        code, out, err = self.record("list", broken)
        self.assertEqual(code, EXIT_SETUP)
        self.assertFalse(os.path.exists(self.fixture))

    def test_recording_needs_a_server(self):
        code, out, err = run_cli(
            ["call", "--fixture", self.fixture, "echo", "--record", "x.json"]
        )
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("only one of them", err)

    def test_append_needs_a_recording(self):
        code, out, err = run_cli(["list", self.descriptor, "--append"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("--append", err)

    def test_recording_covers_one_session(self):
        second = write_json(
            os.path.join(self.directory.name, "second.json"), stdio_descriptor()
        )
        code, out, err = self.record("list", self.descriptor, second)
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("one server", err)


class ReplayCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = write_json(
            os.path.join(self.directory.name, "session.json"),
            fixture_payload([ECHO_CALL]),
        )

    def replay(self, *argv):
        return run_cli(["call", "--fixture", self.fixture] + list(argv))

    def test_a_passing_expectation_against_a_fixture(self):
        code, out, err = self.replay(
            "echo", "--args", '{"a": 1}', "--expect", "result.a == 1"
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("PASS", err)
        self.assertEqual(json.loads(out)["result"]["structuredContent"], {"a": 1})

    def test_a_failing_expectation_against_a_fixture(self):
        code, out, err = self.replay(
            "echo", "--args", '{"a": 1}', "--expect", "result.a == 2"
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("expected: 2", err)
        self.assertIn("actual:   1", err)

    def test_a_list_against_a_fixture(self):
        tools = write_json(
            os.path.join(self.directory.name, "tools.json"), fixture_payload([TOOLS_LIST])
        )
        code, out, err = run_cli(
            [
                "list",
                "--fixture",
                tools,
                "--expect",
                'result.tools.*.name contains "echo"',
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_a_snapshot_against_a_fixture(self):
        snapshot_dir = os.path.join(self.directory.name, "snapshots")
        arguments = ["echo", "--args", '{"a": 1}', "--snapshot", "echo"]
        code, out, err = self.replay(*arguments, "--snapshot-dir", snapshot_dir)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.exists(os.path.join(snapshot_dir, "echo.json")))

        code, out, err = self.replay(*arguments, "--snapshot-dir", snapshot_dir)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("matches", err)

        changed = ["echo", "--args", '{"a": 2}', "--snapshot", "echo"]
        code, out, err = self.replay(*changed, "--snapshot-dir", snapshot_dir)
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("holds no answer", err)

    def test_a_missing_fixture_is_reported(self):
        code, out, err = run_cli(["call", "--fixture", "no-such-file.json", "echo"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("not found", err)

    def test_a_descriptor_is_not_a_fixture(self):
        descriptor = write_json(
            os.path.join(self.directory.name, "server.json"), stdio_descriptor()
        )
        code, out, err = run_cli(["list", "--fixture", descriptor])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("descriptor", err)

    def test_a_fixture_replaces_the_server(self):
        descriptor = write_json(
            os.path.join(self.directory.name, "server.json"), stdio_descriptor()
        )
        code, out, err = run_cli(["list", descriptor, "--fixture", self.fixture])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("--fixture", err)

    def test_no_server_and_no_fixture_is_reported(self):
        code, out, err = run_cli(["list"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("no server given", err)


if __name__ == "__main__":
    unittest.main()
