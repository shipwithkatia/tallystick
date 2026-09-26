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

**Status:** research prototype, v0.9.0. Interfaces may change between versions. The numbers above were measured on v0.6 (the constructed benchmark) and v0.7.3 (the real trajectories) and re-checked on v0.9.0 on the audit side; what that re-check covers is in [docs/known-limitations.md](docs/known-limitations.md#status-and-reproducibility). Each number is summarised from [docs/benchmark.md](docs/benchmark.md), where it names the run it came from. What changed in this release is in [CHANGELOG.md](CHANGELOG.md); the version-by-version record is in [bench/HISTORY.md](bench/HISTORY.md#versions).

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
(`xfail`) are normal too — each one names a limitation described in
[docs/known-limitations.md](docs/known-limitations.md) or in
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

A third declaration, `--tool-returns-external NAME`, is for a tool whose result
is external evidence in its own words — a search API's summary, an API
response. Tool names are matched with case and surrounding spaces ignored, and
a name that matches no tool in the log is reported rather than left to do
nothing in silence. The reader also lists, as `NOTE`, tool results it kept as
evidence although they may be the model's own text; **a note never changes the
exit code**. What a note is worth when read by hand, the two flags that turn
the reading into a gate (`--min-reachable`, `--require-declared-tools`), and
why both are off by default:
[reading a log](docs/known-limitations.md#reading-a-log-declarations-gates-and-notes).

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
step that introduced it; also when more than half of the answer's letters and
digits stand under no claim (`answer_mostly_unclaimed`), or more than one
artifact is marked `final_answer` (`multiple_final_answers`). **2** — the audit
could not run at all: a malformed file, a missing key, a trace with no claims
posted on it yet; also when the only claims that did not close are ones the
audit could not walk to the end (`chain_too_deep`, a chain deeper than 256
hops). A file that cannot be read must never read as "this agent failed". Each
code in full, with the half line and what it cost on the benchmark's posted
traces: [exit codes](docs/known-limitations.md#exit-codes-in-full).

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

Exit code 2 is for what stops the audit from running — a malformed trace, a missing SDK or key, a proposer that crashed. A bad API key must never read as "books do not balance". A file that breaks Python itself first need not end in 2: see the item on nesting under [Known limitations](docs/known-limitations.md#known-limitations).

The proposer's output is a file. Two proposer runs may differ; two audits of the same file never do. That boundary is physical: `tallystick/propose/` is the only package allowed to import an SDK, and nothing in the verdict path may import `propose/` (`cli.py` may, lazily, inside the `propose` subcommand only). A test walks the AST and fails the build on either, for every spelling of the import it knows, including the ones that once slipped past it.

```python
from tallystick import audit

balance = audit("run.json")
assert balance.books_balance       # drop straight into a test suite

balance.coverage                   # share of final CLAIMS that close - not of the answer
balance.laundering_rate            # share that cite something real but unfunded
balance.injection_points()         # failing claims, each with the step that broke
```

The rest of the Python API — `answer_cover` for the share of the answer under
claims that close, `ClaimStatus.UNCHECKED`, what `audit()` raises and what it
does not check — is in
[docs/known-limitations.md](docs/known-limitations.md#the-python-api-in-full).

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
will most likely find more. The full list, with a measurement or a command
for most entries, is in
[docs/known-limitations.md](docs/known-limitations.md#known-limitations). The tool refuses what that file describes as
refused; anything else, it may accept.

In short:

- The quote check reads where a span was cut, and refuses a boundary inside a
  number or a word as it reads them. The shapes it still lets through, and the
  honest quotes it refuses, are listed with how often each occurs in the
  benchmark texts: [forgeries let through](docs/known-limitations.md#forgeries-the-quote-check-lets-through),
  [honest quotes refused](docs/known-limitations.md#honest-quotes-the-quote-check-refuses).
- Some of the model's own text still passes as evidence with no demotion and
  no note — a short note read back inside a store's record, a draft pasted
  back as a `user` message:
  [what still passes](docs/known-limitations.md#the-models-text-that-still-passes-as-evidence-with-no-demotion-and-no-note).
- In places the reading is stricter than it needs to be:
  [where](docs/known-limitations.md#where-the-reading-is-stricter-than-it-needs-to-be).
- What each command says and where, a nesting limit of the reader's own, and
  two shapes open since v0.8.1:
  [the commands](docs/known-limitations.md#what-the-commands-say-and-where).

How the numbers were made, what a fresh clone can recompute and what it
cannot: [reproducing the numbers](docs/known-limitations.md#reproducing-the-numbers).

## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it.
- Writing and attacking are different jobs. Again and again in this repo, the serious defects were found not while writing but in a separate adversarial review whose only brief was to break it — and fixes got the same treatment.
- Decide what the table means before it exists. The caveats and the three rows were published before a single live call, so when the number came in against tallystick on F1 there was nothing to negotiate.

More, with the cases behind each: [docs/design.md](docs/design.md#lessons).

## Next Steps

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
