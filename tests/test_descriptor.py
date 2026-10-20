import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import unittest

from mcpassert.descriptor import ServerDescriptor
from mcpassert.errors import DescriptorError


class DescriptorTests(unittest.TestCase):
    def test_stdio_descriptor_is_parsed(self):
        descriptor = ServerDescriptor.from_dict(
            {
                "transport": "stdio",
                "command": ["python", "my_server.py"],
                "args": ["--verbose"],
                "env": {"TOKEN": "secret"},
                "cwd": "/tmp",
            }
        )
        self.assertEqual(descriptor.transport, "stdio")
        self.assertEqual(descriptor.command, ["python", "my_server.py"])
        self.assertEqual(descriptor.args, ["--verbose"])
        self.assertEqual(descriptor.env, {"TOKEN": "secret"})
        self.assertEqual(descriptor.cwd, "/tmp")
        self.assertEqual(descriptor.timeout, 30.0)

    def test_http_descriptor_is_parsed(self):
        descriptor = ServerDescriptor.from_dict(
            {
                "transport": "http",
                "url": "http://localhost:8000/mcp",
                "headers": {"Authorization": "Bearer token"},
                "timeout": 5,
            }
        )
        self.assertEqual(descriptor.transport, "http")
        self.assertEqual(descriptor.url, "http://localhost:8000/mcp")
        self.assertEqual(descriptor.headers, {"Authorization": "Bearer token"})
        self.assertEqual(descriptor.timeout, 5.0)

    def test_unknown_keys_are_ignored(self):
        descriptor = ServerDescriptor.from_dict(
            {
                "transport": "stdio",
                "command": ["python", "server.py"],
                "somethingElse": {"nested": True},
            }
        )
        self.assertEqual(descriptor.command, ["python", "server.py"])

    def test_type_is_accepted_as_alias_for_transport(self):
        descriptor = ServerDescriptor.from_dict(
            {"type": "http", "url": "http://localhost:8000/mcp"}
        )
        self.assertEqual(descriptor.transport, "http")

    def test_command_may_be_a_string(self):
        descriptor = ServerDescriptor.from_dict(
            {"transport": "stdio", "command": "python server.py --flag"}
        )
        self.assertEqual(descriptor.command, ["python", "server.py", "--flag"])

    def test_missing_transport_is_reported(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_dict({"command": ["python", "server.py"]})
        self.assertIn("transport", str(caught.exception))

    def test_unknown_transport_is_reported(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_dict({"transport": "carrier-pigeon"})
        self.assertIn("carrier-pigeon", str(caught.exception))

    def test_missing_command_is_reported(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_dict({"transport": "stdio"})
        self.assertIn("command", str(caught.exception))

    def test_missing_url_is_reported(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_dict({"transport": "http"})
        self.assertIn("url", str(caught.exception))

    def test_url_needs_a_scheme(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_dict({"transport": "http", "url": "localhost:8000"})
        self.assertIn("http://", str(caught.exception))

    def test_command_must_hold_strings(self):
        with self.assertRaises(DescriptorError):
            ServerDescriptor.from_dict({"transport": "stdio", "command": [1, 2]})

    def test_env_must_hold_strings(self):
        with self.assertRaises(DescriptorError):
            ServerDescriptor.from_dict(
                {"transport": "stdio", "command": ["python"], "env": {"A": 1}}
            )

    def test_timeout_must_be_positive(self):
        with self.assertRaises(DescriptorError):
            ServerDescriptor.from_dict(
                {"transport": "stdio", "command": ["python"], "timeout": 0}
            )

    def test_descriptor_must_be_an_object(self):
        with self.assertRaises(DescriptorError):
            ServerDescriptor.from_dict(["transport", "stdio"])


class EnvironmentVariableTests(unittest.TestCase):
    def test_headers_may_refer_to_the_environment(self):
        os.environ["MCPTEST_TOKEN"] = "s3cret"
        try:
            descriptor = ServerDescriptor.from_dict(
                {
                    "transport": "http",
                    "url": "http://localhost:8000/mcp",
                    "headers": {"Authorization": "Bearer ${MCPTEST_TOKEN}"},
                }
            )
        finally:
            del os.environ["MCPTEST_TOKEN"]
        self.assertEqual(
            descriptor.headers["Authorization"], "Bearer s3cret"
        )

    def test_environment_values_may_refer_to_the_environment(self):
        os.environ["MCPTEST_LOG_LEVEL"] = "debug"
        try:
            descriptor = ServerDescriptor.from_dict(
                {
                    "transport": "stdio",
                    "command": ["python3", "server.py"],
                    "env": {"LOG_LEVEL": "${MCPTEST_LOG_LEVEL}"},
                }
            )
        finally:
            del os.environ["MCPTEST_LOG_LEVEL"]
        self.assertEqual(descriptor.env["LOG_LEVEL"], "debug")

    def test_an_unset_variable_is_left_alone(self):
        descriptor = ServerDescriptor.from_dict(
            {
                "transport": "http",
                "url": "http://localhost:8000/mcp",
                "headers": {"Authorization": "Bearer ${MCPTEST_NOT_SET}"},
            }
        )
        self.assertEqual(
            descriptor.headers["Authorization"], "Bearer ${MCPTEST_NOT_SET}"
        )


class DescriptorFileTests(unittest.TestCase):
    def test_missing_file_is_reported(self):
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_file("does-not-exist.json")
        self.assertIn("not found", str(caught.exception))

    def test_invalid_json_is_reported(self, ):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            name = handle.name
        with self.assertRaises(DescriptorError) as caught:
            ServerDescriptor.from_file(name)
        self.assertIn("not valid JSON", str(caught.exception))
        os.unlink(name)

    def test_valid_file_is_read(self):
        import tempfile

        from support import write_json

        with tempfile.TemporaryDirectory() as directory:
            path = write_json(
                os.path.join(directory, "server.json"),
                {"transport": "stdio", "command": ["python", "server.py"]},
            )
            descriptor = ServerDescriptor.from_file(path)
            self.assertEqual(descriptor.command, ["python", "server.py"])


class DescriptorPathTests(unittest.TestCase):
    def test_relative_paths_point_at_the_descriptor(self):
        import tempfile

        from support import write_json

        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "server.py"), "w", encoding="utf-8") as f:
                f.write("# the server\n")
            path = write_json(
                os.path.join(directory, "server.json"),
                {"transport": "stdio", "command": ["python3", "server.py"]},
            )
            descriptor = ServerDescriptor.from_file(path)
            self.assertEqual(
                descriptor.command,
                ["python3", os.path.join(directory, "server.py")],
            )

    def test_program_names_are_left_alone(self):
        import tempfile

        from support import write_json

        with tempfile.TemporaryDirectory() as directory:
            path = write_json(
                os.path.join(directory, "server.json"),
                {"transport": "stdio", "command": ["python3", "servers/tool.py"]},
            )
            descriptor = ServerDescriptor.from_file(path)
            self.assertEqual(descriptor.command[0], "python3")
            self.assertEqual(
                descriptor.command[1],
                os.path.join(directory, "servers", "tool.py"),
            )

    def test_absolute_paths_are_left_alone(self):
        import tempfile

        from support import write_json

        with tempfile.TemporaryDirectory() as directory:
            path = write_json(
                os.path.join(directory, "server.json"),
                {"transport": "stdio", "command": ["/usr/bin/python3", "server.py"]},
            )
            descriptor = ServerDescriptor.from_file(path)
            self.assertEqual(descriptor.command[0], "/usr/bin/python3")

    def test_the_working_directory_is_resolved_as_well(self):
        import tempfile

        from support import write_json

        with tempfile.TemporaryDirectory() as directory:
            path = write_json(
                os.path.join(directory, "server.json"),
                {"transport": "stdio", "command": ["python3"], "cwd": "work"},
            )
            descriptor = ServerDescriptor.from_file(path)
            self.assertEqual(descriptor.cwd, os.path.join(directory, "work"))


if __name__ == "__main__":
    unittest.main()
