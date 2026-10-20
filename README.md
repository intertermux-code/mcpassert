# mcp-test

A terminal and CI harness for [Model Context Protocol](https://modelcontextprotocol.io)
servers.

The official Inspector is a browser GUI: you click a tool, look at the answer
and judge it with your eyes. That does not work in a pipeline. `mcp-test` is
the scriptable counterpart: it starts a server, calls it and checks the
answer with small expressions, then exits with a status code your CI can act
on.

```console
$ mcp-test call server.json echo --args '{"a": 1}' --expect 'result.a == 1'
PASS  result.a == 1
```

* no dependencies beyond the Python standard library
* stdio and streamable HTTP transports
* a small assertion language instead of a test framework
* golden files for whole responses

## Install

```console
$ pip install mcpassert
```

The command is called `mcp-test`; `mcpassert` is installed as an alias. The
package is called `mcpassert` because `mcp-test` is already taken on PyPI.

Python 3.9 or newer is required. Schema validation is optional and needs one
extra package:

```console
$ pip install 'mcpassert[schema]'
```

## Quick start

The repository ships a mock MCP server, so you can try everything without
writing a server of your own.

```console
$ mcp-test list examples/stdio-server.json
tools (6):
  echo   Return the arguments unchanged
  add    Add two numbers
  fail   Fail with a tool level error
  boom   Answer with a JSON-RPC error
  sleep  Wait for a while before answering
  stamp  Return a value that changes on every call
```

Call a tool and check the answer:

```console
$ mcp-test call examples/stdio-server.json echo --args '{"a": 1}' --expect 'result.a == 1'
{
  "result": {
    "content": [
      {
        "text": "{\"a\": 1}",
        "type": "text"
      }
    ],
    "isError": false,
    "structuredContent": {
      "a": 1
    }
  }
}
PASS  result.a == 1
```

The answer goes to stdout and the report to stderr, so the two can show up in
either order once the output is piped. `mcp-test call ... | jq` keeps working
either way.

A failing check exits with status 1 and shows what was expected and what
arrived:

```console
$ mcp-test call examples/stdio-server.json echo --args '{"a": 1}' --expect 'result.a == 2'
FAILED: result.a == 2
  path:     result.a
  expected: 2
  actual:   1
--- expected
+++ actual
@@ -1 +1 @@
-2
+1

mcp-test: 1 check(s) failed
```

## The server descriptor

A descriptor is a JSON file that says how to reach a server.

```json
{
  "transport": "stdio",
  "command": ["python", "my_server.py"],
  "args": ["--verbose"],
  "env": {"LOG_LEVEL": "debug"},
  "cwd": "/srv/my-server",
  "timeout": 30
}
```

```json
{
  "transport": "http",
  "url": "http://localhost:8000/mcp",
  "headers": {"Authorization": "Bearer ${TOKEN}"},
  "timeout": 30
}
```

| field | meaning |
| --- | --- |
| `transport` | `stdio` or `http`. `type` is accepted as an alias. |
| `command` | stdio only: the program to run, as a list of strings. A plain string is split like a shell would split it. |
| `args` | stdio only: extra arguments for `command`. |
| `env` | stdio only: environment variables for the server. They are added to the current environment; set `inheritEnv` to `false` to start from an empty one. |
| `cwd` | stdio only: working directory of the server. |
| `url` | http only: the endpoint that receives the JSON-RPC messages. |
| `headers` | http only: extra headers for every request, for example an `Authorization` header. |
| `timeout` | how long to wait for an answer, in seconds. Defaults to 30. |
| `inheritEnv` | stdio only: whether the server inherits the current environment. Defaults to `true`. |

Values in `env` and `headers` may refer to the environment with `${NAME}`, so
a token does not have to be written down in a file that gets committed. A
variable that is not set is left as it is.

Unknown fields are ignored, so a descriptor written for another MCP client
usually works as it is. Missing or malformed required fields are reported
with the name of the field.

Relative paths inside a descriptor point at the directory of the descriptor,
not at the directory you happen to be in. A descriptor that sits next to the
server it starts therefore keeps working from anywhere, while a program name
such as `python3` is still looked up in `PATH`.

## Commands

### `mcp-test list <server.json>`

Connects to the server and lists its tools.

| flag | meaning |
| --- | --- |
| `--expect EXPR` | assertion to run against the answer, repeatable |
| `--snapshot NAME` | compare the answer with a golden file |
| `--include-resources` | also list the resources of the server |
| `--include-prompts` | also list the prompts of the server |
| `--json` | print the raw response document instead of a readable list |

### `mcp-test call <server.json> <tool>`

Calls a tool and prints the answer as JSON.

| flag | meaning |
| --- | --- |
| `--args JSON` | arguments of the call, a JSON object. Defaults to `{}`. |
| `--expect EXPR` | assertion to run against the answer, repeatable |
| `--snapshot NAME` | compare the answer with a golden file |
| `--update` | rewrite the golden file instead of comparing against it |
| `--ignore-paths PATHS` | comma separated paths to drop before comparing |
| `--snapshot-dir DIR` | where the golden files live. Defaults to `snapshots`. |
| `--validate-schema` | check the arguments against the `inputSchema` of the tool |
| `--validate-output` | check the output against the `outputSchema` of the tool |

Both commands also accept `--timeout SECONDS`, `--verbose` (trace the JSON-RPC
traffic on stderr) and `--quiet` (only report failures).

## The assertion language

An expression is a path, an operator and a value:

```
result.name == *
result.items contains "needle"
result.total != 0
error exists
```

Several `--expect` flags can be given; all of them have to pass.

### Paths

| path | meaning |
| --- | --- |
| `result.name` | a field of the answer |
| `result.items.0` | an entry of a list, also written `result.items[0]` |
| `result.tools.*.name` | the `name` of every entry of the list, as a list of names |
| `error.code` | the code of a JSON-RPC error |

The document that is checked is the same one that is printed and stored in
the golden files. For a tool call it looks like this:

```json
{
  "result": {
    "content": [{"type": "text", "text": "..."}],
    "isError": false,
    "structuredContent": {"total": 12}
  }
}
```

The fields of `structuredContent` are also reachable directly under `result`,
so `result.total` and `result.structuredContent.total` find the same value.

### Operators

| operator | true when |
| --- | --- |
| `==` | the value is exactly the expected one. `*` matches any value that is there and not null. |
| `!=` | the value is anything else. `!= *` matches a missing or null value. |
| `contains` | a list holds the expected value, a string contains it, or an object has it as one of its values. `contains *` matches a non-empty list, string or object. |
| `exists` | the path is there. `exists false` asserts the opposite. |

Values are JSON, so `1`, `1.0`, `"text"`, `true` and `null` all work. A value
that is not valid JSON is compared as a plain string, which keeps
`result.name == demo` working. Comparison is type strict: the string `"1"`
never equals the number `1`.

Some examples:

```console
$ mcp-test call examples/stdio-server.json echo --args '{"a": 1, "tags": ["alpha"]}' \
    --expect 'result.a == 1' \
    --expect 'result.tags contains "alpha"' \
    --expect 'result.missing != 1' \
    --expect 'error exists false'
```

## Golden files

`--snapshot NAME` stores the answer in `snapshots/NAME.json`. The file is
meant to be committed, so a change in the output of the server shows up as a
reviewable diff.

* The first run records the file.
* Every later run compares against it and fails with a unified diff when
  something changed.
* `--update` rewrites the file.
* `--ignore-paths a.b,c.d` drops the given paths before comparing, for
  values that change on every call (timestamps, request ids, ...).

```console
$ mcp-test call examples/stdio-server.json stamp --snapshot stamp --ignore-paths result.structuredContent.now
$ mcp-test call examples/stdio-server.json stamp --snapshot stamp --ignore-paths result.structuredContent.now
```

## Exit codes

| code | meaning |
| --- | --- |
| 0 | every check passed |
| 1 | an assertion or a golden file did not match |
| 2 | the command could not run: broken descriptor, no connection, protocol error, malformed arguments |

A tool call that answers with a JSON-RPC error or reports `isError` counts as
a failed call, so it exits with 2. Assert on it to make it a passing test:

```console
$ mcp-test call examples/stdio-server.json boom --expect 'error.code == -32000'
```

## Use in CI

```yaml
name: mcp
on: [push, pull_request]

jobs:
  mcp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install mcpassert
      - run: mcp-test call server.json echo --args '{"a": 1}' --expect 'result.a == 1'
      - run: mcp-test list server.json --expect 'result.tools.*.name contains "search"'
```

## Limitations

* The old SSE transport, where the server opens an event stream on `GET` and
  expects the requests on a second URL, is not supported. Servers that speak
  the streamable HTTP transport are, including servers that answer a `POST`
  with an event stream.
* Schema validation is optional and only covers the JSON Schema of the
  arguments and of the structured output.
* There is no support for OAuth, for server initiated requests such as
  `roots/list` or `sampling/createMessage`, or for elicitation. Requests
  from the server are answered with "method not found".
* One server per run. There is no support yet for fixtures, setup and teardown
  scripts, or for several servers talking to each other.
* The transport speaks one version of the protocol, `2025-06-18`.

## Development

```console
$ git clone https://github.com/intertermux-code/mcpassert.git
$ cd mcpassert
$ python3 -m unittest discover -s tests
```

The suite runs against the mock servers in `tests/`, one speaking stdio and
one speaking streamable HTTP, so it needs nothing but a Python interpreter.

## License

MIT, see [LICENSE](LICENSE).
