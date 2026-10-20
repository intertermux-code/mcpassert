import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

import unittest

from mcpassert.errors import UsageError
from mcpassert.expectations import evaluate, parse_expectation, parse_expectations

DOCUMENT = {
    "result": {
        "name": "demo",
        "count": 3,
        "ratio": 1.5,
        "empty": None,
        "flag": True,
        "items": [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}],
        "tags": ["alpha", "beta"],
        "text": "hello world",
        "mapping": {"key": "value"},
    }
}


def check(expression, document=None):
    return evaluate(parse_expectation(expression), document or DOCUMENT)


class ParsingTests(unittest.TestCase):
    def test_empty_expression_is_rejected(self):
        with self.assertRaises(UsageError):
            parse_expectation("   ")

    def test_expression_without_operator_is_rejected(self):
        with self.assertRaises(UsageError):
            parse_expectation("result.name")

    def test_unknown_operator_is_rejected(self):
        with self.assertRaises(UsageError):
            parse_expectation("result.name ~ 3")

    def test_values_may_contain_spaces(self):
        expectation = parse_expectation('result.text == "hello world"')
        self.assertEqual(expectation.value, "hello world")

    def test_bare_words_are_strings(self):
        expectation = parse_expectation("result.name == demo")
        self.assertEqual(expectation.value, "demo")

    def test_json_values_are_parsed(self):
        self.assertEqual(parse_expectation("result.count == 3").value, 3)
        self.assertIs(parse_expectation("result.flag == true").value, True)
        self.assertIsNone(parse_expectation("result.empty == null").value)

    def test_parse_expectations_keeps_the_order(self):
        expectations = parse_expectations(["result.a == 1", "result.b == 2"])
        self.assertEqual(
            [item.path for item in expectations], ["result.a", "result.b"]
        )


class EqualityTests(unittest.TestCase):
    def test_equal_value_passes(self):
        self.assertTrue(check("result.count == 3").passed)

    def test_wrong_value_fails(self):
        outcome = check("result.count == 4")
        self.assertFalse(outcome.passed)
        self.assertEqual(outcome.actual, 3)

    def test_integers_and_floats_compare_by_value(self):
        self.assertTrue(check("result.ratio == 1.5").passed)
        self.assertTrue(check("result.count == 3.0").passed)

    def test_strings_and_numbers_do_not_mix(self):
        self.assertFalse(check('result.count == "3"').passed)

    def test_booleans_are_not_numbers(self):
        self.assertFalse(check("result.flag == 1").passed)
        self.assertTrue(check("result.flag == true").passed)

    def test_null_compares_to_null(self):
        self.assertTrue(check("result.empty == null").passed)

    def test_not_equal(self):
        self.assertTrue(check("result.count != 4").passed)
        self.assertFalse(check("result.count != 3").passed)

    def test_not_equal_on_a_missing_path_passes(self):
        self.assertTrue(check("result.missing != 1").passed)

    def test_equal_on_a_missing_path_fails(self):
        outcome = check("result.missing == 1")
        self.assertFalse(outcome.passed)
        self.assertFalse(outcome.present)

    def test_wildcard_matches_any_present_value(self):
        self.assertTrue(check("result.name == *").passed)
        self.assertTrue(check("result.count == *").passed)

    def test_wildcard_does_not_match_null(self):
        self.assertFalse(check("result.empty == *").passed)

    def test_wildcard_does_not_match_a_missing_path(self):
        self.assertFalse(check("result.missing == *").passed)

    def test_not_wildcard_matches_null_or_missing(self):
        self.assertTrue(check("result.empty != *").passed)
        self.assertTrue(check("result.missing != *").passed)
        self.assertFalse(check("result.name != *").passed)

    def test_deep_equality(self):
        self.assertTrue(check('result.items == [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}]').passed)
        self.assertFalse(check('result.items == [{"id": 1}]').passed)


class ContainsTests(unittest.TestCase):
    def test_contains_in_a_list(self):
        self.assertTrue(check('result.tags contains "alpha"').passed)
        self.assertFalse(check('result.tags contains "gamma"').passed)

    def test_contains_matches_objects_in_a_list(self):
        self.assertTrue(check('result.items contains {"id": 2, "label": "second"}').passed)
        self.assertFalse(check('result.items contains {"id": 9}').passed)

    def test_contains_in_a_string(self):
        self.assertTrue(check('result.text contains "world"').passed)
        self.assertFalse(check('result.text contains "nope"').passed)

    def test_contains_in_an_object(self):
        self.assertTrue(check('result.mapping contains "value"').passed)
        self.assertFalse(check('result.mapping contains "other"').passed)

    def test_contains_wildcard_needs_a_container(self):
        self.assertTrue(check("result.tags contains *").passed)
        self.assertTrue(check("result.text contains *").passed)
        self.assertTrue(check("result.mapping contains *").passed)
        self.assertFalse(check("result.count contains *").passed)

    def test_contains_on_a_missing_path_fails(self):
        self.assertFalse(check('result.missing contains "x"').passed)


class ExistsTests(unittest.TestCase):
    def test_existing_path(self):
        self.assertTrue(check("result.name exists").passed)

    def test_missing_path(self):
        self.assertFalse(check("error exists").passed)
        self.assertFalse(check("result.nope exists").passed)

    def test_exists_with_an_explicit_boolean(self):
        self.assertFalse(check("error exists true").passed)
        self.assertTrue(check("result.name exists true").passed)
        self.assertTrue(check("error exists false").passed)


class ReportTests(unittest.TestCase):
    def test_report_shows_expected_and_actual(self):
        outcome = check("result.count == 4")
        report = outcome.report()
        self.assertIn("result.count == 4", report)
        self.assertIn("expected: 4", report)
        self.assertIn("actual:   3", report)

    def test_report_marks_a_missing_value(self):
        report = check("result.nope == 1").report()
        self.assertIn("<missing>", report)


if __name__ == "__main__":
    unittest.main()
