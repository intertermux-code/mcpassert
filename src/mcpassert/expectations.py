"""The small assertion language used by ``--expect``.

An expectation is a single line made of a path, an operator and a value::

    result.name == *
    result.items contains "needle"
    result.total != 0
    error exists

Operators:

``==``
    Exact equality. ``*`` is a wildcard and matches any value that is present
    and not null.
``!=``
    Anything but exact equality. ``!= *`` matches a missing or null value.
``contains``
    Membership: the value at the path must be a list, a string or an object
    that contains the expected value. ``contains *`` matches a non-empty
    container.
``exists``
    The path must be present. ``exists false`` asserts the opposite.

Values are JSON, so ``1``, ``1.0``, ``"text"``, ``true`` and ``null`` all
work. A value that is not valid JSON is compared as a plain string, which
keeps ``result.name == hello`` working. Comparison is type strict: the string
``"1"`` never equals the number ``1``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from .errors import UsageError
from .jsonpath import MISSING, resolve

OPERATORS = ("==", "!=", "contains", "exists")

WILDCARD = object()

_EXPRESSION_RE = re.compile(
    r"^\s*(?P<path>[^\s=!]+)\s*"
    r"(?P<operator>==|!=|contains|exists)\s*"
    r"(?P<value>.*)$",
    re.DOTALL,
)


@dataclass
class Expectation:
    """One parsed ``--expect`` expression."""

    source: str
    path: str
    operator: str
    value: Any = None
    has_value: bool = False

    def describe(self) -> str:
        if self.operator == "exists" and not self.has_value:
            return "{0} exists".format(self.path)
        return "{0} {1} {2}".format(self.path, self.operator, format_value(self.value))


@dataclass
class Outcome:
    """The result of evaluating one expectation."""

    expectation: Expectation
    passed: bool
    present: bool
    actual: Any = MISSING

    @property
    def actual_display(self) -> str:
        if not self.present:
            return "<missing>"
        return format_value(self.actual)

    @property
    def expected_display(self) -> str:
        expectation = self.expectation
        if expectation.operator == "exists":
            if not expectation.has_value:
                return "present"
            return "present" if expectation.value else "absent"
        return format_value(expectation.value)

    def report(self) -> str:
        lines = [
            "FAILED: {0}".format(self.expectation.describe()),
            "  path:     {0}".format(self.expectation.path),
            "  expected: {0}".format(self.expected_display),
            "  actual:   {0}".format(self.actual_display),
        ]
        return "\n".join(lines)


def parse_expectation(text: str) -> Expectation:
    """Parse one ``--expect`` expression."""
    if text is None or not text.strip():
        raise UsageError("an --expect expression must not be empty")
    match = _EXPRESSION_RE.match(text)
    if not match:
        raise UsageError(
            "cannot parse expectation {0!r} (expected '<path> <operator> <value>' "
            "with operator one of: {1})".format(text, ", ".join(OPERATORS))
        )
    path = match.group("path")
    operator = match.group("operator")
    value, has_value = _parse_value(match.group("value"))
    return Expectation(
        source=text,
        path=path,
        operator=operator,
        value=value,
        has_value=has_value,
    )


def _parse_value(raw: str):
    text = raw.strip()
    if not text:
        return None, False
    if text == "*":
        return WILDCARD, True
    try:
        return json.loads(text), True
    except ValueError:
        return text, True


def evaluate(expectation: Expectation, document: Any) -> Outcome:
    """Evaluate one expectation against a response document."""
    value = resolve(document, expectation.path)
    present = value is not MISSING
    operator = expectation.operator
    expected = expectation.value

    if operator == "exists":
        wanted = True if not expectation.has_value else bool(expected)
        passed = present if wanted else not present
    elif operator == "==":
        if expected is WILDCARD:
            passed = present and value is not None
        else:
            passed = present and json_equal(value, expected)
    elif operator == "!=":
        if expected is WILDCARD:
            passed = (not present) or value is None
        else:
            passed = not (present and json_equal(value, expected))
    elif operator == "contains":
        passed = present and _contains(value, expected)
    else:  # pragma: no cover - guarded by the parser
        raise UsageError("unknown operator {0!r}".format(operator))

    return Outcome(
        expectation=expectation,
        passed=passed,
        present=present,
        actual=value,
    )


def evaluate_all(expectations: List[Expectation], document: Any) -> List[Outcome]:
    """Evaluate every expectation, in the order they were given."""
    return [evaluate(expectation, document) for expectation in expectations]


def json_equal(left: Any, right: Any) -> bool:
    """Compare two JSON values without the usual Python type surprises."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(json_equal(left[key], right[key]) for key in left)
    return False


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, list):
        if expected is WILDCARD:
            return bool(value)
        return any(json_equal(item, expected) for item in value)
    if isinstance(value, str):
        if expected is WILDCARD:
            return bool(value)
        return isinstance(expected, str) and expected in value
    if isinstance(value, dict):
        if expected is WILDCARD:
            return bool(value)
        return any(json_equal(item, expected) for item in value.values())
    return False


def format_value(value: Any) -> str:
    """Render a JSON value the way it is shown in reports."""
    if value is WILDCARD:
        return "*"
    if value is MISSING:
        return "<missing>"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_expectations(texts: Optional[List[str]]) -> List[Expectation]:
    """Parse a list of ``--expect`` values."""
    return [parse_expectation(text) for text in (texts or [])]
