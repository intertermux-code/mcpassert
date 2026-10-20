import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import unittest

from mcpassert.jsonpath import MISSING, resolve

DOCUMENT = {
    "result": {
        "name": "demo",
        "count": 3,
        "ratio": 1.5,
        "empty": None,
        "items": [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}],
        "nested": {"deep": {"value": "found"}},
        "0": "zero key",
    }
}


class PathTests(unittest.TestCase):
    def test_simple_lookup(self):
        self.assertEqual(resolve(DOCUMENT, "result.name"), "demo")

    def test_nested_lookup(self):
        self.assertEqual(resolve(DOCUMENT, "result.nested.deep.value"), "found")

    def test_list_index_with_a_dot(self):
        self.assertEqual(resolve(DOCUMENT, "result.items.0.label"), "first")

    def test_list_index_with_brackets(self):
        self.assertEqual(resolve(DOCUMENT, "result.items[1].label"), "second")

    def test_projection_over_a_list(self):
        self.assertEqual(
            resolve(DOCUMENT, "result.items.*.label"), ["first", "second"]
        )

    def test_projection_over_a_dictionary(self):
        self.assertEqual(
            resolve(DOCUMENT, "result.nested.*"), [{"value": "found"}]
        )

    def test_projection_over_a_dictionary_with_a_field(self):
        self.assertEqual(resolve(DOCUMENT, "result.nested.*.value"), ["found"])

    def test_missing_path(self):
        self.assertIs(resolve(DOCUMENT, "result.nope"), MISSING)
        self.assertIs(resolve(DOCUMENT, "result.items.9"), MISSING)
        self.assertIs(resolve(DOCUMENT, "result.name.deeper"), MISSING)

    def test_null_value_is_present(self):
        self.assertIsNone(resolve(DOCUMENT, "result.empty"))

    def test_numeric_dictionary_key(self):
        self.assertEqual(resolve(DOCUMENT, "result.0"), "zero key")

    def test_booleans_and_numbers(self):
        self.assertEqual(resolve(DOCUMENT, "result.count"), 3)
        self.assertEqual(resolve(DOCUMENT, "result.ratio"), 1.5)

    def test_structured_content_is_reachable_from_the_result(self):
        document = {"result": {"structuredContent": {"total": 12}}}
        self.assertEqual(resolve(document, "result.total"), 12)
        self.assertEqual(resolve(document, "result.structuredContent.total"), 12)


if __name__ == "__main__":
    unittest.main()
