import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import json
import tempfile
import unittest

from mcpassert.errors import UsageError
from mcpassert.snapshot import (
    diff,
    normalize,
    read,
    resolve_paths,
    serialize,
    snapshot_path,
    write,
)

DOCUMENT = {
    "result": {
        "name": "demo",
        "meta": {"timestamp": "2026-01-01T00:00:00Z", "requestId": "abc"},
        "items": [{"id": 1}, {"id": 2}],
    }
}


class NormalizeTests(unittest.TestCase):
    def test_nothing_is_dropped_by_default(self):
        self.assertEqual(normalize(DOCUMENT, []), DOCUMENT)

    def test_ignored_paths_are_dropped(self):
        normalized = normalize(DOCUMENT, ["result.meta.timestamp"])
        self.assertNotIn("timestamp", normalized["result"]["meta"])
        self.assertIn("requestId", normalized["result"]["meta"])

    def test_the_original_document_is_untouched(self):
        normalize(DOCUMENT, ["result.meta.timestamp"])
        self.assertIn("timestamp", DOCUMENT["result"]["meta"])

    def test_several_paths_can_be_ignored(self):
        normalized = normalize(
            DOCUMENT, ["result.meta.timestamp", "result.meta.requestId"]
        )
        self.assertEqual(normalized["result"]["meta"], {})

    def test_unknown_paths_are_ignored(self):
        normalized = normalize(DOCUMENT, ["result.nope", "nope.nope"])
        self.assertEqual(normalized, DOCUMENT)

    def test_resolve_paths_splits_on_commas(self):
        self.assertEqual(resolve_paths("a.b, c.d"), ["a.b", "c.d"])
        self.assertEqual(resolve_paths(""), [])


class SerializeTests(unittest.TestCase):
    def test_serialisation_is_stable_and_sorted(self):
        text = serialize({"b": 1, "a": 2})
        self.assertEqual(text, '{\n  "a": 2,\n  "b": 1\n}\n')

    def test_serialisation_ends_with_a_newline(self):
        self.assertTrue(serialize({}).endswith("\n"))


class SnapshotFileTests(unittest.TestCase):
    def test_snapshot_path_adds_the_extension(self):
        self.assertEqual(str(snapshot_path("demo")), os.path.join("snapshots", "demo.json"))

    def test_snapshot_path_keeps_a_given_extension(self):
        self.assertEqual(
            str(snapshot_path("demo.json")), os.path.join("snapshots", "demo.json")
        )

    def test_snapshot_path_refuses_directories(self):
        with self.assertRaises(UsageError):
            snapshot_path("../escape")

    def test_snapshot_path_refuses_an_empty_name(self):
        with self.assertRaises(UsageError):
            snapshot_path("  ")

    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = snapshot_path("demo", directory)
            write(path, DOCUMENT)
            self.assertEqual(read(path), serialize(DOCUMENT))

    def test_read_of_a_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read(snapshot_path("missing", directory)))


class DiffTests(unittest.TestCase):
    def test_diff_is_empty_for_equal_text(self):
        self.assertEqual(diff("same\n", "same\n", "file"), "")

    def test_diff_shows_the_change(self):
        text = diff("a\nb\n", "a\nc\n", "file")
        self.assertIn("-b", text)
        self.assertIn("+c", text)


if __name__ == "__main__":
    unittest.main()
