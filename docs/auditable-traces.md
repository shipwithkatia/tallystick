# What a trace must contain for its provenance to be checkable

A provenance audit answers one question: *for each claim in the answer, is there
a path back to something the model did not write?* It can only answer it from
what the recording kept. This page says what that is, in the order it matters.
It is framework-agnostic: nothing here asks you to use tallystick.

`tallystick check-trace run.json` reports how much of a given trace meets this,
with no model and no cost. `--json` writes the same thing for CI.

## The one rule

**Record what entered the run from outside, and record it as it arrived.**

Everything else follows. A provenance chain has to end somewhere that is not the
model's own words; that end point is the only thing in the file the audit does
not have to justify. If what you stored in its place is a model's summary of it,
the chain ends on trust and the audit is theatre.

This is not hypothetical. On 225 real trajectories from six agent frameworks,
61 of the 115 human-labelled hallucinations — 53% — were labelled at a step that
recorded only a tool's reply and nothing the model wrote. Not all of those
replies are the same kind of text. Many are a model's digest of a page the
trace never held: OpenDeepSearch's `web_search`, OpenManus's `browser_use`
extracting content, Magentic-One's `answer_question`. Others are not a digest:
a list of search results with their snippets, the text visible in a browser
window after typing or scrolling, a page as fetched (`visit_webpage`), one
interpreter's printed output. Whatever the reply was, the trace keeps no record
of how the tool made it, and an audit of the file stops there. (Measured; see the
AgentHallu section of the [README](../README.md).)

## What to record

### 1. Artifacts, with an honest kind

Every piece of text the run produced or received, each with an id and one of:

| kind | meaning | the audit's stance |
|---|---|---|
| `document` | text supplied or retrieved, stored verbatim | a root: a chain may end here |
| `tool_result` | what a tool returned, **as it returned it** | a root: a chain may end here |
| `intermediate` | the model's own text — a plan, a summary, a scratchpad, a memory write | derived: must itself be funded |
| `final_answer` | what the user saw | derived: this is what the audit starts from |

**A tool that returns text as fetched is a `document`; a tool that returns another
model's words is a `tool_result`.** A retriever handing back the passage, a file
reader handing back the file, a database handing back the row — those brought in
external text and stored it verbatim, which is exactly what a root should be. A
web search API answering with a digest rather than the page did not. The
distinction is load-bearing, not cosmetic: `check-trace` counts documents on
neither side of its share and tool results against you, so labelling a verbatim
retrieval as a `tool_result` understates your own recording.

The other line that matters is `tool_result` against `intermediate`. A tool that hands
back the agent's own text — a note store, a `final_answer` tool, an interpreter
echoing a literal from the model's code — did not bring anything in from
outside, and recording it as a root launders it into evidence. Record it as
`intermediate`. The same holds for a message: a model's draft pasted back into
the conversation as a `user` turn, or a subagent's answer handed over as one,
is the model's text, not a `document`.

Where a tool's output is genuinely a digest produced by another model — most web
search APIs — you have two honest options: store the pages it digested as
`document` artifacts alongside it, or accept that everything resting on it is
unverifiable and say so. `check-trace` counts those artifacts, and the steps that recorded nothing else.

### 2. Steps, with their real inputs

Each step: an id, what it consumed, what it produced. `inputs` is the whole
point. A claim written at a step may only be credited against something that
step actually had in hand, so a citation to a document the step never received
is not a weak citation — it is impossible, and code can say so without asking a
model. A step that produced model text and declares no inputs can never fund
anything it wrote; `check-trace` reports that as `undeclared_inputs`.

Record the inputs the step really saw, not the ones it should have seen. If your
agent puts the whole history in every prompt, then the whole history is the
input, and that is the honest record.

That record has a cost for a long chat, and it is better known than met. Every
step lists every artifact before it, so the trace grows with the square of the
turns. The text is stored once; the ids repeat. How fast depends on how many
artifacts a turn records, so `python bench/trace_growth.py` measures two chats
with the OpenAI reader, both sides as `tallystick convert` writes JSON. When
each turn carries a line of the assistant's text, one tool call and the reply:
with replies of 40 characters the trace reaches ten times the log at 127 turns,
and 2,000 turns turn a 0.9 MB log into a 127 MB trace; with replies of 2,000
characters the crossing is at 677 turns, and 2,000 turns give 4.6 MB of log and
131 MB of trace. When a turn carries the tool call and no text, the growth is
slower: ten times at 516 turns with 40-character replies (0.8 MB of log and
32 MB of trace at 2,000 turns), and not within 2,000 turns with 2,000-character
replies (8 times, 4.6 MB and 36 MB). None of AgentHallu's 693 trajectories
comes near it: the largest trace there is 2.3 times its log
(`python bench/trace_growth.py --corpus <AgentHallu>`; the corpus is not in
this repository — `git clone https://github.com/liuxuannan/AgentHallu`). The
list stays explicit anyway. A shorthand for "everything the
step before had" would be read by an older version of the core without an
error, and give a wrong verdict in silence. So `tallystick convert` and
`check-trace` say it instead - how big, how many steps, and why - once a trace
is ten times the log it was read from (`TRACE_SIZE_NOTE_RATIO` in
`tallystick/convert.py`). That tenfold is measured as tallystick writes JSON on
both sides, so the log's own formatting cannot move it, while the line itself
gives the two files as they lie on disk - where a log stored compactly shows a
larger ratio than the one that decided. It changes no exit code.

### 3. The answer, marked as the answer

Exactly one artifact of kind `final_answer`. Without it there is nothing to
audit back from and `check-trace` reports `unauditable`; with more than one it
reports `multiple_final_answers`, because the audit cannot tell which one the
user saw.

### 4. Know where the same text appears twice

If the final answer repeats the last step word for word — which real agents do
constantly — a quote can no longer
be attributed to one rather than the other, and any tooling matching artifacts
*by their text* will confuse them. That is not theory: it is how one of this
project's own runs was withdrawn (see `bench/HISTORY.md`, v0.7.1).

Keep both artifacts. `check-trace` reports this as a **note**, not a defect: it
does not change the verdict and does not fail a build, because the fix is a
change to the agent, not to the recording.

### 5. Every root consumed by the step that used it

Recording a document is not enough; the step that read it has to declare it as
an input. If your prompt reformats or truncates a document, an automatic
recorder may fail to match it and drop it — and then every claim resting on it
is reported unfunded, with nothing saying why. `check-trace` reports a root that
no step takes in as `orphan_root`, which is the cheapest bug in this list to fix
and the most expensive to miss.

### 6. Cuts, marked as cuts

If you truncate a large tool result to fit a prompt, record which artifacts were
cut (`_meta.truncated`). A quote into the missing tail cannot be found, and the
audit should say "not recorded", not "not supported".

## What the verdicts mean

| verdict | exit | what it licenses you to conclude |
|---|---|---|
| `AUDITABLE` | 0 | nothing in the recording stands in the audit's way; a clean audit of this trace means something |
| `PARTIAL` | 1 | the audit will run, but read its silence as "nothing found here", not "nothing there" |
| `UNAUDITABLE` | 1 | no answer to work back from, or nothing the model wrote: there is no question to ask |
| — | 2 | the file could not be read at all — including a `_meta` block that is not an object, or holds a field of the wrong type. Never confuse this with a verdict |

The JSON report (`--json`) has these fields. `verdict` is the word above and
`min_reachable` the line you asked for, or `null`. `reachable_share` is the
share, `judged_artifacts` what it is taken over. `artifacts`, `documents`,
`tool_results` and `derived` count the artifacts by kind (the last three sum to
the first); `empty_derived` and `empty_tool_results` are how many of those held
no content, and they are excluded from the share on both sides. `root_chars` is
how much root text the audit takes on trust and `tool_result_chars` how much of
that came from tools. `steps` is the step count, `reachable_steps` those that
recorded model text, `opaque_steps` those whose outputs are all tool results,
`ingest_steps` those whose outputs are all documents, `silent_steps` those that
produced nothing. `findings` decide the verdict; `notes` do not.

## The shape

```json
{
  "artifacts": [
    {"artifact_id": "doc_filing", "kind": "document",     "content": "...", "title": "10-K 2024"},
    {"artifact_id": "summary",    "kind": "intermediate", "content": "..."},
    {"artifact_id": "answer",     "kind": "final_answer", "content": "..."}
  ],
  "steps": [
    {"step_id": "s1", "kind": "retrieve",  "inputs": [],          "outputs": ["doc_filing"]},
    {"step_id": "s2", "kind": "summarize", "inputs": ["doc_filing"], "outputs": ["summary"]},
    {"step_id": "s3", "kind": "answer",    "inputs": ["summary"],    "outputs": ["answer"]}
  ],
  "_meta": {"truncated": []}
}
```

That is the whole format. `tallystick/adapters/` converts other shapes into it;
`examples/` has worked files.

## What this does not ask for

No hashes, no signatures, no immutability, no schema registry. Those matter for
tamper-evidence, which is a different property from provenance: a signed log of
a digest is a tamper-evident record of something unverifiable. Get the content
right first.

It also asks for nothing semantic. Whether a claim is *true*, whether a source
*supports* it — neither is decided here, and a trace that satisfies this page
tells you only that the question can be asked.

## Checking a trace

```
$ tallystick check-trace run.json
$ tallystick check-trace run.json --json report.json      # for CI
$ tallystick check-trace run.json --min-reachable         # also fail a thin recording
```

Exit 0 when no defect stands in the audit's way, 1 when one does (or the
recording is thinner than `--min-reachable`), 2 when the file cannot be read at
all. The share — of the artifacts a chain passes through, how many hold the
model's own words rather than a tool's output — is reported either way; by
default it is context, not a verdict, because a run that legitimately leans on
tools is not a broken run, it is a run whose clean audit means less.

On all 443 labelled AgentHallu trajectories, traces at or above 80% held 24 of
their 84 labelled hallucinations beyond the audit's reach (29%) and traces below
it 212 of 359 (59%) — `bench/results/auditability-agenthallu.txt`, reproducible
with `python bench/auditability_agenthallu.py --data <AgentHallu>`. The corpus is
not in this repository: `git clone https://github.com/liuxuannan/AgentHallu`, and
`<AgentHallu>` is the folder inside the clone, `AgentHallu/AgentHallu`, which holds
one folder per framework. Given any other folder the script finds nothing, says so
and exits 2.

Three caveats, and they matter more than the number. The line is read off that
corpus rather than held out. Most of the gap is arithmetic rather than
prediction: replacing the human label with a step drawn at random from the same
trajectory reproduces the ordering in every one of the five draws — 13% above
the line against 46% below on average, 7–15% against 44–47% across draws —
because a trace with more tool-only steps makes *any* step more likely to be
tool-only. And the significance test the script prints
rejects for the placebo too, so it is printed beside it rather than quoted
alone. Treat the share as a fact about a recording, not as a predictor of where
a hallucination is.
