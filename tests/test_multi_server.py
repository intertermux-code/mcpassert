import json
import os
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from mcpassert.errors import EXIT_ASSERTION, EXIT_SETUP, EXIT_SUCCESS

from support import (
    fixture_payload,
    http_descriptor,
    response,
    run_cli,
    stdio_descriptor,
    summary_lines,
    write_json,
)

ECHO_CALL = {
    "method": "tools/call",
    "params": {"name": "echo", "arguments": {"a": 1}},
    "response": response(
        {
            "content": [{"type": "text", "text": '{"a": 1}'}],
            "isError": False,
            "structuredContent": {"a": 1},
        },
        message_id=2,
    ),
}


class MultipleServersTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.first = self.descriptor("first.json")
        self.second = self.descriptor("second.json")

    def descriptor(self, name):
        return write_json(os.path.join(self.directory.name, name), stdio_descriptor())

    def test_two_servers_are_checked_in_one_run(self):
        code, out, err = run_cli(["list", self.first, self.second])
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(
            summary_lines(err), [(self.first, "PASS"), (self.second, "PASS")]
        )
        self.assertIn("all 2 server(s) passed", err)
        self.assertEqual(out.count("tools ("), 2)

    def test_every_server_runs_even_when_a_check_fails(self):
        code, out, err = run_cli(
            [
                "call",
                self.first,
                self.second,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 2",
            ]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertEqual(
            [status for _, status in summary_lines(err)], ["FAIL", "FAIL"]
        )
        self.assertIn("2 of 2 server(s) failed", err)
        self.assertEqual(err.count("FAILED"), 2)

    def test_each_server_is_named_as_it_runs(self):
        code, out, err = run_cli(
            [
                "call",
                self.first,
                self.second,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 1",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("mcp-test: {0}".format(self.first), err)
        self.assertIn("mcp-test: {0}".format(self.second), err)

    def test_a_broken_server_does_not_stop_the_run(self):
        code, out, err = run_cli(["list", "no-such-file.json", self.first])
        self.assertEqual(code, EXIT_SETUP)
        self.assertEqual(
            [status for _, status in summary_lines(err)], ["ERROR", "PASS"]
        )
        self.assertIn("1 of 2 server(s) failed", err)
        self.assertIn("echo", out)

    def test_the_worst_result_of_the_run_is_the_exit_code(self):
        code, out, err = run_cli(
            [
                "call",
                self.first,
                self.second,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 2",
            ]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        code, out, err = run_cli(["list", self.first, "no-such-file.json"])
        self.assertEqual(code, EXIT_SETUP)

    def test_quiet_keeps_the_summary_and_drops_the_rest(self):
        code, out, err = run_cli(["list", self.first, self.second, "--quiet"])
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertNotIn("tools (", err)
        self.assertIn("mcp-test: summary", err)

    def test_a_single_server_reports_no_summary(self):
        code, out, err = run_cli(["list", self.first])
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(summary_lines(err), [])
        self.assertNotIn("mcp-test: {0}".format(self.first), err)


class TransportMixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mock_http_server

        cls.server = mock_http_server.start_server(0)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stdio = write_json(
            os.path.join(self.directory.name, "stdio.json"), stdio_descriptor()
        )
        self.http = write_json(
            os.path.join(self.directory.name, "http.json"), http_descriptor(self.port)
        )

    def test_both_transports_answer_in_one_run(self):
        code, out, err = run_cli(
            [
                "call",
                self.stdio,
                self.http,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 1",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertEqual(
            [label for label, _ in summary_lines(err)], [self.stdio, self.http]
        )

    def test_a_server_that_is_not_there_fails_only_itself(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        dead = write_json(
            os.path.join(self.directory.name, "dead.json"),
            http_descriptor(dead_port),
        )
        code, out, err = run_cli(["list", dead, self.http])
        self.assertEqual(code, EXIT_SETUP)
        self.assertEqual(
            [status for _, status in summary_lines(err)], ["ERROR", "PASS"]
        )


class ServersFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        self.first = self.write("first.json")
        self.second = self.write("second.json")
        self.fixture = self.write_path("session.json", fixture_payload([ECHO_CALL]))

    def write(self, name):
        return self.write_path(name, stdio_descriptor())

    def write_path(self, name, payload):
        return write_json(os.path.join(self.root, name), payload)

    def servers_file(self, entries, directory=None):
        target = os.path.join(directory or self.root, "servers.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
        return target

    def test_a_list_of_descriptors(self):
        path = self.servers_file({"servers": [self.first, self.second]})
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertIn("all 2 server(s) passed", err)

    def test_named_servers_are_reported_by_their_name(self):
        path = self.servers_file(
            {
                "servers": [
                    {"name": "alpha", "descriptor": self.first},
                    {"name": "beta", "descriptor": self.second},
                ]
            }
        )
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertEqual(summary_lines(err), [("alpha", "PASS"), ("beta", "PASS")])

    def test_relative_paths_point_at_the_servers_file(self):
        deeper = os.path.join(self.root, "suite")
        os.mkdir(deeper)
        self.write_path(os.path.join("suite", "echo.json"), stdio_descriptor())
        path = self.servers_file(
            {"servers": [{"name": "again", "descriptor": "echo.json"}, self.first]},
            directory=deeper,
        )
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertEqual(summary_lines(err), [("again", "PASS"), (self.first, "PASS")])

    def test_a_fixture_can_be_one_of_the_servers(self):
        path = self.servers_file(
            {"servers": [{"name": "live", "descriptor": self.first},
                         {"name": "replay", "fixture": self.fixture}]}
        )
        code, out, err = run_cli(
            [
                "call",
                "--servers",
                path,
                "echo",
                "--args", '{"a": 1}',
                "--expect",
                "result.a == 1",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertEqual(summary_lines(err), [("live", "PASS"), ("replay", "PASS")])

    def test_the_file_and_the_arguments_mix(self):
        path = self.servers_file({"servers": [self.second]})
        code, out, err = run_cli(["list", self.first, "--servers", path])
        self.assertEqual(code, EXIT_SUCCESS, err)
        self.assertEqual(len(summary_lines(err)), 2)

    def test_a_bare_array_is_accepted(self):
        path = self.servers_file([self.first, self.second])
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SUCCESS, err)

    def test_a_servers_file_is_not_a_descriptor(self):
        path = self.servers_file({"servers": [self.first]})
        code, out, err = run_cli(["list", path])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("--servers", err)

    def test_a_missing_file_is_reported(self):
        code, out, err = run_cli(
            ["list", "--servers", os.path.join(self.root, "nope.json")]
        )
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("not found", err)

    def test_an_empty_list_is_reported(self):
        path = self.servers_file({"servers": []})
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("non-empty", err)

    def test_a_server_without_a_target_is_reported(self):
        path = self.servers_file({"servers": [{"name": "hollow"}]})
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("'descriptor'", err)

    def test_a_server_cannot_be_both_at_once(self):
        path = self.servers_file(
            {"servers": [{"descriptor": self.first, "fixture": self.fixture}]}
        )
        code, out, err = run_cli(["list", "--servers", path])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("not both", err)


if __name__ == "__main__":
    unittest.main()
