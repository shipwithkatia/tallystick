# Six test logs, and what the tool should say about each

These are made-up test logs, not anyone's data. They are the thing to try
first, before pointing `check-trace` at a log of your own. Each one is a shape you will meet, and each
has a committed expected output in `examples/expected/`, so you can tell
whether what you saw is what the tool meant to say.

Run them from the repository root:

```
tallystick check-trace examples/logs/clean_run.json
```

| file | what it is | verdict | exit |
|---|---|---|---|
| `clean_run.json` | an agent that read a file and answered from it | `CAN BE CHECKED`, 66% | 0 |
| `laundered_search.json` | a web search answered with its own digest, and the agent cited the note it wrote itself | `CAN BE CHECKED`, 60% | 0 |
| `ambiguous_tools.json` | two tools called at once, results with no id and no name; and the log stops on a tool's reply, so it never says what the user saw | `CANNOT BE CHECKED`, 33% | 1 |
| `otel_spans.json` | an OpenTelemetry GenAI span export | `CAN BE CHECKED`, 100% | 0 |
| `not_an_agent_log.json` | not an agent log at all | refused | 2 |
| `langgraph_export.json` | someone else's log that happens to carry a `steps` key | refused, with the reader that would read it | 2 |

**The exit code is the part to build on.** `0` — nothing in the recording
stands in the audit's way. `1` — the audit will run, but read its silence as
"nothing found here", not "nothing there". `2` — the file could not be read;
never confuse this with a verdict about your agent.

## The two flags, on these files

`laundered_search.json` reports 60% until you say what only you know: that
`save_note` hands the agent's own words back rather than bringing anything in
from outside.

```
tallystick check-trace examples/logs/laundered_search.json \
  --tool-returns-model-text save_note
```

That moves the note out of the evidence column and the share to 80%, and the
line `tools read as the model's own text: save_note` is printed above the
report so the number is never quoted without the assumption behind it.

The opposite flag says a tool returns external text exactly as fetched:

```
tallystick check-trace examples/logs/clean_run.json \
  --tool-returns-verbatim read_file
```

Neither is guessed from the file. A message list records that a tool returned
text; nothing in it says whether that text came from the world or from the
model, and inventing an answer to that question is the one mistake this tool
exists to catch.

## Checking that your copy agrees

```
tallystick check-trace examples/logs/clean_run.json > /tmp/got.txt
diff /tmp/got.txt examples/expected/clean_run.txt
```

No output from `diff` means your copy says exactly what this repository's does.
`tests/test_examples.py` runs that comparison for all six on every commit, so
the expected files cannot quietly drift away from the code.
