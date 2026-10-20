import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import json
import os
import subprocess
import sys
import tempfile
import unittest

from mcpassert import validation
from mcpassert.errors import EXIT_ASSERTION, EXIT_SETUP, EXIT_SUCCESS

import mock_http_server
from support import http_descriptor, run_cli, stdio_descriptor, write_json


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.descriptor = write_json(
            os.path.join(self.directory.name, "server.json"), stdio_descriptor()
        )


class ListCommandTests(CliTestCase):
    def test_list_prints_the_tools(self):
        code, out, err = run_cli(["list", self.descriptor])
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("echo", out)
        self.assertIn("add", out)
        self.assertIn("tools (", out)

    def test_list_can_print_the_raw_document(self):
        code, out, err = run_cli(["list", self.descriptor, "--json"])
        document = json.loads(out)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("echo", [tool["name"] for tool in document["result"]["tools"]])

    def test_list_can_include_resources_and_prompts(self):
        code, out, err = run_cli(
            ["list", self.descriptor, "--json", "--include-resources", "--include-prompts"]
        )
        document = json.loads(out)
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertEqual(document["result"]["resources"][0]["uri"], "mock://greeting")
        self.assertEqual(document["result"]["prompts"][0]["name"], "greet")

    def test_a_passing_expectation(self):
        code, out, err = run_cli(
            ["list", self.descriptor, "--expect", 'result.tools.*.name contains "echo"']
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("PASS", err)

    def test_a_failing_expectation(self):
        code, out, err = run_cli(
            ["list", self.descriptor, "--expect", 'result.tools.*.name contains "nope"']
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("FAILED", err)
        self.assertIn("expected:", err)
        self.assertIn("actual:", err)


class CallCommandTests(CliTestCase):
    def test_call_echoes_the_arguments(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}']
        )
        self.assertEqual(code, EXIT_SUCCESS)
        document = json.loads(out)
        self.assertEqual(document["result"]["structuredContent"], {"a": 1})
        self.assertIn('"a": 1', document["result"]["content"][0]["text"])

    def test_a_passing_expectation(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}', "--expect", "result.a == 1"]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertIn("PASS", err)

    def test_a_failing_expectation_shows_expected_and_actual(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}', "--expect", "result.a == 2"]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("expected: 2", err)
        self.assertIn("actual:   1", err)

    def test_several_expectations_are_anded(self):
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                '{"a": 1}',
                "--expect",
                "result.a == 1",
                "--expect",
                "result.b == 2",
            ]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("PASS", err)
        self.assertIn("FAILED", err)

    def test_the_wildcard_operator(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}', "--expect", "result.a == *"]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_the_contains_operator(self):
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                '{"a": "needle"}',
                "--expect",
                'result.a contains "need"',
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_the_not_equal_operator(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}', "--expect", "result.a != 2"]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_the_exists_operator(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", '{"a": 1}', "--expect", "error exists"]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("FAILED", err)

    def test_add_returns_the_sum(self):
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "add",
                "--args",
                '{"a": 2, "b": 3}',
                "--expect",
                "result.sum == 5",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_a_protocol_error_is_reported(self):
        code, out, err = run_cli(["call", self.descriptor, "boom"])
        self.assertEqual(code, EXIT_SETUP)
        document = json.loads(out)
        self.assertEqual(document["error"]["code"], -32000)

    def test_a_protocol_error_can_be_asserted_on(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "boom", "--expect", "error exists"]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_a_tool_error_is_reported(self):
        code, out, err = run_cli(["call", self.descriptor, "fail"])
        self.assertEqual(code, EXIT_SETUP)
        document = json.loads(out)
        self.assertTrue(document["result"]["isError"])
        self.assertIn("refused", document["error"]["message"])


class UsageErrorTests(CliTestCase):
    def test_a_missing_descriptor_is_reported(self):
        code, out, err = run_cli(["list", "no-such-file.json"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("not found", err)

    def test_a_broken_descriptor_is_reported(self):
        path = os.path.join(self.directory.name, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"transport": "telepathy"}')
        code, out, err = run_cli(["list", path])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("telepathy", err)

    def test_invalid_arguments_are_reported(self):
        code, out, err = run_cli(
            ["call", self.descriptor, "echo", "--args", "{not json"]
        )
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("not valid JSON", err)

    def test_an_unknown_tool_is_reported(self):
        code, out, err = run_cli(["call", self.descriptor, "nope"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("nope", err)

    def test_update_without_a_snapshot_is_reported(self):
        code, out, err = run_cli(["list", self.descriptor, "--update"])
        self.assertEqual(code, EXIT_SETUP)
        self.assertIn("--update", err)

    def test_a_missing_command_is_reported(self):
        code, out, err = run_cli([])
        self.assertEqual(code, EXIT_SETUP)


class SnapshotTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.snapshot_dir = os.path.join(self.directory.name, "snapshots")
        self.snapshot = os.path.join(self.snapshot_dir, "demo.json")

    def call_with_snapshot(self, arguments, *extra):
        return run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                arguments,
                "--snapshot",
                "demo",
                "--snapshot-dir",
                self.snapshot_dir,
            ]
            + list(extra)
        )

    def test_the_first_run_records_the_snapshot(self):
        code, out, err = self.call_with_snapshot('{"a": 1}')
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.exists(self.snapshot))
        with open(self.snapshot, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["result"]["structuredContent"]["a"], 1)

    def test_the_second_run_matches(self):
        self.call_with_snapshot('{"a": 1}')
        code, out, err = self.call_with_snapshot('{"a": 1}')
        self.assertEqual(code, EXIT_SUCCESS)

    def test_a_changed_result_fails_with_a_diff(self):
        self.call_with_snapshot('{"a": 1}')
        code, out, err = self.call_with_snapshot('{"a": 2}')
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("does not match", err)
        self.assertIn("-", err)
        self.assertIn("+", err)

    def test_update_rewrites_the_snapshot(self):
        self.call_with_snapshot('{"a": 1}')
        code, out, err = self.call_with_snapshot('{"a": 2}', "--update")
        self.assertEqual(code, EXIT_SUCCESS)
        code, out, err = self.call_with_snapshot('{"a": 2}')
        self.assertEqual(code, EXIT_SUCCESS)

    def test_ignored_paths_hide_volatile_values(self):
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "stamp",
                "--snapshot",
                "stamp",
                "--snapshot-dir",
                self.snapshot_dir,
                "--ignore-paths",
                "result.now",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        with open(os.path.join(self.snapshot_dir, "stamp.json"), encoding="utf-8") as f:
            document = json.load(f)
        self.assertNotIn("now", document["result"])
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "stamp",
                "--snapshot",
                "stamp",
                "--snapshot-dir",
                self.snapshot_dir,
                "--ignore-paths",
                "result.now",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)

    def test_a_list_can_be_snapshotted(self):
        code, out, err = run_cli(
            [
                "list",
                self.descriptor,
                "--snapshot",
                "tools",
                "--snapshot-dir",
                self.snapshot_dir,
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)
        self.assertTrue(os.path.exists(os.path.join(self.snapshot_dir, "tools.json")))


class HttpCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock_http_server.start_server(0)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.descriptor = write_json(
            os.path.join(self.directory.name, "server.json"),
            http_descriptor(self.port),
        )

    def test_list_over_http(self):
        code, out, err = run_cli(["list", self.descriptor, "--json"])
        self.assertEqual(code, EXIT_SUCCESS)
        document = json.loads(out)
        self.assertIn("echo", [tool["name"] for tool in document["result"]["tools"]])

    def test_call_over_http(self):
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                '{"a": 1}',
                "--expect",
                "result.a == 1",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)


class SchemaValidationTests(CliTestCase):
    def test_the_arguments_are_validated_against_the_input_schema(self):
        if not validation.is_available():
            code, out, err = run_cli(
                [
                    "call",
                    self.descriptor,
                    "echo",
                    "--args",
                    '{"a": "not a number"}',
                    "--validate-schema",
                ]
            )
            self.assertEqual(code, EXIT_SETUP)
            self.assertIn("jsonschema", err)
            return

        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                '{"a": "not a number"}',
                "--validate-schema",
            ]
        )
        self.assertEqual(code, EXIT_ASSERTION)
        self.assertIn("schema", err)

    def test_valid_arguments_pass_the_validation(self):
        if not validation.is_available():
            self.skipTest("jsonschema is not installed")
        code, out, err = run_cli(
            [
                "call",
                self.descriptor,
                "echo",
                "--args",
                '{"a": 1}',
                "--validate-schema",
            ]
        )
        self.assertEqual(code, EXIT_SUCCESS)


class EntryPointTests(CliTestCase):
    def test_the_module_can_be_run(self):
        environment = dict(os.environ)
        source = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
        )
        environment["PYTHONPATH"] = source
        completed = subprocess.run(
            [sys.executable, "-m", "mcpassert", "list", self.descriptor],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("echo", completed.stdout)


if __name__ == "__main__":
    unittest.main()
