# tallystick

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

## TL;DR

- **Problem.** A multi-step agent can invent a fact in an intermediate step and quote it faithfully in the answer; every one-hop check then says "grounded".
- **What it does.** tallystick walks each claim in the answer back to the documents with plain code — no model in the verdict — and names the step where the chain breaks.
- **Result.** On 290 constructed answer sentences it is level with an LLM judge shown the full history: F1 0.59 against 0.58, with a false flag on 7% of clean sentences against 16%.
- **Main finding.** In 443 human-labelled real agent runs ([AgentHallu](https://arxiv.org/abs/2601.06818)), 53% of the hallucinations sit at a step where the agent wrote no prose, only a tool call — beyond this audit's reach (32% if a tool call's own text counts as checkable).
- **Limit.** On 225 real trajectories, where the hallucination is in the agent's own text, it flags the answer in 57% of runs but names the labelled step in 15% — and it flags 35% of clean runs.
- **Details:** [benchmark](docs/benchmark.md) · [design, decisions and lessons](docs/design.md)

![One hop is not enough: the answer quotes the summary, the summary invented a sentence, and the chain breaks at the summarise step](docs/chain.svg)

**Status:** research prototype, v0.7.x. Interfaces may change between versions. The numbers above are summarised from [docs/benchmark.md](docs/benchmark.md), where each names the run it came from; the per-trace row files are committed under `bench/results/`, and the limits of each measurement are stated next to it.

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

Before this version the same results were **warnings** that held the exit at 1
until you confirmed each tool with `--accept-echo-warning`, and one version
removed them altogether. The flag is gone and does not come back: a signal
wrong two times in three may not fail a build, but what it noticed is shown.

What still passes without a word — no demotion, no note — is listed with its
numbers under [Known limitations](#known-limitations).

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

Exit code 2 covers everything that stops the audit from running — a malformed trace, a missing SDK or key, a proposer that crashed. A bad API key must never read as "books do not balance".

The proposer's output is a file. Two proposer runs may differ; two audits of the same file never do. That boundary is physical: `tallystick/propose/` is the only package allowed to import an SDK, and nothing in the verdict path may import `propose/` by any spelling (`cli.py` may, lazily, inside the `propose` subcommand only). A test walks the AST and fails the build on either.

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

Found by external review and not closed. Each is measured; where a command
recomputes the number, it is given. The tool's promise these do not break: a
tool result the log shows to be the model's own value is never evidence, and a
note never moves an exit code. What they do mean is that some of the model's
text still passes as evidence **without a word**, and that the reading is
stricter than it needs to be in places.

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
  with exit 2, not read - the same on every Python (`tests/test_nesting_limit_is_ours.py`).
  The log file itself is held to no limit of ours, and what a file nested past
  what Python recurses gets depends on the Python: exit 2 on 3.10, a traceback
  and exit 1 on 3.12, read on 3.14. Measured by hand in round 22; no test
  covers it.
- Inside one assistant turn, a call id given to two calls names neither: each
  result with that id is filed as matching no call, stays evidence and gets an
  `unmatched` note - so a value handed back by its own call is not demoted
  there. An id reused across turns is not affected.
- Coverage counts letters and digits with `str.isalnum()`: the vowel signs of
  Devanagari, Thai and similar scripts are not counted, on either side of the
  half line.
- The wheel carries the package only. The commands in this README that name
  `examples/` need a clone of the repository.

## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it.
- Writing and attacking are different jobs. Every serious defect in this repo was found not while writing but in a separate adversarial review whose only brief was to break it — and fixes got the same treatment.
- Decide what the table means before it exists. The caveats and the three rows were published before a single live call, so when the number came in against tallystick on F1 there was nothing to negotiate.

More, with the cases behind each: [docs/design.md](docs/design.md#lessons).

## Next Steps

- [ ] an adapter for log shapes practitioners already have — OpenAI Chat Completions message lists first, then OpenTelemetry GenAI spans
- [ ] v0.8: check *whether* a funding span supports a claim, not only *where* it is
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
| LEDGER ([arXiv 2608.18398](https://arxiv.org/abs/2608.18398)) | ✅ | ✅ | ❌ *(authors' own caveat)* | partly | ✅ |
| **tallystick** | ✅ | ✅ *(F1 0.59 in the benchmark)* | ✅ audit; proposer is a model | ✅ | ✅ |

The rows other than the two judges and tallystick are my reading of public documentation (the products) and of the papers' abstracts and method sections (the papers) as of September 2026, not benchmark results; the F1 cells come from the [benchmark](docs/benchmark.md).

What the 2026 papers around it measure, and what tallystick does that none of them do together: [docs/design.md](docs/design.md#related-work).

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
