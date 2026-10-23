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
* recorded sessions that replay without the server running
* several servers checked by one command

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

The repository also ships a recording of that server and a file naming two
servers, so replaying and checking several at once can be tried the same way:

```console
$ mcp-test call --fixture examples/mock-session.json add --args '{"a": 2, "b": 3}' --expect 'result.sum == 5'
$ mcp-test list --servers examples/servers.json
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

### `mcp-test list [SERVER ...]`

Connects to one or more servers and lists their tools.

| flag | meaning |
| --- | --- |
| `--expect EXPR` | assertion to run against the answer, repeatable |
| `--snapshot NAME` | compare the answer with a golden file |
| `--include-resources` | also list the resources of the server |
| `--include-prompts` | also list the prompts of the server |
| `--json` | print the raw response document instead of a readable list |

### `mcp-test call [SERVER ...] <tool>`

Calls a tool on one or more servers and prints the answer as JSON.

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

Both commands take `--timeout SECONDS`, `--verbose` (trace the JSON-RPC
traffic on stderr) and `--quiet` (only report failures), and the three flags
that decide where the answer comes from:

| flag | meaning |
| --- | --- |
| `--record PATH` | write the exchanges of this run into a fixture file |
| `--append` | with `--record`: add to the file instead of replacing it |
| `--fixture PATH` | replay a recorded session instead of connecting to a server |
| `--servers FILE` | check every server the JSON file lists |

Give `--fixture` and `--servers` before the names, not between them:
`mcp-test call --servers suite.json echo` is right, and so is
`mcp-test call live.json recorded.json echo`, while
`mcp-test call live.json --servers suite.json echo` leaves the tool name
stranded behind the flag.

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

## Recorded sessions

A live server is not always around when the checks have to run: it may need a
token, a database, or thirty seconds of start up. `--record` writes what the
server answered into a JSON file and `--fixture` hands those answers back
later, without starting anything. The answer printed on stdout is left out
below; it is the same document in both runs.

```console
$ mcp-test call examples/stdio-server.json echo --args '{"a": 1}' --record fixtures/echo.json
mcp-test: wrote fixture fixtures/echo.json (2 interaction(s))
$ mcp-test call --fixture fixtures/echo.json echo --args '{"a": 1}' --expect 'result.a == 1'
PASS  result.a == 1
```

The repository ships one of these recordings, `examples/mock-session.json`,
made from the mock server with three commands.

Recording is a normal run with a flag on top, so `list` and `call` keep every
other flag: `--record` a `list` to capture the tools, `--record` a `call` with
`--validate-schema` to capture both. `--append` adds this run's exchanges to a
file that already holds some, which is how a fixture for a whole session is
built up one command at a time.

The file is plain JSON and is meant to be committed:

```json
{
  "format": "mcpassert-fixture",
  "version": 1,
  "transport": "stdio",
  "server": "examples/stdio-server.json",
  "serverInfo": {"name": "mock-stdio-server", "version": "1.0.0"},
  "protocolVersion": "2025-06-18",
  "interactions": [
    {
      "method": "tools/call",
      "params": {"name": "echo", "arguments": {"a": 1}},
      "response": {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
          "content": [{"type": "text", "text": "{\"a\": 1}"}],
          "isError": false,
          "structuredContent": {"a": 1}
        }
      }
    }
  ]
}
```

`interactions` holds the requests worth replaying and what the server answered
to each; everything else a session does is dropped. How a replay is matched
against them:

* A request is answered from the recording made with the same method and the
  same arguments. Asking the fixture for something it never saw is a run that
  could not happen, so it exits with 2 and lists what the file does hold:

  ```console
  $ mcp-test call --fixture fixtures/echo.json echo --args '{"a": 2}'
  mcp-test: error: the fixture fixtures/echo.json holds no answer for call 'echo' with {"a": 2}
  recorded in the fixture:
    initialize
    call 'echo' with {"a": 1}
  ```

* Several answers to the same request are handed out in the order they were
  recorded, which is what a tool that changes between calls looks like. Once
  they are used up, the last one is repeated.
* The `initialize` handshake is matched on its method alone, so a recording
  keeps working when the client or the negotiated protocol version changes.
* `--expect`, `--snapshot` and `--quiet` behave exactly as they do against a
  live server: a recording is just another transport.

A recording holds the arguments of every call it captured, so keep secrets out
of the arguments you record. The descriptor is never written into the file, not
even its `env` and `headers`.

## Several servers in one run

Give more than one server, or name them in a file:

```console
$ mcp-test list examples/stdio-server.json other-server.json
$ mcp-test list --servers examples/servers.json
```

A servers file lists the servers, each of them a path or an object with a
name. A recording can be one of them too, so a live server and its own fixture
can be checked in the same run:

```json
{
  "servers": [
    { "name": "stdio", "descriptor": "stdio-server.json" },
    { "name": "recorded", "fixture": "mock-session.json" }
  ]
}
```

Relative paths point at the servers file, the same way they point at the
descriptor. Every server is checked in turn, its answer goes to stdout as
usual (left out of the transcripts below), and the run ends with a combined
report on stderr:

```console
$ mcp-test call --servers examples/servers.json add --args '{"a": 2, "b": 3}' --expect 'result.sum == 5'
mcp-test: stdio
PASS  result.sum == 5
mcp-test: recorded
PASS  result.sum == 5
mcp-test: summary
  stdio     PASS
  recorded  PASS
mcp-test: all 2 server(s) passed
```

A server that cannot run does not stop the others; it is reported as `ERROR`:

```console
$ mcp-test list examples/stdio-server.json no-such-server.json
mcp-test: examples/stdio-server.json
mcp-test: no-such-server.json
mcp-test: error: server descriptor not found: no-such-server.json
mcp-test: summary
  examples/stdio-server.json  PASS
  no-such-server.json         ERROR
mcp-test: 1 of 2 server(s) failed
```

The exit code is the worst of the run, so a single `ERROR` (2) outranks a check
that did not match (1), which outranks a pass (0). A run with one server reports
exactly as it always did, with no summary and no extra lines.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | every check passed |
| 1 | an assertion or a golden file did not match |
| 2 | the command could not run: broken descriptor, no connection, protocol error, malformed arguments, nothing recorded for this call |

A tool call that answers with a JSON-RPC error or reports `isError` counts as
a failed call, so it exits with 2. Assert on it to make it a passing test:

```console
$ mcp-test call examples/stdio-server.json boom --expect 'error.code == -32000'
```

A run over several servers exits with the worst result of any of them, so one
server that did not match is enough to fail the pipeline.

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

When the server needs credentials the pipeline does not have, record the
answers from a machine that does and commit the fixture. The checks then run
anywhere:

```yaml
      - run: mcp-test call --fixture fixtures/search.json search --args '{"q": "mcp"}' --expect 'result.total != 0'
      - run: mcp-test list --fixture fixtures/tools.json --expect 'result.tools.*.name contains "search"'
```

Several servers can be checked by one step, and the step fails if any of them
does:

```yaml
      - run: mcp-test list --servers ci/servers.json
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
* A recording replays requests and answers. Notifications, server initiated
  requests and streams of progress messages are not kept, and a fixture only
  answers calls whose arguments match what was recorded.
* Servers are checked one after the other; there is no support yet for setup
  and teardown scripts or for servers that talk to each other.
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
