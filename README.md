# tallystick

[![ci](https://github.com/shipwithkatia/tallystick/actions/workflows/ci.yml/badge.svg)](https://github.com/shipwithkatia/tallystick/actions/workflows/ci.yml)

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

## TL;DR

- **Problem.** A multi-step agent can invent a fact in an intermediate step and quote it faithfully in the answer; every one-hop check then says "grounded".
- **What it does.** tallystick walks each claim in the answer back to the documents with plain code — no model in the verdict — and names the step where the chain breaks.
- **Result.** On 290 constructed answer sentences it is level with an LLM judge shown the full history: F1 0.59 against 0.58, with a false flag on 7% of clean sentences against 16% (measured on v0.6; the audit side re-checked on v0.9.0, unchanged).
- **Main finding.** In 443 human-labelled real agent runs ([AgentHallu](https://arxiv.org/abs/2601.06818)), 53% of the hallucinations sit at a step where the agent wrote no prose, only a tool call — beyond this audit's reach (32% if a tool call's own text counts as checkable).
- **Limit.** On 225 real trajectories, where the hallucination is in the agent's own text, it flags the answer in 57% of runs but names the labelled step in 15% — and it flags 35% of clean runs.
- **Details:** [benchmark](docs/benchmark.md) · [design, decisions and lessons](docs/design.md)

![One hop is not enough: the answer quotes the summary, the summary invented a sentence, and the chain breaks at the summarise step](docs/chain.svg)

**Status:** research prototype, v0.9.0. Interfaces may change between versions. The numbers above were measured on v0.6 (the constructed benchmark) and v0.7.3 (the real trajectories); the log readers that arrived in v0.7.5 are not in them. The model sides — the proposer and both judges — were not run again: that is a paid model run. The audit side was: it is a function of the posted files, and re-auditing the 424 posted files of both runs on v0.9.0 reproduces every committed row — all 580 sentence flags of the constructed benchmark and all 225 trajectory scores of the real one, so every number above holds for those files. That is no longer because the quote check never fires on them. Since v0.9.0 it asks where a quote was cut before it compares text, and of the 5,615 quotes in those files — all exact slices of their sources — it refuses 8; 1 of 11,547 claims changes status, and no scored sentence or trajectory moves ([Known limitations](#known-limitations), [reproducing](#reproducing-the-numbers)). Each number is summarised from [docs/benchmark.md](docs/benchmark.md), where it names the run it came from; the per-trace row files are committed under `bench/results/`. What changed in this release is in [CHANGELOG.md](CHANGELOG.md); the version-by-version record is in [bench/HISTORY.md](bench/HISTORY.md#versions).

**Authors:** Katia Engalycheva, with Claude (Anthropic) as co-author. The decisions, reviews and write-ups are mine; much of the typing is not.

Issues and corrections are welcome — especially a case where the audit is wrong.

## The Problem

Teams shipping multi-step agents (retrieve → tool call → summarise → answer) check the answer against its sources with a citation checker, RAGAS faithfulness, or an LLM judge. All of those look at **one hop**: does the answer quote its immediate context?

But the context was usually written by a model too. When a summariser invents a sentence and the answer quotes that summary verbatim, every one-hop check reports 100% grounded. The fabrication acquired the appearance of support by passing through a step that copied it faithfully. Anyone debugging an agent that "cites everything and is still wrong" hits this, and today the answer is a score, not a location.

## The Solution

`tallystick` treats an agent run as a set of books. Each claim is a debit; each piece of evidence is a credit. An entry is verified by three deterministic gates — the cited artifact exists, the step that wrote the claim actually *had* it as an input, and the quoted text is really at that address. Then the chain is walked backwards until it lands on a **root** artifact (a document, a tool result) or breaks.

Where it breaks is the injection point. The output is a step number, not a faithfulness score. No model is asked whether anything is faithful.

## Demo

```
$ tallystick examples/laundered_summary.json

Trial balance — 3 claims in the final answer
──────────────────────────────────────────────────────────
  grounded       2  ████████████░░░░░░  67%
  laundered      1  ██████░░░░░░░░░░░░  33%
──────────────────────────────────────────────────────────
  coverage         54.0% of the answer (67 of 124 letters and digits)
  under no claim   0.0% of the answer - not checked
  claims closed    2 of 3
  laundering rate  33.3%   <- invisible to one-hop checks

1 claim(s) did not close:

  [LAUNDERED] ans_3: Management expects the fourth quarter to be the strongest on record.
      introduced at step s3 (summarize) — model prior, no external evidence
      └─ EVIDENCE:summary#113-181

BOOKS DO NOT BALANCE
```

In that example every sentence of the answer quotes the summary verbatim, so a one-hop checker passes all three. The third sentence was invented at step s3; the filing it came from explicitly says no guidance was given.

Two terms, since they appear above before they are explained. A **posted trace** is a run with its claims and evidence entries already written in — by hand, or by the model-side proposer below. The **trial balance** is the per-claim verdict over the final answer: `grounded` traces to something outside the model, `laundered` cites something real whose own support is missing further back, `unsupported` cites nothing.

```
$ tallystick examples/laundered_summary.json --chain ans_3

ans_3 [laundered] depth=1
  "Management expects the fourth quarter to be the strongest on record."

  1. ans_3 @ step s4
     credit EVIDENCE:summary#113-181  [intermediate]
     quote  "Management expects the fourth quarter to be the strongest on record."

  chain breaks at step s3 on claim sum_4: model prior, no external evidence
  that claim reads: "Management expects the fourth quarter to be the strongest on record."
  and nothing outside the model funds it.
```

## How to Use

Everything in the first two steps needs no API key, no account and no network.

```bash
git clone https://github.com/shipwithkatia/tallystick && cd tallystick
pip install -e .
```

Python 3.10 or newer. Run the commands below in a terminal — **Terminal** on
macOS, **PowerShell** on Windows, or the built-in terminal in your editor
(in Cursor and VS Code: Terminal → New Terminal).

Check that your copy of the code agrees with this one — no key, no network:

```bash
pip install -e ".[dev]"
pytest -q
```

Green is the expected result. A handful of skips is normal: those tests need
the AgentHallu corpus, which is not in this repository. The expected failures
(`xfail`) are normal too — each one names a limitation described in this
README (most under [Known limitations](#known-limitations)) or in
`docs/auditable-traces.md`, held open by a test.

**1. Ask whether a run can be checked at all.** This is the cheap question, and
it comes first: a recording that did not keep what an audit needs cannot be
audited, however much you spend on the audit. No model is called.

```bash
# six test logs are included - deliberately different shapes, not your data
tallystick check-trace examples/logs/clean_run.json        # a tidy run
tallystick check-trace examples/logs/ambiguous_tools.json  # CANNOT BE CHECKED, and why
tallystick check-trace examples/logs/not_an_agent_log.json # refused, exit 2
```

`examples/logs/README.md` says what each one is and what it should print;
`examples/expected/` holds the exact output, so you can tell whether your copy
agrees with this one.

Then your own log. An OpenAI `messages` array, whatever your app already
writes — LiteLLM, vLLM and most gateways use the same keys — or an
OpenTelemetry GenAI span export. No recorder, no code change:

```bash
tallystick check-trace my_log.json
tallystick check-trace my_log.json --json report.json --quiet   # for CI
tallystick convert     my_log.json -o trace.json                # keep the reading
```

A long chat makes a big trace. Every step lists every artifact recorded before
it, because a chat sends its whole history each turn, so the trace grows with
the square of the turns: 2,000 turns, each a line of text, one tool call and a
40-character reply, turn a 0.9 MB log into a 127 MB trace
(`python bench/trace_growth.py`, no data needed). `convert` and `check-trace`
say so, with the sizes, once the trace is ten times the log; the exit code does
not change. Why the format keeps it that way
is in [docs/auditable-traces.md](docs/auditable-traces.md).

Two things the file cannot tell it, and you can. A tool that hands the model's
own words back (a `final_answer` tool, a note store) is not evidence; a tool
that returns a page exactly as fetched is:

```bash
tallystick check-trace my_log.json --tool-returns-model-text save_note
tallystick check-trace my_log.json --tool-returns-verbatim  read_file
```

A third declaration is for a tool whose result is external evidence in its own
words — a search API's summary, an API response. It counts the tool as declared
for strict mode below. It never undoes a **demotion**: where the reply IS a
value the answering call carried — the whole reply, one of its lines, the
single value of a JSON reply — the reader established that itself, and it stays
the model's own text whatever you declare.

Tool names in all three declarations are matched with case and surrounding
spaces ignored, and a name that matches no tool in the log is reported rather
than left to do nothing in silence. The name `tool` cannot be declared: it is
what this reader writes in where the log gives a call no name at all, so
declaring it would reach every unnamed result in the file. Declaring it is
refused out loud, in a note under the report. That holds for a log whose tool is
literally named `tool` too, so such a log cannot pass `--require-declared-tools`.

```bash
tallystick check-trace my_log.json --tool-returns-external web_search
```

The report counts, on one line, how many tool results came from tools you
declared nothing about, and from how many names. That line blocks nothing. A
tool result is where the audit stops by design, and how much of a run is out of
reach is what the rest of `check-trace` already reports.

**The share, and how to make it a gate.** The report gives the share of the
text a chain walks through that holds the model's own words rather than a
tool's reply — the higher it is, the more of the run an audit can follow. By
default it decides nothing. `--min-reachable` turns it into a gate: exit 1 when
the recording is thinner than the line. The bare flag means 0.8, read off the
AgentHallu run rather than chosen as a constant; `--min-reachable 0.5` moves it.
What the line is worth — and why most of the banding behind it is arithmetic
rather than prediction — is in
[docs/design.md](docs/design.md#before-the-audit-can-this-trace-be-audited-at-all).

**Strict mode, for a team whose tool set is fixed.** `--require-declared-tools`
turns that count into a gate: exit 1 with `undeclared_tools` until every tool
in the log is declared one of the three ways. `audit` and `propose` take the same
flag and use the declarations recorded when the log was read. It is off by
default because it would fail almost every real run: on the AgentHallu corpus,
662 of 693 trajectories (95.5%), even with their four echo-returning tools
declared (`python bench/strict_mode.py <AgentHallu>`; the corpus is not in this
repository — `git clone https://github.com/liuxuannan/AgentHallu`).

```bash
tallystick check-trace my_log.json --require-declared-tools \
  --tool-returns-verbatim read_file --tool-returns-external web_search
```

Neither is guessed from the log. By default every tool result is treated as a
wall the audit cannot see past, which counts against your recording rather
than quietly in its favour.

**What the reading decides on its own, and what it only notes.** One thing is
read from the file without being told, because the file shows it: a tool
result that IS a value of the call it answered — the model's own text handed
back — is the model's text, not a root (the demotion above).

**A note, not a verdict.** The reader also lists tool results it kept as
evidence although they may be the model's own text: at least half of the reply
stands in text the model wrote into a call (the one it answered, an earlier
one, another of the same turn), a short reply is a value an earlier call
carried, most of the reply is a whole message the model wrote earlier (a memory
or history tool handing back what the assistant said), the reading matched the
result to no call, or it could not weigh the reply to the end. `check-trace` and
`audit` print them under the report as `NOTE`, `--json` lists them in
`may_be_model_text`, and `--quiet` prints them on stderr: a header line, then one
line per result, at most ten and a `+N more` line after them. **A note never
changes the exit code** and asks you to confirm nothing, with or without
`--json`.

What a note is worth, measured: an external review drew 20 notes at random on
AgentHallu and read them by hand — **6 of 20 were the model's own text**; 7 were
a tool doing honest work (an interpreter printing a number it computed under a
label the model wrote), 7 disputable (a browser's `Navigated to <url>`, a record
of an order the model placed). So a note is a reason to look, not a finding.
The hand labels are not in this repository. How often notes appear is: 123 of
693 AgentHallu trajectories get at least one, 154 notes in all
(`python bench/echo_notes.py <AgentHallu>`).

If a noted tool does hand the model's text back, say so and read the log again:
`--tool-returns-model-text NAME`. That demotes its results and can change the
verdict; the note cannot.

Before v0.8.0 the same results were **warnings** that held the exit at 1
until you confirmed each tool with `--accept-echo-warning`, and for a stretch of
v0.7.5 they were removed altogether. The flag is gone and does not come back: a
signal wrong two times in three may not fail a build, but what it noticed is shown.

What the reviews found still passes without a word — no demotion, no note — is
under [Known limitations](#known-limitations), with its numbers.

**2. Audit a run whose claims are already posted.** Deterministic, offline, no
SDK needed:

```bash
tallystick examples/balanced_run.json     # what a passing gate looks like: exit 0
tallystick examples/laundered_summary.json  # a fabrication quoted faithfully: exit 1
tallystick examples/laundered_summary.json --chain ans_3   # the full chain for one claim
```

Exit codes are the product decision here. **0** — every claim in the answer
traces back to something outside the model, and at least half of the answer
stands under a claim. **1** — a claim does not, and the report names it and the
step that introduced it. **1** also when more than half of the answer's letters
and digits stand under no claim at all (`answer_mostly_unclaimed`): the claims
may all balance, but they speak for less than half of what the user saw. The
report prints `coverage` as the share of the WHOLE answer under claims that
close, and `under no claim` beside it, always; the share of claims that close
is the line `claims closed`. Post claims on the rest of the answer to clear it —
there is no flag that does. The line is at half, and it stays there by the
owner's decision of 16 September 2026, taken knowing its price: on the 1,489
posted traces of this project's benchmark runs (RAGTruth and AgentHallu, the
current runs and earlier ones, and `examples/`), it moved 130 from exit 0 to
exit 1 — about 9 in 100, more than was named before the measurement
(`python bench/half_line.py`; the benchmark's posted traces are written by
`bench/run.py` and `bench/agenthallu.py` with a model and are not in this
repository, so without them the script counts `examples/` only). If half of an answer is unchecked, "the books
balance" is the silent assurance this tool exists to refuse. Moving the line to
two thirds after seeing the number was considered and refused. **1** also when more
than one artifact is marked `final_answer` (`multiple_final_answers`): the trace
does not say which answer the user saw, so claims that balance on one of them
do not make a checked run — the same finding `check-trace` reports on that file.
**2** — the audit could not run at all: a malformed file, a missing key, a
trace with no claims posted on it yet, or a `_meta` block the command cannot
read (`_meta` that is not an object, or a field it reads that has the wrong
type — a list given as a number). A file that cannot be read must
never read as "this agent failed". A `_meta` it cannot read is refused rather
than skipped, because what the reading left out is recorded there.

**2** also when the only claims that did not close are ones the audit could not
walk to the end: a chain of claims deeper than 256 hops. The report prints
`BOOKS NOT CHECKED` instead of a balance, those claims get the status
`unchecked` — "not checked", a third outcome beside `grounded` and the statuses
that fail — and the reason is `chain_too_deep`. Nothing was found against them,
so this is not exit 1. Anything else found in the same run — a claim that really
does not close, most of the answer under no claim, two answers — still makes it
exit 1, with `chain_too_deep` listed beside it.

**3. Let a model post the claims.** This is the only step that costs anything,
and the only one that needs `ANTHROPIC_API_KEY`. It reads a raw trace, writes
the claims and the evidence behind each one, and audits the file it wrote:

```bash
pip install -e ".[propose]"
tallystick propose raw_trace.json -o posted.json
```

Without a key you can still see the whole path, with canned answers and no
network — this is what CI runs:

```bash
tallystick propose examples/raw_research_run.json -o posted.json \
  --proposer fake --script examples/fake_answers.json
```

**4. Record a run yourself, if you want the recording to be better than your
logs.** The LangChain recorder writes down what each step actually saw, which
a message list cannot tell you:

```bash
pip install -e ".[langchain]"
python examples/langchain_demo.py     # a 4-step agent, recorded -> raw_langchain.json
```

```python
from tallystick.adapters.langchain import TraceRecorder

rec = TraceRecorder()                                 # one recorder per agent run
chain.invoke(question, config={"callbacks": [rec]})   # any LangChain runnable
rec.save("raw.json")                                  # then: tallystick propose raw.json
```

Exit code 2 is for what stops the audit from running — a malformed trace, a missing SDK or key, a proposer that crashed. A bad API key must never read as "books do not balance". A file that breaks Python itself first need not end in 2: see the item on nesting under [Known limitations](#known-limitations).

The proposer's output is a file. Two proposer runs may differ; two audits of the same file never do. That boundary is physical: `tallystick/propose/` is the only package allowed to import an SDK, and nothing in the verdict path may import `propose/` (`cli.py` may, lazily, inside the `propose` subcommand only). A test walks the AST and fails the build on either, for every spelling of the import it knows, including the ones that once slipped past it.

```python
from tallystick import audit

balance = audit("run.json")
assert balance.books_balance       # drop straight into a test suite

balance.coverage                   # share of final CLAIMS that close - not of the answer
balance.laundering_rate            # share that cite something real but unfunded
balance.injection_points()         # failing claims, each with the step that broke
```

How much of the answer those claims cover — what the terminal prints as
`coverage` — is `answer_cover`:

```python
from tallystick import audit, load_run, read_json_file
from tallystick.report import answer_cover

raw = read_json_file("run.json")
cover = answer_cover(load_run(raw), audit(raw))
cover.coverage, cover.unclaimed    # whole answer: under claims that close / under none
cover.mostly_unclaimed             # True where `tallystick audit` exits 1 for it
```

`audit()` returns the balance; books that do not balance return normally with
`books_balance` False (the terminal's exit 1). A claim the audit could not walk
to the end has the status `ClaimStatus.UNCHECKED`, is listed by
`balance.unchecked()`, and leaves `books_balance` False — where the terminal
exits 2 with `chain_too_deep`, Python returns, so check `balance.unchecked()`
before reading False as "does not balance". A file that cannot be read —
missing, a directory, not JSON, a `_meta` of the wrong shape — raises
`TraceError` (the terminal's exit 2). `audit()` does not look at how many
answers are marked `final_answer`; `tallystick audit` does.

The trace format is plain JSON — artifacts, steps with inputs/outputs, claims by character span, entries — documented in `tallystick/io.py`. `examples/laundered_summary.json` is a complete posted trace; `examples/raw_research_run.json` is the same run before posting; `examples/balanced_run.json` is the same run with an honest summariser, and it balances. A `Run` can also be built in Python from the exported `Artifact`, `Step`, `Claim` and `Entry` types; it gets the same validation as a file.

Before spending anything on a model, `tallystick check-trace` tells you whether a raw trace can be audited at all, and which defects a recorder can fix: [docs/design.md](docs/design.md#before-the-audit-can-this-trace-be-audited-at-all).

## Tradeoffs and Decisions

- **All-of over any-of.** A citation that covers several upstream claims closes only if every one of them does, so a wide, lazy citation inherits the worst sentence it covers. It is stricter and occasionally flags a fine answer.
- **The model proposes into a file; the verdict reads the file.** Two proposer runs may differ; two audits of the same file never do.
- **Verbatim or nothing on the model side.** Letting the proposer paraphrase would raise recall and quietly bring a judgement call back into the credit, so paraphrase is dropped and logged.

The full list, with what each decision cost and what review found: [docs/design.md](docs/design.md#tradeoffs-and-decisions).

## Known limitations

This list is what the external reviews had found by 25 September 2026. It is
not complete: each review found shapes the one before it had not, and the next
will most likely find more. The tool refuses what this README describes as
refused; anything else, it may accept.

Found by external review and not closed. Most entries carry a measurement, and
where one is missing the entry says so; where a command recomputes the number,
it is given. The tool's promise these do not break: a
tool result the log shows to be the model's own value is never evidence, and a
note never moves an exit code. What they do mean is that some of the model's
text still passes as evidence **without a word**, and that the reading is
stricter than it needs to be in places.

**The quote check: what is measured, and on what**

The quote check (gate 3) asks two things of an evidence entry: whether the
quote says what the source says at that address, and — since v0.9.0 — where the
span was cut: a span whose boundary falls inside a number or a word, as the
check reads them, is refused, because `66 300 people` cut after its third
character is `300 people`, word for word. What it does not read as part of a
number or a word it does not protect. The shapes of that kind the reviews have
found are below, and they are not all there are. What a
quote may differ by is listed, not what it may not: whitespace, the comma, the
two quotation marks, and a sentence mark where the quote begins or ends
(`tallystick/tokens.py`). Before that list applies, both sides are read
through `tallystick/normalize.py`, and that reading forgives more: letter case
as Python's `casefold` reads it (`Polish` cited as `polish`, `ß` as `ss`), the
kind of dash (six dashes and the minus sign `−` read as `-`), the style of a
quotation mark (curly, low and angle quotation marks and the primes `′` `″`
read as `'` or `"`, so a prime may be dropped where a quotation mark may),
Unicode composition, and six invisible characters (the soft hyphen, the
zero-width space, the two zero-width joiners, the word joiner and the
byte-order mark). No other difference is on either list.

What that promise rests on is one instrument and one corpus, and it is no wider
than they are:

- `bench/quote_gate_corpus.py` builds its cases from the 5,615 real quotes of
  the two published benchmark runs. At v0.9.0 it refuses every tampering pair
  and every cut span it asks about, and accepts every typographic pair, whole
  span and value of real tool output it asks about: 305,499 tampering pairs,
  284,010 typographic pairs, 194,321 spans cut inside a number and 415,886
  inside a word, 1,381 values of tool output. Members of a list of numbers
  written with no space after the comma are counted apart and are all refused
  (192 of 192; see below). The 1,381 are the 1,044 values of real tool output
  counted below and the same objects reprinted as compact JSON (529), less
  those 192 list members (75 and 117). Each change is planted in the
  middle of a span and at both of its edges, and the thresholds are written in
  the script before any rule is run. Two controls must fail and do: a boundary
  that reads digits alone, and one that reads every mark beside a digit as part
  of the number.
- That is a statement about the classes the instrument asks about, on this
  corpus. Classes it does not ask about, as far as the reviews have found
  them, are below: forgeries that pass, honest quotes that are refused, and
  what the check does not attempt.
  Frequencies are counted over the 2,325 distinct texts of the two runs; they
  measure how often the shape occurs (exposure), not how often a forgery was
  made. Where an entry says the gate accepts or refuses, that is the gate's
  own answer: a pattern finds the places the shape occurs, as the pattern
  defines it, the cut is made there, and the gate is asked about that one
  boundary. A count with no answer beside it (`+/-` 4 times) is the
  pattern's alone.
- On the 5,615 real quotes themselves — all exact slices of their sources — the
  boundary checks refuse 8. One is a real cut: `1/2` cited as `2`
  (`Magentic_One__009`). Seven are the price of the rule, named in
  `tallystick/tokens.py` and printed by the instrument on every run: four at a
  seam the rule reads as inside a word (`structure—there` cited from `there`
  twice, `2,883Medal` from `Medal`, `1811The` from `The`) and three at a spaced
  sign (`Nepal Census 2011 - 2.6 %` cited as `2.6`; `Hydroxide (2) > 4-…`
  cited from `4`, twice).
- The instrument has limits of its own. Of the 270 cells of its hand-decided
  table of marks beside a numeral, 120 carry an example copied from the corpus;
  the other 150 were decided by argument, and all 150 agree with the rule, so
  they check consistency rather than measure. Four changes to the rule pass
  both the instrument and the test suite: reading `US$ 1.183` without its
  letters, joining only `½` (not `⅓`) across a space, treating only `→` (not
  `↓`) as a relation, and reading `^` between spaces as an operator. Where
  the rule stands on those four is held by nothing.
- The corpus is not in this repository; see
  [Reproducing the numbers](#reproducing-the-numbers).

**Forgeries the quote check lets through**

Found by the external reviews of v0.9.0; the rule was frozen for this release
before the later ones. None has been closed; for none has the price of closing
it been measured.

- **The left end of a range written with spaces.** `$3.74 - $4.83` cited as
  `$3.74`, `5 – 10` as `5`: accepted. The right end (`$4.83`) and the range
  without spaces (`$3.74-$4.83` cited as `$3.74`) are refused. A spaced dash is
  read as the sign of the number after it, and nothing reads it as joining the
  number before it. 195 places in the texts have a number, a dash of any kind
  with spaces on both sides, and a number (49 in tool output), this price range
  among them; the gate accepts the left number alone at all 195.
- **A dash with a space after a number.** `pp. 1119– 1190` cited as `1119` or
  as `1190`: accepted. A dash after a number is read as a compound hyphen
  (`500-entry`), which leaves the number whole. 13 places in the texts, all in
  tool output, have a number, a dash, a space or a line break, and a number;
  the gate accepts either number alone at all 13. Eight are ranges — seven page
  ranges and `(2−\n10)` — and five a list number after a broken line
  (`butylnona- 6-\n\n2.`).
- **A sign before a bracket.** `U = −(3/5) GM^2` cited as `(3/5)`, `-(5%)` as
  `(5%)`: accepted. A bracket hugging a number is read as part of it; a sign
  outside the bracket is not. 28 places in the texts have a sign before a
  bracketed digit, and the gate accepts the cut after the sign at all 28. In 2
  there is no letter or digit before the sign: this formula and a line of
  drawing code (`(0,0)--(2,4)`); the other 26 are chemical names
  (`2-(4-benzylphenyl)`). These are counted with a digit right after the
  bracket; with a sign inside it there are 2 more (`-(-2)**2` in code,
  `$-(-2)^2$`), and there the gate accepts the cut too.
- **A word run into a number.** `Windows10` cited as `10`: accepted. A word in
  small letters run into a number is read as the seam of a scraped table
  (`Kashmir1 \nBakshi`), the owner's decision of round 7, because refusing the
  shape refused real honest spans. The gate accepts a cut between a small
  letter and a digit at 4,340 places in the texts, 4,171 of them in tool
  output. Sorted by a rough script of ours rather than read one by one, so
  the split below is approximate, most are not words: 1,934 are a digit after
  the letter of a literal
  escape in output saved through `repr()` (the `0` of `\xa0`, a `1` after a
  literal backslash-n), 1,249 are inside identifiers that mix letters and
  digits (hashes, UUIDs), 279 follow one or two letters (`cp310`, `Cl2`); 878
  are a word of three or more letters run into a number (`manylinux2014`,
  `Shar2`, the medal-table seam `Malta9 \nMonaco7`). How many of the 4,340
  would make a misleading quote is not measured.
- **Shapes built by the review.** `↓5.2%` cited as `5.2%` (an arrow is read as
  relating two values, `J=0→1`, not as a sign), `+/-0.5` as `-0.5`, `10**3` as
  `10`, `:30` as `30` (a time without its hour; `10:30` cited as `30` is
  refused), `&minus;5` as `5` (the check reads text as it is and does not decode
  HTML). In the texts: `↑` or `↓` before a number 0 (other arrows 9: `→` 7, as
  in `J=0→1`, `⇐` and `⇒` one each, and the gate accepts the number alone at
  all 9); `+/-` 4 times, never beside a number; `**` between digits 2 (code);
  an HTML entity before a digit 4.
- **The mixed fraction.** `5 1/2` cited as `5` or as `1/2`: accepted. 9 places
  in the texts (`44 3/4″`); the gate accepts either part alone at all 9.
- **A size written as a word.** `$5 million` cited as `$5`. A boundary cannot
  see that a word changes a number; the instrument names it and does not score
  it.
- **An operator after a number.** `9.109390 x 10-31` cited as `9.109390`,
  `5 ^ 2` as `5`, `5 +/- 0.2` as `5`, `5 ×10` as `5`: accepted. `×` and `*`
  with a space on both sides are read as joining two numbers (`5 × 10` cited as
  `5` is refused); the letter `x`, `^`, `+/-`, and a `×` that touches only the
  number after it are not. In the texts the gate accepts the number before an
  operator alone at 9 places, all before ` x ` (`4 x 4` sizes,
  `6.626 x 10^-26`); `×` after a spaced number stands 66 times and the gate
  refuses the number alone at all 66; `^` or `+/-` after a number and a space,
  0.
- **A dash and a space at the start of a line.** `- $5.2 million` at the start
  of a line cited as `$5.2 million`, `- 5.2%` as `5.2%`: accepted. A dash and a
  space that open a line are read as a list bullet, not as a sign. The same
  dash in the middle of a line is read as a sign (below), and `-$5.2 million`
  with no space is refused at the start of a line too. 41 lines in the texts
  open on a dash, a space and a number (14 in tool output): 40 list items
  (`- 2 aromatic hydrogens`, `- 8 Orcs`) and the date line of a page header
  (` - 17 May 2019 | `), none a sum of money; the gate accepts the number
  alone at all 41.
- **A cut at a capital, an underscore or an escape inside a word.** `notFound`
  cited as `Found`, `not_found` as `found`, `MacArthur` as `Arthur`,
  `C:\nuclear` as `uclear`: accepted, and the first two drop a negation. A
  small letter before a capital is read as the seam of scraped text
  (`resultsEtta Cone`), an underscore does not hold a word together, and a
  letter after a backslash belongs to the escape. The gate accepts the cut at a
  capital after a small letter at 4,819 places in the texts (4,251 in tool
  output), after an underscore at 8,112 (6,304), after `\n`, `\t` or `\r` at
  2,897 (2,582). Where the part cut off is `not`, `non`, `no` or `un`: 1 place
  at a capital (`notWikipedia`), 3 at an underscore (URL slugs such as
  `no_more_ads`).
- **A mark after a number.** A trailing minus `1,234.56-` cited as `1,234.56`,
  a prime `4″` as `4`, `5,-` as `5`: accepted. A minus or a prime after a
  number is not read as part of it. In the texts 16 places, all in tool
  output, have a dash directly after a number and then whitespace, a comma, a
  full stop, a semicolon, a colon, `)`, `]` or the end of the text, with no
  number after the dash and its whitespace; none of
  them is an amount with a trailing minus (open ranges `(1942–)`, suspended
  hyphens `1,3-, 1,4-`, identifiers), and the gate accepts the number alone at
  all 16. A prime or double
  prime follows a number at 34 places; the gate accepts the number alone at 22
  (8 sizes, `4″ layer`; 14 map coordinates, `5°33′59″N`). `5,-` does not occur
  in the texts.
- **A symbol before a number.** `▼5.2%` cited as `5.2%`, `🔻5%` as `5%`, `➖5` as
  `5`: accepted. These are symbols (Unicode `So`), not arrows, and a symbol is
  not read as a sign. In the texts: no `▼`, `🔻` or `➖`; a symbol directly
  before a digit is `©` once (`©2015`), `°` 20 times (there the gate refuses
  the number alone), and the replacement character U+FFFD 1,660 times, in
  binary junk in tool output (the gate accepts the number alone at 1,563).
- **A currency before a bracket.** `$(5.2) million` cited as `(5.2) million`:
  accepted. A currency is read as part of a number it touches, and here a
  bracket stands between them; the cut inside the bracket (`5.2`) is refused.
  8 places in the texts have a currency before a bracketed digit: 7 in TeX
  math (`$(0 1)$`) and 1 in binary junk in tool output; the gate accepts the
  cut at all 8.
- **A mark before a word.** `if !ready then abort` cited as `ready then
  abort`, `x != 5` as `= 5`, the removed line `-enable_ssl = true` of a diff as
  `enable_ssl = true`, `-inf` as `inf`, `~mutable` as `mutable`, `¬valid` as
  `valid`: accepted. The first four drop a negation or a sign; the diff line
  cited that way says a setting that was taken out is in force. `unsafe` cited
  as `safe` and `-$5` as `$5` are refused. This is not an oversight in one case
  but the open side of a symmetry: the rule reads a number together with the
  marks that change its value — a sign, a currency, a bracket — and reads a
  word as its letters only. A mark in front of a word is not part of the word,
  so a cut right after it is not a cut inside a word, and the gate accepts
  it. In the texts 235 places have a dash, a minus sign, `~`, `¬` or `!`
  directly before a letter and opening a word (134 in tool output); the gate
  accepts the cut after the mark at 229. Nine are in binary junk. The other
  226 are a dash after a space (85: `-i` in a matrix, `-OH`), `~` in a URL
  (62, `/~history/`), a dash after an opening bracket (56, `(-OH)`, `(-HCl)`),
  `~` before a word (21, `~Psychic Power`), `!=` in code (1) and a dash opening
  a line (1, `-x³`); outside the junk there is no `!` or `¬` before a word and
  no line of a diff opening on a dash before a word. None of the 5,615 real
  quotes begins right after such a mark. How many of the 229 would make a
  misleading quote is not measured.

**Honest quotes the quote check refuses**

- **A member of a list of numbers with no space after the comma.** `3,4,7,8`
  cited as `4`: refused, because a comma between two digits joins them
  (`7,000`). Letting a comma join only three digits would accept `6-diene` cut
  from `nona-2,6-diene`, so the owner kept the comma. 75 of the 1,044 values of
  real tool output in the two runs (7.18%), all in one text (`SmolAgents__003`);
  117 of 529 values (22.12%) when the same objects are reprinted as compact
  JSON, members of 31 runs of numbers inside arrays, in 24 texts. Part of it
  cannot be told from a grouped
  number by any reading: `67,163`.
- **Two numbers one space apart, the second of three digits.** `GET /api 200
  512` cited as `512` or as `200`, `HTTP/2 200` as `200`, a pandas row `0  5 100`
  as `100`: refused. By the characters this is a number with a grouping space
  (`66 300`). 46 places in the texts, 45 in tool output; some are real groupings
  (`557 000 males`), some lists (`240 146 144 170`).
- **A spaced dash before a number in the middle of a line.** The Python logging
  line `2024-01-05 10:30:00,123 - INFO - 42 rows` cited as `42 rows`,
  `see Report - 2023 file` as `2023 file`, `Nepal Census 2011 - 2.6 %` as
  `2.6 %`: refused. In the middle of a line a spaced dash before a number is
  read as its sign, so that `Operating margin - 5.2 % lower` cited as
  `5.2 % lower` is refused; by the characters the two are the same, and the
  owner chose the forgery's side. 164 places in the texts have a letter or a
  closing bracket, spaces, a dash of any kind, spaces and a number (86 in tool
  output); the gate refuses the number alone at all 164. 1 of the 5,615 real
  quotes.
- **A literal escape before a number.** `p.\xa0247` cited as `247`: refused.
  Output saved through Python's `repr()` carries the escape as text, and its
  last character runs into the number. 80 places in the texts, all in tool
  output, have a literal `\x..` or `\u....` escape directly before a number:
  13 are `\xa0`, the rest `\u200a` and other escapes, most of them control
  characters (`\u0003`). The gate refuses 78; the two `\ufeff` are accepted.
- **Part of a timestamp or an address.** The date of `2025-08-27T00:00:00Z`, the
  port of `192.168.1.1:8080`: refused. 67 ISO timestamps with a `T` in the texts
  (66 in tool output), and the gate refuses the date alone at all 67; no IP
  address with a port.
- **A version.** `v1.2.3` cited as `1.2.3`: refused. One letter before a number
  is read as one token with it (`B12`, `H5N1`). 27 places in the texts, all in
  tool output (`Astropy v7.1.0`); the gate refuses the number alone at all 27.
- **A mark a recorder dropped or spelled out.** A full stop is content wherever
  it stands between two words, so `the U.S. delegation` cited as `the US
  delegation` or `Dr. Smith` as `Dr Smith` is refused; so is a hyphen spelled
  out (`state-of-the-art` as `state of the art`) and a bracket dropped from the
  middle of a quote. The two published runs write every quote as a slice of its
  source, so none of these occurs there: exposure, not measured cost.

**What the quote check does not do at all**

- It checks where a quote came from and where it was cut, not what it means. A
  quote can be exact, uncut and still misleading: a pronoun whose referent is
  outside the span, a condition or a hedge left behind, a connective (`but`,
  `unless`) that the span drops. Zhang, Wan and Bansal found problems of this
  kind in about 30% of 1,600 extractive summaries — summaries made only of
  sentences copied from the source ([Extractive is not Faithful, ACL
  2023](https://aclanthology.org/2023.acl-long.120/)). tallystick does not look
  for them.
- The model-side proposer asks the gate's question about a number before it
  places a quote, not the question about a word. Of the 5,615 real quotes it
  places 5,611, and the gate refuses 4 of those for cutting a word: the four
  seams above. There a claim loses a credit.
- When the model adds a full stop or quotation marks to a quote, the proposer
  no longer finds it as it stands and looks for it by its words, and to that
  search a dash is part of a word only when a digit follows it directly. So an
  honest quote that opens on a dash before a currency or a point, on a dash
  before a second dash or a minus sign and a digit, or, in the middle of a
  line, on a dash, a space and a number is placed nowhere: `-$5.2 million.`
  for `-$5.2 million this year`,
  `–$5`, `-€5`, `(-$5`, `-.5`, `--5`, `-−5`, and `- 2023 file.` from `see
  Report - 2023 file`, each tried in the middle of a line. These are the forms
  tried, not a complete list. A quote that opens on a dash before a space and
  a word, or before a second dash and a word, is placed, but without the
  dash: see the next item. The exact quote is placed; so is a quote
  opening on the minus sign `−`, on a dash before a digit (`-5.2%`), or on `+`
  or `~`. In the texts the forms with no space after the dash open a word at no
  place; a spaced dash before a number in the middle of a line — ranges,
  logging lines, `Report - 2023`, `Tenet - $363,656,000` — stands at 449 places
  (230 in tool output), and at all 449 the gate refuses the number without its
  dash anyway. This error is on the safe side: a claim loses a credit.
- The next one is not. The same word search drops any mark at the edge of the
  quote that is not part of a word to it — a dash before a letter or a space,
  a bracket, `!`, `_`, a trailing dash — and where the gate does not protect
  that cut (the shapes above, and a mark before a word), it accepts what is
  left. An honest quote is then placed as a forgery and `tallystick audit`
  closes the books at exit 0. `!ready then abort now.` is placed as `ready then
  abort now`; the removed diff line `-enable_ssl = true.` as `enable_ssl =
  true`, which turns "taken out" into "in force"; `--no-verify now.` as
  `no-verify now`; `-inf here now.` as `inf here now`; `Amount 1,234.56-.` as
  `Amount 1,234.56`. Of 31 forms tried, each once with a full stop added and
  once in quotation marks, 15 were placed without their mark and closed the
  books at exit 0 both times, and in 13 of those the lost mark is a negation
  or a sign. Six more did until the proposer began to check that a span it
  finds by words states the quote's signs and digits, as the gate reads a
  number: a dash glued to a round bracket that holds a number alone (`"-(5%) this
  year"`) and a dash and a space before a number outside brackets at the start
  of a quote (`- $5.2 million lost.`) are now placed nowhere. That
  reading does not see a dash glued to a square or a curly bracket, a dash
  glued to a round bracket that holds more than a number, or a dash a space
  away from a bracket, so these are still placed without the dash, at
  exit 0, whether the model added the dash or the source has it: `"-[5] now
  here"` as `5] now here` (one of the 15), `"-{5} now here"` as `5} now
  here`, `"- (5%) this year"` as `(5%) this year`, `"-(5.2 million) this year"`
  as `5.2 million) this year`, in the middle of a line or at its start. In the
  texts a dash glued to a square or a curly bracket
  before a digit stands at no place; a dash, a space and a round bracket
  before a digit stands at 13 (5 of them a list bullet at the start of a
  line). The minus sign `−`, `+`,
  `~`, `¬`, a bullet `•` and a prime were kept. These are the forms tried, not
  the whole class. Of two forms beyond the 31, `- Paris is big.` in the
  middle of a line goes the same way and is placed as `Paris is big`, at exit
  0; `- 2023 file.` at the start of a line, once placed as `2023 file`, is now
  placed nowhere. The texts have 41 lines that open on a dash, a space and a
  number, 16 trailing dashes, 28 signs before a bracketed digit and 229 marks
  before a word where the gate accepts the cut (above). Up to v0.8.1 the
  proposer did the same to `-$5.2 million.`; that form is now placed nowhere.
- **A quote into the model's own text is written by its claim, not as it was
  checked.** When a quote points into an intermediate artifact — a summary or
  a plan the model wrote earlier in the run — the proposer finds it, checks it,
  and then writes into the trace the part that falls inside that artifact's
  claims, cut to their edges. A dash the quote held just before the claim is
  cut off: the exact quote `– (5%) this year` in `Margin fell – (5%) this
  year for the group.`, whose claim starts at `(5%)`, is written as `(5%) this
  year`, and the audit closes the books at exit 0. The check the quote passed
  was asked of the text with the dash; the text written was never asked. No
  warning is logged: the count of characters left outside a claim skips
  punctuation, and a dash is punctuation to it. A claim cannot start inside a
  number as the gate reads it, so a glued or spaced sign in the middle of a
  line (`-5% this year`, `- 5% this year`) stays inside its claim and is
  kept. What that reading leaves outside the number can be cut: of the forms
  tried, a list bullet at the start of a line, a dash glued to a round bracket,
  which the word search now refuses (`-(5%) this year` written as `(5%) this
  year`), a dash glued to a square or a curly bracket (`-[5] this year` written
  as `5] this year`), and a dash a
  space away from a bracket. Measured on the 2,767 quotes
  the two published runs write into intermediate artifacts: 408 stand right
  after a dash not glued to a letter or a digit on its left; for 404 of them
  that dash lies outside the claim, and with it added to the quote, those 404
  are written without it. All 404 are list bullets at the start of a line
  (`- 8 Orcs` written as `8 Orcs`), 14 of them before a number, none in the
  middle of a line. This counts where the route can drop a dash, not how
  often it did: a posted file keeps the text written, not the text the model
  quoted. **This route was not measured in any earlier round.** Every earlier
  price of the proposer was taken on the exact search and the word search,
  two of the three ways a quote reaches the trace: those numbers hold, and
  they are incomplete.

**The model's text that still passes as evidence, with no demotion and no note**

- **A note read back inside a store's record** (a mem0 search result, a
  LangGraph Store item) when the note is short: the record's id, hash and dates
  make up more than half of the reply. The limit is in characters, not words:
  on a mem0 record a note is listed from about 165 characters — 163 of long
  words, 182 of numbers, 189 of short words — so 20 long words (278 characters)
  are listed and 20 short ones (69) are not
  (`python bench/store_record_threshold.py`, no data needed).
- **Text added to a reply until the model's words are less than half of it** —
  an echo inside a longer page, or junk appended on purpose.
- **A message the model wrote, handed back as less than most of the reply**, or
  reformatted. Only a reply whose main piece IS a whole earlier message is
  noted. Two readings by share were built and measured on AgentHallu and not
  shipped: read by hand, none of the notes either added was the model's text
  handed back (a tool returning a file the model had quoted, a search repeating
  the question, a search answering in the words of the model's plan). The labels
  are not in this repository. The note that shipped fires on no AgentHallu
  trajectory, so what it is worth on real logs is not measured.
- **A note written straight onto other letters with no mark between**, in
  Chinese, Japanese, Thai, Lao, Khmer, Myanmar or Tibetan; **a value read back in
  another case**; **a bidirectional mark (RLM, LRM) after every word**, in any
  language.
- **A note in a script written without spaces that is not in the reader's
  list, read back inside anything** — JSON, `Note: "..."`, brackets. Javanese,
  Balinese, Sundanese, Buginese, New Tai Lue, Tai Le, Tai Tham, Tai Viet, Cham,
  Yi, Phags-pa, Han outside the basic blocks (extension B and later), Hangul
  written without spaces, Ethiopic with its word mark `፡`. A reply that is the
  bare note is still found. `tests/test_scripts_not_in_the_list.py` shows
  the missed shapes (marked as expected failures). No noise was found in their honest
  chats, but nothing was measured on real logs in these scripts.
- **The model's draft pasted back as a `user` message** ("here is your previous
  draft"), or a subagent's answer handed over as one: every `user` message is a
  root, and nothing compares it with the model's text. A rule to close it was
  built, measured and not shipped: read by hand, too much of what it demoted
  was honest material. The labels are not in this repository.

**Where the reading is stricter than it needs to be**

- **A page is demoted when one of its lines is one word the call carried.**
  The demotion reads every line of a reply as a value, and a word of the call's
  arguments is a value: a browser page with a line `Awards` after a `click`
  whose reasoning said "Awards" is recorded as the model's text. That costs a
  claim a source it had; it never makes the model's text evidence. Round 21 read
  such demotions on AgentHallu by hand (the ones call ids numbered per turn used
  to hide): some were the model's own text, some disputable (`cd` answering with
  the directory it was given), some a tool's own page. The labels are not in
  this repository.

**What the commands say, and where**

- `convert` does not list notes; the trace it writes keeps them in `_meta`, and
  `check-trace` on that trace prints them.
- `tallystick.audit()` in Python returns the balance without the notes; read
  `_meta.echo_warning_details` or run `check-trace`.
- A tool call's arguments nested more than 256 levels deep
  (`MAX_ARGUMENT_NESTING` in `tallystick/adapters/openai_chat.py`) are refused
  with exit 2, not read - the same on 3.10 and 3.12, the Pythons CI runs
  (`tests/test_nesting_limit_is_ours.py`).
  The log file itself is held to no limit of ours, and what a file nested past
  what Python recurses gets depends on the Python: exit 2 on 3.10, a traceback
  and exit 1 on 3.12, read on 3.14. Measured by hand in round 22; no test
  covers it.
- Inside one assistant turn, a call id given to two calls names neither: each
  result with that id is filed as matching no call, stays evidence and gets an
  `unmatched` note - so a value handed back by its own call is not demoted
  there. An id reused across turns is not affected.
- Inside a loop of claims that cite each other, the status does not depend on
  the claim ids, but the step the audit names as the break does: renaming one
  claim of a two-step loop moves the break from one step of the loop to the
  other, `laundered` both times. Open since v0.8.1.
- A recorder that writes UTF-16 code units where the format means code points
  gets a verdict about its agent rather than a word about its offsets: after
  one emoji every address is one character off, the quote is refused
  (`span_mismatch`), the claim is `unsupported`, and the output says nothing
  about offsets. Open since v0.8.1.
- Coverage counts letters and digits with `str.isalnum()`: the vowel signs of
  Devanagari, Thai and similar scripts are not counted, on either side of the
  half line.
- The wheel carries the package only. The commands in this README that name
  `examples/` need a clone of the repository.

## Reproducing the numbers

The measurements in this README and in [docs/benchmark.md](docs/benchmark.md)
are made on the **posted files** of two benchmark runs: 424 traces (199 from
RAGTruth, 225 from AgentHallu) with the claims and quotes the proposer wrote
into them. They live in `bench/work/` and `bench/work-agenthallu/`, which are
not in this repository.

- **Where the data comes from.** [RAGTruth](https://github.com/ParticleMedia/RAGTruth)
  (MIT) is cloned by `python bench/build.py`, which builds the constructed traces
  without a model. [AgentHallu](https://github.com/liuxuannan/AgentHallu)
  (CC BY 4.0) is cloned by hand under `bench/work-agenthallu/`. Six RAGTruth items
  and five AgentHallu trajectories are committed under `bench/sample/` and
  `bench/sample-agenthallu/` as test fixtures.
- **Why the posted files are not here.** They are the output of a paid model run
  (`bench/run.py`, `bench/agenthallu.py`, with `ANTHROPIC_API_KEY`), 8.7 MB, and
  they were kept out of the repository with the rest of the working directories.
  A new run writes different files: the proposer is a model, so its quotes
  differ from run to run. The audit of a given file never does.
- **What a fresh clone can check.** The committed row files recompute both
  tables with no model: `python bench/ci.py bench/results/2026-09-n100-v0.6.json`.
  `python bench/quote_gate_corpus.py` runs, and exits 0, on text the repository
  carries (131 sentences, 7,172 tampering and 6,625 typographic pairs) instead
  of the 5,615 real quotes; its
  counts are smaller and are not the ones quoted above. Scripts that need the
  posted files or the AgentHallu clone (`bench/diagnose.py`,
  `bench/strict_mode.py`, `bench/echo_notes.py`) exit 2 and say what is missing.
- **What it cannot check.** The instrument's counts on the 5,615 real quotes, the
  re-audit that shows the published rows unchanged, and the exposure counts in
  [Known limitations](#known-limitations). Those need the posted files. Both
  source datasets allow redistribution with attribution, so publishing the
  posted files — 1.7 MB as a gzipped tar — is possible; whether to publish
  them is still open.

## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it.
- Writing and attacking are different jobs. Again and again in this repo, the serious defects were found not while writing but in a separate adversarial review whose only brief was to break it — and fixes got the same treatment.
- Decide what the table means before it exists. The caveats and the three rows were published before a single live call, so when the number came in against tallystick on F1 there was nothing to negotiate.

More, with the cases behind each: [docs/design.md](docs/design.md#lessons).

## Next Steps

- [x] v0.7.5: an adapter for log shapes practitioners already have — OpenAI Chat Completions message lists and OpenTelemetry GenAI spans, read by `check-trace`, `convert` and `propose`
- [x] v0.8.0: the echo warning retired for a note that moves no exit code; more than half of the answer under no claim is exit 1; a chain past 256 hops is `unchecked` rather than a failure
- [x] v0.8.1: the quote gate's edit budget of 2% of the span removed; the break reason no longer depends on the order of the arrays in the file

The numbers v0.8.2 to v0.8.4 are not in this list: they were working versions
that stated the quote gate three different ways, and none of the three numbers
reached `main`. The rule they arrived at did — it is the list of what may
differ that v0.9.0 ships. What each one tried, and what stopped it, is in
[`bench/HISTORY.md`](bench/HISTORY.md#versions).

- [x] v0.9.0: the quote check asks where a quote was cut — a span whose boundary falls inside a number or a word, as it reads them, is refused — and reads a sign, a currency, a decimal point, a grouping space or comma, a percent or degree sign and a bracket as part of the number they touch, but not a trailing minus, a prime or a symbol such as `▼`; what the reviews found it still lets through and still refuses is under [Known limitations](#known-limitations), a list that is not complete, and the release notes are in [CHANGELOG.md](CHANGELOG.md)
- [ ] next version: read a word together with the mark that changes its meaning, as v0.9.0 reads a number with its sign, so that `!ready` cited as `ready` is refused. The price of that rule is not measured and may be too high to pay: a list bullet (`- Paris`), a line of a diff, a command flag (`--verbose`) and a hyphen in a compound word (`well-known`) are honest text that such a rule could start to refuse
- [ ] next version: the instrument asks about an honest quote with a sign outside the span it names, and compares the exact span on its honest side. It compares the span without its ASCII minus now, so a proposer that places an honest quote without that minus passes it; one test holds the case
- [ ] next version, a round of its own with its own instrument and price: the proposer's sign check reads a dash glued to a square or a curly bracket and a dash a space away from a bracket
- [ ] next: check *whether* a funding span supports a claim, not only *where* it is
- [ ] PyPI release

Done so far and the full list: [docs/design.md](docs/design.md#next-steps).

## Where This Sits

| | one-hop grounding | across steps | deterministic verdict | names the step | post-hoc on any trace |
|---|---|---|---|---|---|
| Anthropic Citations API | ✅ documents only | ❌ | ✅ | ❌ | ❌ |
| RAGAS / DeepEval / Patronus faithfulness | ✅ | ❌ | ❌ | ❌ | ✅ |
| LLM-as-judge, one-hop context | ✅ | ❌ *(F1 0.05 in the benchmark)* | ❌ | ❌ | ✅ |
| LLM-as-judge, full history | ✅ | ✅ *(F1 0.58 in the benchmark)* | ❌ | ❌ | ✅ |
| [AgentHallu](https://arxiv.org/abs/2601.06818) (Liu et al., 2026) | — | ✅ benchmark: 693 real trajectories, step labelled by hand | ❌ judges; best model finds the step 41% of the time | ✅ | ✅ |
| [Hallucination Snowball](https://arxiv.org/abs/2608.14588) (Singh & Pawar, 2026) | — | ✅ measures escape across 4 agents | — measurement study; detection by GPT-4o and others | ❌ | — |
| [TRACER](https://arxiv.org/html/2605.09934) (Yu, Jia et al., 2026) | ✅ | ✅ dependency graph to tool turns | partly: schema checks; semantics judged by Gemini | ✅ | ❌ agent emits its own provenance |
| [CAMS](https://arxiv.org/html/2606.23989v2) (Guan, 2026) | ✅ claim-anchored spans | ❌ one step | span resolution deterministic; claims extracted by a model, faithfulness scored by NLI models | ❌ | ❌ |
| [LEDGER](https://arxiv.org/abs/2608.18398) (Kim, Miao & Liu, 2026) | ✅ | ✅ | ❌ *(authors' own caveat)* | partly | ✅ |
| **tallystick** | ✅ | ✅ *(F1 0.59 in the benchmark)* | ✅ audit; proposer is a model | ✅ | ✅ |

The rows other than the two judges and tallystick are my reading of public documentation (the products) and of the papers' abstracts and method sections (the papers) as of September 2026, not benchmark results; the F1 cells come from the [benchmark](docs/benchmark.md).

What the 2026 papers around it measure, and what tallystick does that none of them do together: [docs/design.md](docs/design.md#related-work).

**Exact and still wrong.** That a quote copied word for word can mislead is
known. Zhang, Wan and Bansal ([ACL 2023](https://aclanthology.org/2023.acl-long.120/))
found extractive summaries unfaithful through coreference and discourse — a
pronoun, a missing condition, a connective — and built a metric for it;
journalism research names the out-of-context quote *contextomy*, and Song et al.
detect contextomized headline quotes with a trained encoder
([Findings of EACL 2023](https://aclanthology.org/2023.findings-eacl.52/)).
Checking that a quote is really in its source as an exact substring is common
practice too. What tallystick checks is narrower and different: **the cut
itself** — whether the boundary of a verbatim span falls inside a number or a
word — decided by code, not a model, and after the fact on a trace someone else
recorded. We looked for prior work that does this and did not find it; that is
a statement about our search, not about the field.

## Built With

- Python 3.10+; the verdict path is standard library only
- `anthropic` SDK, optional, for the proposer
- `langchain-core`, optional, for the recorder
- pytest

## Why a tally stick

An Exchequer tally was a stick notched with an amount and split lengthwise. Payer and payee each kept a half. To settle, the halves were fitted back together and the notches and the grain had to line up. No clerk's opinion was involved, and a forged half simply did not fit.

## License

MIT

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
