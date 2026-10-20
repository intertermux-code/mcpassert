"""Error types and the exit codes they map onto.

Every exception carries the exit status the CLI should return, so the command
layer never has to guess how a failure maps onto CI semantics:

* ``0`` - the command ran and every check passed.
* ``1`` - a check ran but did not match (assertion or snapshot).
* ``2`` - the command could not run (bad descriptor, no connection, protocol
  error, malformed input).
"""

EXIT_SUCCESS = 0
EXIT_ASSERTION = 1
EXIT_SETUP = 2


class McpTestError(Exception):
    """Base class for every error reported to the user."""

    exit_code = EXIT_SETUP

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self):
        return self.message


class DescriptorError(McpTestError):
    """The server descriptor is missing something or holds the wrong type."""


class TransportError(McpTestError):
    """The server could not be reached, or died while we were talking to it."""


class ProtocolError(McpTestError):
    """The server answered with something that is not valid MCP."""


class UsageError(McpTestError):
    """The command line itself does not make sense."""


class CheckFailed(McpTestError):
    """An assertion or a snapshot comparison did not match."""

    exit_code = EXIT_ASSERTION
