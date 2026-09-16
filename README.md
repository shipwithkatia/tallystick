# tallystick

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

**In short.** An agent that summarises its sources and then answers from the summary can invent a fact in the middle and quote it faithfully at the end; every one-hop check then says "grounded". tallystick walks the chain back to the documents with plain code — no model in the verdict — and names the step where it breaks. On 290 answer sentences built from RAGTruth, that audit is level with an LLM judge shown the full history on F1 (0.59 vs 0.58; the paired difference spans zero) and puts a false flag on fewer than half as many clean sentences (FPR 0.07 vs 0.16). The benchmark is constructed, not natural; its limits are stated under [Benchmark](#benchmark). On 225 real agent trajectories from [AgentHallu](https://arxiv.org/abs/2601.06818), the same audit names the labelled step in 15% of the runs it can reach and reports a break in 57% — and measures its own boundary: 53% of the human-labelled hallucinations are inside tool results the trace never kept, where no post-hoc audit can follow.

- **About half of the labelled mistakes in real agent runs happen at steps this audit cannot check.** On [AgentHallu](https://arxiv.org/abs/2601.06818), 236 of the 443 human labels point at a step where the agent wrote no prose — only a tool call and the result it came back with. tallystick checks what a model wrote, so those steps are beyond its reach. That boundary is measured, not argued, and so is the one way to argue with it: if a tool call's own text counts as something to check, the share is 32% rather than 53% ([`bench/results/boundary-sensitivity.txt`](bench/results/boundary-sensitivity.txt)).
- **Plain code is level with an LLM judge on F1: fewer false flags, fewer finds.** On 290 answer sentences built from RAGTruth: F1 0.59 against 0.58, a paired difference that spans zero; a false flag on 7% of clean sentences against 16%, and 61% of the invented sentences found against 81%. The benchmark is constructed, not natural; its limits are under [Benchmark](#benchmark).
- **On 225 real trajectories, it detects a break more often than it finds the step.** Where the hallucination is in the agent's own text, it flags the answer in 57% of runs and names the labelled step in 15%. It also flags 35% of clean runs.

![One hop is not enough: the answer quotes the summary, the summary invented a sentence, and the chain breaks at the summarise step](docs/chain.svg)

**Status:** research prototype, v0.7.x. Interfaces may change between versions. Every number in this README names the run it came from; the per-trace row files are committed under `bench/results/`, and the limits of each measurement are stated next to it.

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

### Before the audit: can this trace be audited at all?

An audit of provenance can only work on what the recording kept, and the thing it
most often did not keep is the page behind a tool result. `tallystick check-trace`
reads a raw trace and says so before a single model call is spent:

```
$ tallystick check-trace examples/logs/laundered_search.json

Read as openai: 7 artifact(s) from examples/logs/laundered_search.json. The report below judges that reading.
  undeclared tools: 2 result(s) from 2 tool name(s) nobody declared (save_note, web_search) - counted, not blocked

Can this run be checked?   7 pieces of text across 5 steps
----------------------------------------------------------------------------
  A check starts at the answer and walks back until it reaches text the
  model did not write. This run gives it 5 pieces of text to walk through:

      3 of 5   (60%)   the model's own words - a plan, a summary,
                       a note. The check can ask each one what it
                       rests on, and keep walking back.

      2 of 5           a tool's reply - a search result, a page, a
                       row from a table. The walk stops here and
                       takes the tool's word for it.

  2 further pieces of text are documents kept word for word - 100 characters.
  Reaching one of those is how a walk is meant to end, so they are not
  part of the 5 above and count neither for you nor against you.

  The higher that 60% is, the more of the run a check can follow.

CAN BE CHECKED - nothing in the recording is in the way, but only 60% of what
the walk goes through is the model's own words.
  2 steps recorded only a tool's reply and nothing the model wrote,
  so there is nothing to ask at those steps. They are:
    a2.tools, a4.tools
  For scale: across 443 runs where a person marked where the agent went
  wrong, runs below 80% had that point out of reach 59% of the time,
  against 29% above it - mostly because there is more out of reach to
  begin with, not because a low number predicts trouble.
```

Three things are reported and they are not the same kind of thing. The **share** is
a fact about the recording: of the pieces of text a chain passes through or stops at,
how many hold the model's own words rather than a tool's output. It is counted over
artifacts, not steps, because a step-based fraction measures the recorder — one
agent turn logged as a single step and the same turn logged as two score
differently, while the artifact count cannot change: artifacts are texts, and
how turns are grouped into steps does not change how many there are. It decides nothing unless you ask with `--min-reachable`, because a run
that leans on tools is not broken; it is a run whose clean audit means less. The
**defects** decide the verdict and each names something a recorder can fix: model
text from a step that declares no inputs (nothing it wrote can ever be funded), text
no step admits to producing, an empty or truncated artifact, more than one answer,
or none. The **notes** are worth knowing and are nobody's bug — above all two
artifacts holding the same string, which real agents produce constantly and which
breaks any tooling that matches artifacts by their text. Exit 0 when no defect
stands in the way, 1 when one does or the recording is thinner than a
`--min-reachable` you passed, 2 when the file cannot be read.

The three verdicts say what they license you to conclude. **CAN BE CHECKED**: nothing
in the recording stops the audit; a clean result from it means something. **PARTLY**:
the audit will run, and its silence will not mean much — read it as "nothing found
here", not "nothing here". **CANNOT BE CHECKED**: there is no answer to work back
from, or nothing the model wrote, so the audit has no question to ask. Exit 1 covers the
last two, because the distinction that matters to a build is "can I trust a clean
result", and the answer for both is no.

Because `check-trace` needs no model, the 80% line could be measured on the whole of
AgentHallu rather than the subset a paid run could afford —
[`bench/auditability_agenthallu.py`](bench/auditability_agenthallu.py), output in
[`bench/results/auditability-agenthallu.txt`](bench/results/auditability-agenthallu.txt).
The corpus is not in this repository: `git clone https://github.com/liuxuannan/AgentHallu`
and pass the folder inside the clone, `--data AgentHallu/AgentHallu`, which holds one
folder per framework; given any other folder the script says it found nothing and exits 2.
Of the 443 labelled trajectories, those at or above 80% have the hallucination beyond
the audit's reach in 24 of 84 runs (29%); those below it, in 212 of 359 (59%).

Three things are true about that table and they belong together.

The share itself is counting: it says how much of a run the audit cannot look at.

**The banding is mostly arithmetic, and the script proves that against itself.** Swap
the human label for a step drawn at random from the same trajectory — a label that
knows nothing about where the hallucination is — and the same ordering appears in
every one of the five draws the script prints, 13% above the line against 46% below
on average (7–15% against 44–47% across the draws), because a trace with more
tool-only steps makes *any* step more likely to be tool-only. So "below the line, more hallucinations are out of reach" is largely a
restatement of "below the line, more of everything is out of reach", and the
significance test the script prints rejects for the placebo too, which is why it is
printed beside it rather than quoted alone.

**But the real labels are not the placebo.** They sit at a tool boundary 236 times
where each trace's own composition predicts 172 — 1.37×. In a within-trace Monte Carlo
that draws each trace at its own rate of tool-only steps, none of 20,000 draws reached
236; that is the floor of what 20,000 draws can resolve rather than a measured p-value,
and it is not a permutation test — nothing is shuffled, each trace draws its own coin.
(The framework-stratified test printed beside the placebo *is* a permutation test, and
it rejects for the placebo too, which is why it is never quoted alone.)
The enrichment is largest in the traces with the *highest* share
(24 against 8.8, 2.7×). Real hallucinations do land at tool boundaries more often than
chance puts them. That is a fact about agents rather than about this number, and it is
why the boundary is worth measuring at all. The cut is not held out, and it decides
nothing unless you pass `--min-reachable`.

[`docs/auditable-traces.md`](docs/auditable-traces.md) is the specification behind
it: what a trace has to contain, in the order it matters, framework-agnostic and
asking nothing of tallystick.

## How It Works

**Artifacts** are either roots (documents, tool results — text that entered the run from outside) or derived (summaries, plans, memory writes, the answer — text a model wrote). A credit against a root terminates a chain. A credit against a derived artifact is a promissory note: the claims of *that* artifact covering the cited span must be redeemed in turn, recursively.

**The conservation rule** is the gate that makes this a trace auditor rather than another span matcher: a step may only cite artifacts it received as inputs. Reachability is checked without reading any text.

**The recorder recovers inputs from the prompt.** No framework logs what a model call could see, so the LangChain adapter takes the only honest source: when a model starts, its prompt is kept; when it ends, every artifact recorded so far whose content appears verbatim in that prompt is an input of the step. "Was retrieved earlier" is not "was seen". Matching is longest-first with each match blanked out, so a document nested inside another is credited only when it appears on its own, and duplicates are credited once. Root artifacts under 20 characters are never matched (a three-word tool result would appear in any prompt by coincidence); model outputs always are. If a framework reformats a document before prompting — truncates it, re-wraps it — the recorder will not find it, the step gets no input, and its claims fail reachability. That fails closed, on purpose, and is the adapter's main limitation.

**Two rules keep the walk honest.** A citation into a derived artifact inherits *every* claim it covers — quoting the whole summary does not launder the one bad sentence in it (and since v0.5.3 the proposer's split of one quote into several entries is bound into a group that closes on its worst member — see Tradeoffs). And the cited span must be fully accounted for by claims: text nobody vouched for cannot fund anything.

**The proposer is a pointer generator, not a judge.** It is asked for claims and quotes as verbatim substrings of text it was shown. Each one is then *located*: exact substring search first, then (since v0.6) a match on the sequence of words that forgives punctuation, spacing, case and quote or dash variants and never a changed letter, digit or symbol — `14%` does not find `15%` or `14`. Whatever cannot be located is logged in the posted file and dropped. A claim with no locatable support is posted as `PRIOR:model`, which never funds anything, and a quote into text no claim vouches for is refused — so an invented sentence fails closed downstream, whether or not the segmenter picked it up. The model never answers "is this faithful?"; it only answers "where?", and the answer is checked.

**Two searches back the model up (v0.6).** A sentence of an intermediate artifact the segmenter did not return is posted as a claim anyway — *coverage* — so text a later step quotes always has an account, funded or not; the final answer is not covered, because its segmenter decides what the audit checks and a hedge it skipped is not a claim to fund. And for every claim, each source of the step is searched for the claim's own text; a word-for-word hit is posted as a credit marked `proposed_by: "verbatim"`, ahead of whatever the model offers. Both are substring searches, not judgements, and both are counted in the posted file's `_proposal` block.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every verdict-path module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping. Found while reviewing the v0.5.2 benchmark results: the proposer never posted a wide span. It snapped a quote that covers two summary claims into one credit per claim, and a claim with several credits closes if any one does — so on proposer-posted files the all-of rule was bypassed by the pipeline that feeds it, and a sentence made of one true clause and one invented clause closed on the true one. The rule was right; the pipeline undercut it. v0.5.3 closes it with **entry groups**: the entries that came from one quote share a `group` id, and the ledger closes a group only if every member closes — the quote inherits the worst claim it covered, as a single wide entry would — while independent entries still close on the best (a real second source is a real second source). Hand-written traces without groups are unchanged. What stays open: a narrow second quote that is a sub-span of the group's own quote still closes the claim on its own, under the independent-entries rule — the same where-not-whether cost stated under Benchmark. The v0.5.2 table was measured with the leak in place; the v0.5.3 rerun that followed is in `bench/HISTORY.md`.
- **The model proposes into a file; the verdict reads the file.** Segmenting text into claims and proposing credits are model tasks by nature. Putting them in a separate package with a materialised output means the audit stays reproducible byte for byte and the model's work can be inspected, diffed and re-run without touching the verdict. The cost is one extra artefact on disk and one more command.
- **Verbatim or nothing on the model side.** The proposer could have been allowed to paraphrase. That would have raised recall and quietly reintroduced a judgement call into the credit. Every span the proposer posts is located word for word (punctuation, spacing and case at most — see the next point); paraphrase is dropped and logged. The verifier, which also audits hand-written traces, separately allows 2% edit distance for typographic drift with no floor — two rules, both stated.
- **v0.6: the false flags had causes on disk, so they were fixed on disk.** `bench/diagnose.py` re-audits a run's posted files offline and sorts every false flag by the reason the chain broke. At v0.5.3 the top causes were not paraphrase: a credit the model never offered, a quote that straddled the summary's claim boundaries, and a quote dropped for punctuation the model had added or left off. Each got a deterministic fix — the word-level locate, coverage claims for intermediate artifacts, the self-evident credit. Two were refused: a character-level tolerance, because a locate that forgave one character would let `14` find `15`; and coverage on the final answer, because it turned skipped hedges into flagged claims. What the fixes did to the table is under Benchmark. The full diagnosis, the holes review found in the first cut, and the one fix that overshot on real trajectories are in [`bench/HISTORY.md`](bench/HISTORY.md).
- **Credits into a summary are resolved by containment, not by trimming.** The segmenter is told to skip meta text ("In summary,"); a quote that straddles such text would otherwise cover characters no claim owns, and the audit would report laundering that isn't there. The first fix trimmed the quote to whatever claims it touched — and review showed that a quote clipping two characters of a real claim was then rewritten into those two characters and *counted*. The rule now has no thresholds: a quote funds a claim only if one contains the other. A quote inside a claim is kept; a claim inside a quote is kept (one credit per whole claim, meta text discarded and logged); a partial straddle is refused as ambiguous.
- **No tolerance floor on quote matching.** A 2% edit tolerance for typographic drift with a minimum of one edit meant `14%` could be cited as `44%` and pass. The floor is gone; a three-character quote gets no free edit.
- **An empty trace does not balance.** `all([])` is `True` in Python, so a truncated trace originally exited 0. A gate that passes on missing input is not a gate.
- **Exit 2 is a hard boundary.** Seven ordinary malformed-JSON shapes originally escaped the loader as `KeyError`/`TypeError` and reached the shell as exit 1 — "your agent is unfaithful" — when the truth was "I could not read your file". The loader now validates every shape and raises one typed error, and the same validation runs on a `Run` built by hand.


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
  shipped: against every message the model wrote, 19 new notes and not one was
  the model's text handed back (a tool returning a file the model had quoted, a
  search repeating the question); with text the model had copied from outside
  set aside, 4 notes, none of them either (a search answering in the words of
  the model's plan). Read by hand; the labels are not in this repository. The
  note that shipped fires on none of the 693 AgentHallu trajectories, so what
  it is worth on real logs is not measured.
- **A note written straight onto other letters with no mark between**, in
  Chinese, Japanese, Thai, Lao, Khmer, Myanmar or Tibetan; **a value read back in
  another case**; **a bidirectional mark (RLM, LRM) after every word**, in any
  language.
- **A note in a script written without spaces that is not in the reader's
  list, read back inside anything** — JSON, `Note: "..."`, brackets. Javanese,
  Balinese, Sundanese, Buginese, New Tai Lue, Tai Le, Tai Tham, Tai Viet, Cham,
  Yi, Phags-pa, Han outside the basic blocks (extension B and later), Hangul
  written without spaces, Ethiopic with its word mark `፡`. A reply that is the
  bare note is still found. `tests/test_proverka20_unlisted_scripts.py` shows
  the missed shapes (it fails on them). No noise was found in their honest
  chats, but nothing was measured on real logs in these scripts.
- **The model's draft pasted back as a `user` message** ("here is your previous
  draft"), or a subagent's answer handed over as one: every `user` message is a
  root, and nothing compares it with the model's text. A rule to close it was
  built, measured and not shipped: read by hand, more than a third of what it
  demoted was honest material.

**Where the reading is stricter than it needs to be**

- **A page is demoted when one of its lines is one word the call carried.**
  The demotion reads every line of a reply as a value, and a word of the call's
  arguments is a value: a browser page with a line `Awards` after a `click`
  whose reasoning said "Awards" is recorded as the model's text. That costs a
  claim a source it had; it never makes the model's text evidence. Round 21 read
  30 demotions on AgentHallu by hand (the ones call ids numbered per turn used to
  hide): 16 the model's own text, 6 disputable (`cd` answering with the
  directory it was given), 8 a tool's own page. The labels are not in this
  repository.

**What the commands say, and where**

- `convert` does not list notes; the trace it writes keeps them in `_meta`, and
  `check-trace` on that trace prints them.
- `tallystick.audit()` in Python returns the balance without the notes; read
  `_meta.echo_warning_details` or run `check-trace`.
- A log nested more deeply than Python recurses (about a thousand levels) is
  refused with exit 2, not read.
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

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it. Each hop can be individually correct while the whole chain is wrong, and only a transitive walk sees that.
- "Deterministic" is a property you have to defend structurally. Memoising a result computed while a cycle was open made the verdict depend on array order in the JSON file. Same run, same code, different exit code. The fix was to refuse to cache anything tainted by an open cycle, and the shuffle test now exists so it cannot regress silently. That fix had its own price, found by a later review: with nothing cached inside a loop, the walk followed every path through it, and an 11 KB trace kept `audit` busy for 42 seconds. A loop of claims is now settled as a whole, best verdict first, which no order in the file and no name can move.
- A README that overstates what the code does is the single most expensive defect in a project whose thesis is "unsupported claims should be named". The adversarial review pass caught seven such lines before publish.
- **Writing and attacking are different jobs, and the same pass cannot do both.** Every serious defect in this repo was found not while writing but in a separate adversarial review run afterwards, by a reviewer whose only brief was to break it: the wide-citation bypass and the array-order dependence in v0.1; in v0.2, a false `laundered` verdict on a model that had followed the prompt correctly, a string-shaped answer that would have posted one claim per character, and three ways to sneak a model import past the boundary test. None of them were visible from inside the writing.
- **A framework tells you what happened, not what a model saw.** LangChain logs retrievals, tool calls and model outputs, but nothing in it says which of those a given model call had in its context. The recorder recovers that from the prompt bytes, and the first review showed how easy it is to get subtly wrong: a 20-character floor meant for tiny tool results was also dropping short model outputs from the chain, and chat "content blocks" were being JSON-escaped so no multi-line document ever matched. Both mislocated the injection step — the one number the project promises — while every test stayed green, because the demo happened to use one long paragraph.
- **A fix needs its own review.** The fix for the false `laundered` verdict passed its tests and was wrong in both directions — it still failed when the meta text sat *between* two claims, and it turned a mostly-unvouched quote into a two-character credit that counted. The tests I wrote for the fix tested the case I had in mind, not the cases I had not. A third pass, scoped to the fixes only, caught it. The rule this repo now follows: nothing ships without a second pass whose job is to make the first one look bad, and fixes get the same treatment as the code they fix.
- **Decide what the table means before it exists.** The benchmark's caveats, the three rows and the sentence "The table goes here when it exists, whatever it says" were all written and published before a single live call. When the number came in against tallystick on F1, there was nothing to negotiate: the README already said what would be reported and how. A number that could have been adjusted after the fact would have been worth less than the one that couldn't.
- **The failure you get is not the one you tested for.** The first live run crashed at trace 12 on a line I had added the same day to *handle* failures — a progress print that assumed the thing it was printing existed. Then 7 of 198 proposer runs failed and were dropped — the ones I inspected were malformed JSON (a missing comma inside the object), which no amount of trailing-text tolerance fixes. One was a harness bug, one a model-output bug; neither touched the audit, and both cost a rerun.
- **The worst failure is the one that prints a plausible table.** At the v0.5.3 rerun, `run.py` found the v0.5.2 rows file, checked that the model and run counts matched — they did — and resumed: 99 old rows plus one new trace, followed by a complete, well-formatted report. Nothing crashed. The resume check tested the settings and not the data, because when I wrote it the data could not change. Rows now carry a fingerprint of their trace, and a mismatch refuses to resume.
- **Sort the errors by cause before touching the model.** At v0.5.3, one clean sentence in six was flagged, and the obvious reading was "the proposer paraphrases". An offline pass over the posted files said otherwise: the top causes were a credit the model did not bother to offer for text sitting verbatim in its source, a sentence the segmenter skipped, and a full stop the model added — in that order. Three searches, no new judgement anywhere, and the false flags halved on the same sentences. The paraphrase problem is real — it is most of what is left — but it was a third of the story, not the story.

## Next Steps

Done so far: v0.2 model-side proposers outside the verdict path; v0.3 LangChain recorder; v0.5–v0.5.3 the benchmark, its intervals and the entry-group fix; v0.6 precision from the false-flag diagnosis; v0.7 the AgentHallu adapter and harness. The version-by-version record, with what each one cost, is in [`bench/HISTORY.md`](bench/HISTORY.md).

- [x] v0.7.3 run — 228 AgentHallu trajectories (115 labelled, of which 61 at a tool-result boundary; 113 clean), CodeAct runs excluded and counted. Two runs were withdrawn in review before publication, and three cheap changes were measured on the clean rerun before one was adopted; both stories, with numbers, are in `bench/HISTORY.md`
- [ ] a second retry when the proposer returns malformed or empty JSON: runs were lost to it at v0.7.0 and v0.7.2, and the run directories that counted them are not in this repository
- [x] a pre-flight auditability check (`check-trace`): what share of the artifacts a chain passes through hold the model's own words, and which defects a recorder can fix, so a trace is told whether it can be audited before anything is audited. Measured on all 443 labelled AgentHallu trajectories at no cost, since it calls no model: 29% unreachable above the line against 59% below, most of that gap arithmetic rather than prediction — though real labels do sit at tool boundaries more often than the placebo (see Real trajectories)
- [ ] an adapter for the log shapes practitioners already have — OpenAI Chat Completions message lists first, then OpenTelemetry GenAI spans — so that `check-trace` can be run on a real recording without writing a converter by hand. This is the gap between the tool and its first user
- [ ] v0.8 — the recall gap is the verifier checking *where*, not *whether*. On real trajectories 5 of 23 reachable misses are a real source misread (a 1946 date taken for a 1937 one), which is exactly this. One deterministic candidate: require the funding span to contain the claim's content words, measured on this benchmark before it is adopted. Separately: let the proposer post a paraphrase together with the verbatim span behind it, and verify the span
- [ ] coverage for the remainder of a sentence the segmenter claimed only in part ("Revenue rose, and the CEO resigned" with a claim over the first clause): today the remainder is logged as uncredited characters, not posted as a claim
- [ ] adapters: LangSmith exports, and the OTel reader checked against real exports rather than the specification alone — and a benchmark on such traces, three or more steps, where the last hop is not verbatim by construction; LlamaIndex; Claude Citations ingested as pre-verified credits
- [ ] HTML ledger view: the answer colour-coded by status, click a sentence to unfold its chain to the root
- [ ] PyPI release

## Benchmark

When this benchmark was built, no multi-step dataset with sentence-level labels of unsupported claims was available — the hallucination corpora are one hop, and [AgentHallu](https://arxiv.org/abs/2601.06818) (2026), which does have real multi-step trajectories, labels the responsible *step*, not the sentence; it is the basis of the v0.7 run below. So `bench/build.py` constructs two-hop traces from RAGTruth (test split, Summary and QA tasks, human-annotated hallucinated spans; MIT) without any model: the RAGTruth response becomes the intermediate summary, and a final answer is built by quoting up to three of its sentences, chosen uniformly at random, verbatim. Selection and quoting are seeded (`--seed`, default 7): the item shuffle takes the seed, and each trace's sentence choice is seeded per item, so a trace is byte-identical whatever `--limit` built it, and `--limit N` takes a random prefix of one fixed order rather than a different sample. The seed is recorded in `manifest.json`. Ground truth follows from the annotations alone — a quoted sentence that overlaps an annotated span is laundered, one that overlaps none is grounded.

Four things the reader should know before the number (the first two, and the per-side failure counts behind the fourth, are also printed in the report):

- **The last hop is trivially verifiable.** The answer quotes the summary verbatim, so tallystick's answer-level result is the summary-step detection carried through the chain, not new detection power. What the construction shows is *where a one-hop check breaks*: a judge shown only summary + answer scores ~0 recall by construction. That row is scored anyway, because it is the point.
- **Prevalence is enriched.** Items with at least one annotated hallucination are oversampled to 60%; within an item, sentence choice is uniform. The report prints the constructed and the natural sentence-level prevalence side by side.
- **Data2txt is excluded** — a third of the split and the densest in hallucinations — because its sources are structured records, not prose a model could quote.
- **Dropped traces are dropped by tallystick's side.** A trace is scored only if every run on every side succeeded, and at the v0.5.2 run every failure was the proposer's (7 proposer runs, 0 judge runs), so the scored set is conditioned on tallystick having run. At n=100 the 6 dropped traces were all Summary items, 18 answer sentences between them and 1 laundered (5.6%, against 15.8% kept) — mostly clean summaries on which the model returned malformed JSON, not hard cases. `bench/ci.py` prints the dropped-set prevalence for any run.

`bench/run.py` compares three things at the sentence level: a **one-hop judge** (summary + answer only, the industry default), a **full-history judge** (same model, shown every document, the summary and the numbered answer — the strongest thing a team can do today without a provenance tool), and **tallystick**. Every side is rerun — judges `--judge-runs` times, the proposer `--proposer-runs` times, all at API-default sampling — so tallystick's end-to-end spread is reported next to the fact that auditing one posted file twice is identical. Failures are counted per side; a trace on which any run on any side failed is dropped from all sides, so every row and every run is scored on exactly the same sentences, and that count is printed. Results are appended per trace, and rerunning the same command resumes; every row carries a fingerprint of the trace it was scored on, and a rows file from a different build of the traces is refused rather than resumed. A fourth number, hallucination detection at the summary step, is the classic task where fine-tuned detectors live; tallystick's known false-positive source there (an abstractive sentence fusing two passages has no single verbatim quote) is stated in the report.

An offline test drives the whole harness with an oracle proposer scripted from the labels and requires F1 = 1.0 on both levels, so a live number measures the proposer, not the plumbing.

```bash
python bench/build.py --limit 100     # clones RAGTruth, builds traces + manifest, no model
python bench/run.py --dry-run         # counts calls and estimates cost
python bench/run.py --limit 100       # needs ANTHROPIC_API_KEY; writes bench/work/RESULTS.md
```

### Earlier runs

Two earlier runs on this benchmark — v0.5.2 (the first number, the LLM judge ahead by 0.14) and v0.5.3 (the fix that closed a leak, recall up, precision down, F1 unchanged) — are kept in full, tables, intervals and what each change cost, in [`bench/HISTORY.md`](bench/HISTORY.md). Their row files are in `bench/results/`. The current table is below and is measured on the same sentences as v0.5.3.

### Results — v0.6 (September 2026)

Same command, **same traces as v0.5.3** — not rebuilt, so this is the first pair of tables on identical sentences: the 99 scored traces are the same files with identical labels in both row files (the v0.5.3 rows predate the trace fingerprint, so identity is by file name and label, not hash), and `run.py` now refuses to resume rows written by another tallystick version. Model on every side: `claude-sonnet-4-6`, API-default sampling. Rows: `bench/results/2026-09-n100-v0.6.json`.

| method | precision | recall | F1 | FPR | variance |
|---|---|---|---|---|---|
| one-hop judge (summary + answer only) | 0.12 | 0.03 | 0.05 | 0.04 | 3 runs, F1 spread 0.04, 2% of sentences flip |
| full-history judge (documents + summary + answer) | 0.45 | 0.81 | 0.58 | 0.16 | 3 runs, F1 spread 0.03, 5% of sentences flip |
| tallystick (propose + audit) | 0.57 | 0.61 | 0.59 | 0.07 | 2 runs, F1 spread 0.00, 5% of sentences flip; audit of one posted file: identical |

Bootstrap over traces (`bench/ci.py`, 2000 resamples): 95% intervals on F1 — one-hop judge [0.00, 0.13], full-history judge [0.47, 0.67], tallystick [0.47, 0.69]. Paired difference full-history judge − tallystick: −0.01, 95% CI [−0.12, 0.09], identical across seeds 0–4; about 60% of resamples put tallystick at or ahead. Summary-step detection (claim level, first proposer run): precision 0.39, recall 0.62, F1 0.48 over 759 claims. Across both proposer runs, 2223 claims were posted (140 of them by coverage, not the model), 2715 credits (793 found by search, not the model; 352 claims or quotes located only by the word-level match) and 301 prior-only claims.

**What changed from v0.5.3, on the same 290 sentences.**

- **False flags fell by more than half; true flags fell by three in one run and none in the other.** Per proposer run, tallystick's false flags went from 43 and 43 to 17 and 20, and its true flags from 27 and 25 of 40 to 24 and 25. Precision 0.38 → 0.57, recall 0.65 → 0.61, FPR 0.17 → 0.07. The judge, on the same sentences, is where it was: 32–33 true and 37–43 false flags per run in both files. The gap to the judge (0.10 in favour of the judge at v0.5.3) is gone: the paired difference is −0.01 with an interval that spans zero either way. "Neither beats the other on F1" is the reading the data supports. So is "tallystick puts a false flag on fewer than half as many clean sentences (FPR 0.07 against 0.16)", and so is "the judge finds eight laundered sentences in ten to tallystick's six". Which of the last two matters depends on whether the gate blocks or annotates.
- **The gain came from bookkeeping.** `bench/diagnose.py` sorts a run's false flags by the reason the chain broke, reading the posted files the proposer wrote. Those files are not in this repository, and a new proposer run would not write the same ones, so its counts cannot be recomputed from here and are not quoted. What it showed, in words: the three causes v0.6 targeted — no credit offered, a quote that straddled the summary's claim boundaries, a quote dropped for punctuation — were most of the v0.5.3 false flags; after v0.6 the punctuation drops were gone, and most of what remained was a summary claim the proposer offered no credit for — a paraphrase of a passage, or a hedge or abstention ("this passage does not address…"), that the annotators do not mark. That is a paraphrase problem, not a locate problem, and nothing deterministic in this design addresses it.
- **Recall slipped by a net three sentences in one run and none in the other** — six flags lost on four sentences, three gained on two. The likeliest reason, from reading the lost ones: at v0.5.3 a quote the pipeline could not locate happened to sit on a laundered sentence, so the flag was right by accident; those quotes locate now and the flag depends on the summary hop like every other. That is a reading of four cases, not a measurement. The misses (31 across two runs, 28 at v0.5.3) are the same shape as before: 28 close as grounded at depth 2, the verifier accepting a real document span that does not support the claim it funds — the *where*-not-*whether* limit stated under Tradeoffs and, first, in the v0.5.2 discussion in `bench/HISTORY.md`.
- **The summary-step number got worse, and that is a cost of v0.6.** Claim-level precision on the summary fell 0.49 → 0.39 over 759 claims in the first run (590 at v0.5.3). Two things added claims: 70 coverage claims per run over sentences the segmenter skipped — closing lines, hedges, list tails — which the proposer can rarely fund and which, on inspection of the posted files, the annotators mostly left unmarked; and roughly 80–100 more model claims per run, most plausibly ones the word-level locate now keeps instead of dropping (`tolerant_locates` counts claims and quotes together, and the two v0.5.3 runs already differ by 14 claims from segmenter variance, so this is an inference). The rows do not mark which is which, so the split of the 45 extra false flags between them is not measured. Coverage claims exist so that an answer quote always has an account to land on; together with the self-evident credit and the locate they are what cut the false flags on the chain, and at the summary step they are noise. Both numbers are reported because they pull in opposite directions.
- **Two proposer runs now agree on F1 to two decimals** (spread 0.004, was 0.03), and 5% of sentences flip between runs (was 8%). Less of the posting is the model's: coverage, self-evident credits and the word-level locate are searches, and searches do not vary. The judge's flip rate at identical settings is also 5% this run (7% at v0.5.3).

### Real trajectories: AgentHallu (v0.7.3, September 2026)

Everything above is on traces `build.py` constructed. [AgentHallu](https://arxiv.org/abs/2601.06818) (Liu et al., 2026; CC BY 4.0) is 693 real runs of seven agent frameworks — SmolAgents, OpenDeepSearch, OpenManus, Magentic-One, OWL/Camel, OctoTools, function-calling agents — of which 443 carry a human label naming the step that introduced the hallucination and 250 are clean. `tallystick/adapters/agenthallu.py` reads a trajectory as a trace (question → document; each step's model text → intermediate; tool results → roots; the answer → final_answer; every step sees everything before it — which makes the reachability gate vacuous on this data, so what the audit tests here is the quote gate and the chain, not conservation), and `bench/agenthallu.py` posts a selection with the proposer, audits, and compares the audit's `break_step_id` with the labelled step: any final claim flagged, earliest breaking step equal to the label, within one step, and the false-alarm rate on clean runs. Credits are proposed on demand — the answer's claims first, then only the claims their credits reach — so the cost is one credit call per claim the answer rests on, not per sentence the agent ever wrote.

Two boundaries are stated before any number exists, because reading the files settled them:

- **The audit stops at a tool result, and 61 of the 115 labelled runs in the default selection are labelled at a step whose only artifacts are tool results.** OpenDeepSearch's `web_search`, above all, returns a model-written digest of pages the file does not contain, and the label sits inside that digest. There is no page in the file to check it against; no post-hoc audit of the file can reach it. Those rows are reported apart — neither hits nor misses; a flag on one is a flag on something else in the run. Tools that hand the agent's own text back — `final_answer`, Camel's notes, `terminate` — are posted as model text, not roots, or every answer would ground on itself; five runs labelled at a note-writing step are reachable for that reason and are not in the 61. (An interpreter printing the answer literal back from the model's code is treated the same way; that only occurs in CodeAct runs, excluded by default.)
- **CodeAct agents (55 of 693) call their tools from inside model-written code**, so one execution log holds a web result and a model-computed string side by side, and the file does not mark where one ends and the other begins. No reading of that log — root or model text — audits it honestly; those runs are excluded by default and counted.

The default selection is the categories where the label and the audit ask the same question — a fact stated in model text with nothing behind it: Planning/Fact Derive, Reasoning/Factual Reasoning, and the three Retrieval sub-categories — plus as many clean runs from the same frameworks as exist (OpenManus has 20 for its 22): 115 labelled and 113 clean, 228 trajectories, roughly $17–34 of proposer calls at the first attempt — an estimate from input tokens alone, assuming about six credit calls per trajectory. Two earlier runs were withdrawn in review; the published run answered most questions from their posted files through `--reuse` and sent only what was new to the model, so it cost a small fraction of that. The harness prints the split but does not record it, so the exact number of live calls is not on file. The table reports, for the reachable rows, whether any final claim was flagged, whether the earliest breaking step is the labelled one, whether the labelled step is among the breaks at all (an unfunded hedge quoted from an earlier step drags the earliest break forward without making the audit wrong about the labelled step), and within one step. AgentHallu's abstract reports its best model judge localising the step in 41.1% of cases over all categories; that number is on a different set and a different question, and will be quoted next to ours only with that said. What went wrong in each withdrawn run, and how it was fixed, is in [`bench/HISTORY.md`](bench/HISTORY.md). Five trajectories are committed under `bench/sample-agenthallu/` as test fixtures, unchanged, with attribution.

**The result.** 225 of the 228 scored; 3 failed in the proposer and are excluded. Rows are in [`bench/results/agenthallu-v0.7.3-rows.jsonl`](bench/results/agenthallu-v0.7.3-rows.jsonl), the report in [`bench/results/agenthallu-v0.7.3.md`](bench/results/agenthallu-v0.7.3.md), the false-alarm diagnosis in [`bench/results/diagnose-agenthallu-v0.7.3.txt`](bench/results/diagnose-agenthallu-v0.7.3.txt).

| set | n | any final claim flagged | earliest break = labelled step | labelled step among the breaks | within one step |
|---|---|---|---|---|---|
| hallucinated, label in model text (reachable) | 54 | 31/54 (57%) | 8/54 (15%) | 8/54 (15%) | 11/54 (20%) |
| hallucinated, label at a tool-only step (beyond the audit's boundary) | 61 | 7/61 (11%) | — | — | — |
| clean | 110 | 38/110 (35%) (false alarms) | — | — | — |

| category / sub-category | n | flagged | exact step | label among breaks | within one |
|---|---|---|---|---|---|
| Planning Hallucination / Fact Derive | 34 | 18 | 4 | 4 | 4 |
| Reasoning Hallucination / Factual Reasoning | 11 | 5 | 3 | 3 | 4 |
| Retrieval Hallucination / Context Misalign | 3 | 2 | 1 | 1 | 1 |
| Retrieval Hallucination / Query Misalign | 1 | 1 | 0 | 0 | 1 |
| Retrieval Hallucination / Summarize Misalign | 5 | 5 | 0 | 0 | 1 |

Read the first row as: where the hallucination was stated in the agent's own prose, the audit reported *something in this answer rests on nothing* in 57% of runs, and the step it named was the labelled one in 15% — against 35% of clean runs flagged, so the margin is real but modest (Fisher exact p = 0.007). Detection of a break is the stronger half; localisation is weak, and the sub-category table says where each comes from — the three Retrieval rows are flagged in 8 of 9 runs — too few to put a rate on — and the two larger reasoning categories in about half. AgentHallu's own best model judge localises the step in 41.1% of cases over all categories; that is a different selection, a different question, and a model judge rather than plain code, so it is context, not a comparison.

**What the numbers mean, three readings.**

- **The boundary is the finding, not the footnote.** 61 of the 115 labelled runs — 53% — are labelled at a step whose only artifacts are tool results. No post-hoc audit of the file can reach them: the page the digest came from is not in the file. That is a statement about what a trace has to contain for provenance to be checkable at all, and it is measured, not argued. It also tracks something the file carries without any label, and `tallystick check-trace` reports that quantity. Measured on all 443 labelled trajectories in the dataset — which costs nothing, since that check reads the file and calls no model: where at least 80% of the artifacts a chain passes through hold the model's own words rather than a tool's output, the hallucination is beyond the boundary in 24 of 84 runs (29%); below that line, in 212 of 359 (59%). Most of that gap is arithmetic rather than prediction — a placebo label, a step picked at random from the same run, reproduces the ordering — so read it first as "a thin recording has more of itself out of reach", which is what makes a clean audit of one worth less. Real labels do sit at tool boundaries more often than the placebo (236 against an expected 172), which is a fact about agents rather than about the share.

  The 53% rests on one definition — a labelled step whose only recorded artifacts are tool results — and that definition is worth arguing with, so the argument is measured rather than left open ([`bench/boundary_sensitivity.py`](bench/boundary_sensitivity.py), output in [`bench/results/boundary-sensitivity.txt`](bench/results/boundary-sensitivity.txt)). The objection that moves it: the model wrote the tool call at that step, so a reader can say there *is* model text there. Every one of the 236 labelled steps beyond the boundary carries a call, and 96 of them carry more than 200 characters of it; counting each of those as reachable takes the share from 53% to 32% on the whole corpus, and from 53% to 43% on the 115 scored here. Read next to what the calls say, the objection is weak — `{"query": "which two ASEAN capitals are furthest apart"}` asserts nothing that could be checked against a source — but weak is not unmeasured, so treat the finding as **32–53%**, not as one number. The other two objections move it the other way: reading `final_answer`, note stores and `terminate` as model text costs 12 traces out of the boundary set, and including the CodeAct runs gives 53.3% where excluding them gives 57.3%, so the published figure is already the lower of the two. Nothing here checks AgentHallu's labels themselves.
- **23 reachable misses, and they are not one failure.** 16 of the 23 are labelled at step 1 — the plan. The agent read the question wrong or chose the wrong rule before doing anything, and everything after it, the answer included, is faithfully derived from that choice. The chain to a root is intact and the audit is right to close it; the error is in the reasoning, which a provenance audit does not judge. Five more are misreadings of a real source (1946 taken for 1937; the wrong actor from a cast list): the words are in the source, the meaning is not, and those need a check of *whether* a span supports a claim — the v0.8 question. The last two had no claim to check. The three groups do not overlap.
- **35% false alarms, and paraphrase is most of them.** `bench/diagnose_agenthallu.py` sorts them by why the chain broke: a paraphrase of a source, a quote the proposer offered that is not verbatim in the source, a computation or formula the agent derived, something the agent stated from its own knowledge. It reads the posted files of the run, which are not in this repository, so the counts per cause are not quoted here. Only the last cause is the audit working as designed on a claim with no external support — and AgentHallu calls those runs clean because the answer was *true*, which is the other question. The rest is the cost of verbatim verification against agents that paraphrase what they read and compute what they report.

**Two changes were measured on the previous run's files and refused** rather than shipped, with the numbers in [`bench/HISTORY.md`](bench/HISTORY.md): skipping the model's narration of itself in coverage, and requiring the claim's numbers to appear in the funding span. One was adopted: a bare-value answer the segmenter returns nothing locatable for is posted whole, which closed a silent pass on 15 answers that carried no claim at all and so could not be flagged whatever they said.

### Using the benchmark for your own detector

The traces are plain JSON with the labels inside, so anything that flags sentences can be scored on the same construction without running tallystick; the format, the scoring rule, the committed row files and the checks on the labels are described in [`bench/README.md`](bench/README.md).

## Where This Sits

| | one-hop grounding | across steps | deterministic verdict | names the step | post-hoc on any trace |
|---|---|---|---|---|---|
| Anthropic Citations API | ✅ documents only | ❌ | ✅ | ❌ | ❌ |
| RAGAS / DeepEval / Patronus faithfulness | ✅ | ❌ | ❌ | ❌ | ✅ |
| LLM-as-judge, one-hop context | ✅ | ❌ *(F1 0.05 above)* | ❌ | ❌ | ✅ |
| LLM-as-judge, full history | ✅ | ✅ *(F1 0.58 above)* | ❌ | ❌ | ✅ |
| [AgentHallu](https://arxiv.org/abs/2601.06818) (Liu et al., 2026) | — | ✅ benchmark: 693 real trajectories, step labelled by hand | ❌ judges; best model finds the step 41% of the time | ✅ | ✅ |
| [Hallucination Snowball](https://arxiv.org/abs/2608.14588) (Singh & Pawar, 2026) | — | ✅ measures escape across 4 agents | — measurement study; detection by GPT-4o and others | ❌ | — |
| [TRACER](https://arxiv.org/html/2605.09934) (Yu, Jia et al., 2026) | ✅ | ✅ dependency graph to tool turns | partly: schema checks; semantics judged by Gemini | ✅ | ❌ agent emits its own provenance |
| [CAMS](https://arxiv.org/html/2606.23989v2) (Guan, 2026) | ✅ claim-anchored spans | ❌ one step | span resolution deterministic; claims extracted by a model, faithfulness scored by NLI models | ❌ | ❌ |
| LEDGER ([arXiv 2608.18398](https://arxiv.org/abs/2608.18398)) | ✅ | ✅ | ❌ *(authors' own caveat)* | partly | ✅ |
| **tallystick** | ✅ | ✅ *(F1 0.59 above)* | ✅ audit; proposer is a model | ✅ | ✅ |

The rows other than the two judges and tallystick are my reading of public documentation (the products) and of the papers' abstracts and method sections (the papers) as of September 2026, not benchmark results; the F1 cells come from the Benchmark section.

The problem itself is not new and is now well documented: 2026 saw at least three papers on errors that arise at an intermediate agent step — AgentHallu, and two that name the effect *snowball* and *cascade*. Singh & Pawar inject errors and measure an 89% escape rate at the last of the three hand-offs in their four-agent pipeline; Jamshidi et al. ([Hallucination Cascade](https://arxiv.org/abs/2606.07937)) measure something different — the hallucination score of each agent's output in a refinement chain, no injection — and find it falls along the chain, which is worth keeping next to the first. AgentHallu is the closest in aim — find the step responsible — and benchmarks model judges at it on real trajectories; it is the data the v0.7 run is on (see Benchmark). What none of the works above do together is the three things this repository is built around: a verdict with no model in it, so the same trace gives the same answer; an audit that runs after the fact over a trace it did not produce, rather than provenance the agent writes about itself; and the conservation rule, under which a step may cite only what it received and a citation into model-written text inherits the worst claim it covers. The 2026 survey [*From Agent Traces to Trust*](https://arxiv.org/abs/2606.04990) lists claim-level provenance across execution chains and benchmarks of multi-step traces with labelled unsupported claims as open; this is a shipped, tested answer to a narrow slice of both, with its limits measured above.

Citations grounds one hop and does it well; the plan is still to ingest those citations as pre-verified credits and spend effort on the hops citations cannot see.

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
Built by Katia Engalycheva with Claude | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
