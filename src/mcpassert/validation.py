"""Optional JSON Schema validation.

Schema validation is a convenience, never a requirement: the tool works
without any third party package installed. When ``jsonschema`` is available
the arguments of a tool call can be checked against the ``inputSchema`` the
server advertises, and structured output against ``outputSchema``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import CheckFailed, UsageError

MISSING_MODULE_HINT = (
    "schema validation needs the 'jsonschema' package; install it with "
    "'pip install mcpassert[schema]'"
)


def is_available() -> bool:
    """Whether the optional dependency is importable."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


def _validator(schema: Any):
    try:
        import jsonschema
    except ImportError:
        raise UsageError(MISSING_MODULE_HINT)

    if not isinstance(schema, dict):
        raise UsageError("the server did not provide a usable JSON Schema")
    try:
        return jsonschema.Draft7Validator(schema)
    except jsonschema.exceptions.SchemaError as exc:
        raise UsageError("the server provided an invalid JSON Schema: {0}".format(exc))


def _describe(error) -> str:
    location = "/".join(str(part) for part in error.absolute_path)
    where = location or "<root>"
    return "{0}: {1}".format(where, error.message)


def validate(schema: Optional[Dict[str, Any]], value: Any, what: str) -> None:
    """Validate ``value`` against ``schema``; raises :class:`CheckFailed`."""
    if not schema:
        raise UsageError(
            "the server did not advertise a {0} schema, nothing to validate".format(what)
        )
    validator = _validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        details = "\n".join("  " + _describe(error) for error in errors)
        raise CheckFailed(
            "{0} does not match the schema the server advertised:\n{1}".format(
                what, details
            )
        )


def validate_arguments(schema: Optional[Dict[str, Any]], arguments: Any) -> None:
    """Validate the arguments of a tool call."""
    validate(schema, arguments, "the tool arguments")


def validate_output(schema: Optional[Dict[str, Any]], output: Any) -> None:
    """Validate the structured output of a tool call."""
    validate(schema, output, "the tool output")
