# tallystick

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

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
  coverage         66.7%
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

```bash
git clone https://github.com/shipwithkatia/tallystick && cd tallystick
pip install -e ".[propose,langchain]"   # plain `pip install -e .` gives the audit path only

# 1. Get a raw trace (artifacts + steps, no claims yet). Either record a
#    LangChain run — or write the JSON by hand, as examples/raw_research_run.json.
python examples/langchain_demo.py          # a 4-step agent, recorded -> raw_langchain.json

# 2. Let a model post the books: writes a posted trace, then audits it.
#    Needs ANTHROPIC_API_KEY.
tallystick propose raw_langchain.json -o posted.json

# 3. Audit a posted trace. Deterministic, offline, no SDK needed.
tallystick posted.json                 # exit 0 = balance, 1 = don't, 2 = could not run
tallystick posted.json --chain <id>    # full provenance chain for one claim
tallystick posted.json --json out.json # machine-readable balance
tallystick posted.json --quiet         # exit code only
tallystick examples/balanced_run.json  # what a passing gate looks like: exit 0

# Same propose path with canned answers and no network — what CI runs.
tallystick propose examples/raw_research_run.json -o posted.json \
  --proposer fake --script examples/fake_answers.json
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

balance.coverage                   # share of final claims that trace to a root
balance.laundering_rate            # share that cite something real but unfunded
balance.injection_points()         # failing claims, each with the step that broke
```

The trace format is plain JSON — artifacts, steps with inputs/outputs, claims by character span, entries — documented in `tallystick/io.py`. `examples/laundered_summary.json` is a complete posted trace; `examples/raw_research_run.json` is the same run before posting; `examples/balanced_run.json` is the same run with an honest summariser, and it balances. A `Run` can also be built in Python from the exported `Artifact`, `Step`, `Claim` and `Entry` types; it gets the same validation as a file.

## How It Works

**Artifacts** are either roots (documents, tool results — text that entered the run from outside) or derived (summaries, plans, memory writes, the answer — text a model wrote). A credit against a root terminates a chain. A credit against a derived artifact is a promissory note: the claims of *that* artifact covering the cited span must be redeemed in turn, recursively.

**The conservation rule** is the gate that makes this a trace auditor rather than another span matcher: a step may only cite artifacts it received as inputs. Reachability is checked without reading any text.

**The recorder recovers inputs from the prompt.** No framework logs what a model call could see, so the LangChain adapter takes the only honest source: when a model starts, its prompt is kept; when it ends, every artifact recorded so far whose content appears verbatim in that prompt is an input of the step. "Was retrieved earlier" is not "was seen". Matching is longest-first with each match blanked out, so a document nested inside another is credited only when it appears on its own, and duplicates are credited once. Root artifacts under 20 characters are never matched (a three-word tool result would appear in any prompt by coincidence); model outputs always are. If a framework reformats a document before prompting — truncates it, re-wraps it — the recorder will not find it, the step gets no input, and its claims fail reachability. That fails closed, on purpose, and is the adapter's main limitation.

**Two rules keep the walk honest.** A citation into a derived artifact inherits *every* claim it covers — quoting the whole summary does not launder the one bad sentence in it (and since v0.5.3 the proposer's split of one quote into several entries is bound into a group that closes on its worst member — see Tradeoffs). And the cited span must be fully accounted for by claims: text nobody vouched for cannot fund anything.

**The proposer is a pointer generator, not a judge.** It is asked for claims and quotes as verbatim substrings of text it was shown. Each one is then *located* by exact substring search; whatever cannot be located is logged in the posted file and dropped. A claim with no locatable support is posted as `PRIOR:model`, which never funds anything, and a quote into text no claim vouches for is refused — so an invented sentence fails closed downstream, whether or not the segmenter picked it up. The model never answers "is this faithful?"; it only answers "where?", and the answer is checked.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every verdict-path module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping. Found while reviewing the v0.5.2 benchmark results: the proposer never posted a wide span. It snapped a quote that covers two summary claims into one credit per claim, and a claim with several credits closes if any one does — so on proposer-posted files the all-of rule was bypassed by the pipeline that feeds it, and a sentence made of one true clause and one invented clause closed on the true one. The rule was right; the pipeline undercut it. v0.5.3 closes it with **entry groups**: the entries that came from one quote share a `group` id, and the ledger closes a group only if every member closes — the quote inherits the worst claim it covered, as a single wide entry would — while independent entries still close on the best (a real second source is a real second source). Hand-written traces without groups are unchanged. What stays open: a narrow second quote that is a sub-span of the group's own quote still closes the claim on its own, under the independent-entries rule — the same where-not-whether cost stated under Benchmark. The v0.5.2 table was measured with the leak in place; the v0.5.3 rerun is below it.
- **The model proposes into a file; the verdict reads the file.** Segmenting text into claims and proposing credits are model tasks by nature. Putting them in a separate package with a materialised output means the audit stays reproducible byte for byte and the model's work can be inspected, diffed and re-run without touching the verdict. The cost is one extra artefact on disk and one more command.
- **Verbatim or nothing on the model side.** The proposer could have been allowed to paraphrase. That would have raised recall and quietly reintroduced a judgement call into the credit. Every span the proposer posts is located exactly (capitalisation at most); paraphrase is dropped and logged. The verifier, which also audits hand-written traces, separately allows 2% edit distance for typographic drift with no floor — two rules, both stated.
- **Credits into a summary are resolved by containment, not by trimming.** The segmenter is told to skip meta text ("In summary,"); a quote that straddles such text would otherwise cover characters no claim owns, and the audit would report laundering that isn't there. The first fix trimmed the quote to whatever claims it touched — and review showed that a quote clipping two characters of a real claim was then rewritten into those two characters and *counted*. The rule now has no thresholds: a quote funds a claim only if one contains the other. A quote inside a claim is kept; a claim inside a quote is kept (one credit per whole claim, meta text discarded and logged); a partial straddle is refused as ambiguous.
- **No tolerance floor on quote matching.** A 2% edit tolerance for typographic drift with a minimum of one edit meant `14%` could be cited as `44%` and pass. The floor is gone; a three-character quote gets no free edit.
- **An empty trace does not balance.** `all([])` is `True` in Python, so a truncated trace originally exited 0. A gate that passes on missing input is not a gate.
- **Exit 2 is a hard boundary.** Seven ordinary malformed-JSON shapes originally escaped the loader as `KeyError`/`TypeError` and reached the shell as exit 1 — "your agent is unfaithful" — when the truth was "I could not read your file". The loader now validates every shape and raises one typed error, and the same validation runs on a `Run` built by hand.


## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it. Each hop can be individually correct while the whole chain is wrong, and only a transitive walk sees that.
- "Deterministic" is a property you have to defend structurally. Memoising a result computed while a cycle was open made the verdict depend on array order in the JSON file. Same run, same code, different exit code. The fix was to refuse to cache anything tainted by an open cycle, and the shuffle test now exists so it cannot regress silently.
- A README that overstates what the code does is the single most expensive defect in a project whose thesis is "unsupported claims should be named". The adversarial review pass caught seven such lines before publish.
- **Writing and attacking are different jobs, and the same pass cannot do both.** Every serious defect in this repo was found not while writing but in a separate adversarial review run afterwards, by a reviewer whose only brief was to break it: the wide-citation bypass and the array-order dependence in v0.1; in v0.2, a false `laundered` verdict on a model that had followed the prompt correctly, a string-shaped answer that would have posted one claim per character, and three ways to sneak a model import past the boundary test. None of them were visible from inside the writing.
- **A framework tells you what happened, not what a model saw.** LangChain logs retrievals, tool calls and model outputs, but nothing in it says which of those a given model call had in its context. The recorder recovers that from the prompt bytes, and the first review showed how easy it is to get subtly wrong: a 20-character floor meant for tiny tool results was also dropping short model outputs from the chain, and chat "content blocks" were being JSON-escaped so no multi-line document ever matched. Both mislocated the injection step — the one number the project promises — while every test stayed green, because the demo happened to use one long paragraph.
- **A fix needs its own review.** The fix for the false `laundered` verdict passed its tests and was wrong in both directions — it still failed when the meta text sat *between* two claims, and it turned a mostly-unvouched quote into a two-character credit that counted. The tests I wrote for the fix tested the case I had in mind, not the cases I had not. A third pass, scoped to the fixes only, caught it. The rule this repo now follows: nothing ships without a second pass whose job is to make the first one look bad, and fixes get the same treatment as the code they fix.
- **Decide what the table means before it exists.** The benchmark's caveats, the three rows and the sentence "The table goes here when it exists, whatever it says" were all written and published before a single live call. When the number came in against tallystick on F1, there was nothing to negotiate: the README already said what would be reported and how. A number that could have been adjusted after the fact would have been worth less than the one that couldn't.
- **The failure you get is not the one you tested for.** The first live run crashed at trace 12 on a line I had added the same day to *handle* failures — a progress print that assumed the thing it was printing existed. Then 7 of 198 proposer runs failed and were dropped — the ones I inspected were malformed JSON (a missing comma inside the object), which no amount of trailing-text tolerance fixes. One was a harness bug, one a model-output bug; neither touched the audit, and both cost a rerun.
- **The worst failure is the one that prints a plausible table.** At the v0.5.3 rerun, `run.py` found the v0.5.2 rows file, checked that the model and run counts matched — they did — and resumed: 99 old rows plus one new trace, followed by a complete, well-formatted report. Nothing crashed. The resume check tested the settings and not the data, because when I wrote it the data could not change. Rows now carry a fingerprint of their trace, and a mismatch refuses to resume.

## Next Steps

- [x] v0.2 — model-side proposers, kept outside the verdict path
- [x] v0.3 — LangChain callback recorder
- [ ] v0.3.x — LlamaIndex, Claude Citations ingestion as pre-verified credits, OpenTelemetry span reader
- [ ] v0.4 — HTML ledger view: the answer colour-coded by status, click a sentence to unfold its chain to the root
- [x] v0.5 — benchmark harness: two-hop traces from RAGTruth, tallystick vs. full-history judge; one command
- [x] v0.5.1 — harness survives a failed proposer run; JSON parse ignores trailing text
- [x] v0.5.2 — run it at n=100 and publish the number; bootstrap intervals, paired difference and label-integrity tests
- [x] v0.5.3 — entry groups close the any-of leak; a proposer reply that is not JSON is asked for once more; the sentence splitter treats a line break as a boundary (numbered lists, headings); QA traces carry the question as a root artifact
- [x] v0.5.3 benchmark rerun — F1 unchanged at 0.48, recall 0.53 → 0.65, precision 0.44 → 0.38; both tables kept, both row files in `bench/results/`
- [ ] v0.6 — the recall gap is the verifier checking *where*, not *whether*. One deterministic candidate: require the funding span to contain the claim's content words, measured on this benchmark before it is adopted. Separately, for precision: let the proposer post a paraphrase together with the verbatim span behind it, and verify the span
- [ ] bench: real traces of three or more steps (LangSmith / OpenTelemetry exports), where the last hop is not verbatim by construction
- [ ] PyPI release

## Benchmark

There is no dataset of laundered claims, because every hallucination dataset is one hop. So `bench/build.py` constructs two-hop traces from RAGTruth (test split, Summary and QA tasks, human-annotated hallucinated spans; MIT) without any model: the RAGTruth response becomes the intermediate summary, and a final answer is built by quoting up to three of its sentences, chosen uniformly at random, verbatim. Selection and quoting are seeded (`--seed`, default 7): the item shuffle takes the seed, and each trace's sentence choice is seeded per item, so a trace is byte-identical whatever `--limit` built it, and `--limit N` takes a random prefix of one fixed order rather than a different sample. The seed is recorded in `manifest.json`. Ground truth follows from the annotations alone — a quoted sentence that overlaps an annotated span is laundered, one that overlaps none is grounded.

Four things the reader should know before the number (the first two, and the per-side failure counts behind the fourth, are also printed in the report):

- **The last hop is trivially verifiable.** The answer quotes the summary verbatim, so tallystick's answer-level result is the summary-step detection carried through the chain, not new detection power. What the construction shows is *where a one-hop check breaks*: a judge shown only summary + answer scores ~0 recall by construction. That row is scored anyway, because it is the point.
- **Prevalence is enriched.** Items with at least one annotated hallucination are oversampled to 60%; within an item, sentence choice is uniform. The report prints the constructed and the natural sentence-level prevalence side by side.
- **Data2txt is excluded** — a third of the split and the densest in hallucinations — because its sources are structured records, not prose a model could quote.
- **Dropped traces are dropped by tallystick's side.** A trace is scored only if every run on every side succeeded, and at the v0.5.2 run every failure was the proposer's (7 proposer runs, 0 judge runs), so the scored set is conditioned on tallystick having run. At n=100 the 6 dropped traces were all Summary items, 18 answer sentences between them and 1 laundered (5.6%, against 15.8% kept) — longer than typical (median 1,019 characters against 659 kept), mostly clean summaries on which the model returned malformed JSON, not hard cases. `bench/ci.py` prints the dropped-set prevalence for any run.

`bench/run.py` compares three things at the sentence level: a **one-hop judge** (summary + answer only, the industry default), a **full-history judge** (same model, shown every document, the summary and the numbered answer — the strongest thing a team can do today without a provenance tool), and **tallystick**. Every side is rerun — judges `--judge-runs` times, the proposer `--proposer-runs` times, all at API-default sampling — so tallystick's end-to-end spread is reported next to the fact that auditing one posted file twice is identical. Failures are counted per side; a trace on which any run on any side failed is dropped from all sides, so every row and every run is scored on exactly the same sentences, and that count is printed. Results are appended per trace, and rerunning the same command resumes; every row carries a fingerprint of the trace it was scored on, and a rows file from a different build of the traces is refused rather than resumed. A fourth number, hallucination detection at the summary step, is the classic task where fine-tuned detectors live; tallystick's known false-positive source there (an abstractive sentence fusing two passages has no single verbatim quote) is stated in the report.

An offline test drives the whole harness with an oracle proposer scripted from the labels and requires F1 = 1.0 on both levels, so a live number measures the proposer, not the plumbing.

```bash
python bench/build.py --limit 100     # clones RAGTruth, builds traces + manifest, no model
python bench/run.py --dry-run         # counts calls and estimates cost
python bench/run.py --limit 100       # needs ANTHROPIC_API_KEY; writes bench/work/RESULTS.md
```

### Results — v0.5.2 (September 2026)

Built with `--limit 100` (99 traces: one selected item had fewer than two sentences and yields no trace); 99 ran, 93 are scored: 6 were dropped because at least one of their proposer runs failed (7 failed runs out of 198; both judges 0 failures). 273 final-answer sentences, 43 laundered by human annotation (constructed prevalence 15.8%; natural sentence-level prevalence in the eligible pool 6.2%). Every row is scored on the same 273 sentences. Model on every side: `claude-sonnet-4-6`, API-default sampling.

| method | precision | recall | F1 | FPR | variance |
|---|---|---|---|---|---|
| one-hop judge (summary + answer only) | 0.33 | 0.05 | 0.09 | 0.02 | 3 runs, F1 spread 0.04, 1% of sentences flip |
| full-history judge (documents + summary + answer) | 0.50 | 0.81 | 0.62 | 0.15 | 3 runs, F1 spread 0.02, 7% of sentences flip |
| tallystick (propose + audit) | 0.44 | 0.53 | 0.48 | 0.13 | 2 runs, F1 spread 0.06, 8% of sentences flip; audit of one posted file: identical |

Sample noise, separately from the model noise in the last column: `bench/ci.py` bootstraps over traces (2000 resamples of the 93, seed 0) on the committed rows. 95% intervals on F1: one-hop judge [0.01, 0.19], full-history judge [0.51, 0.71], tallystick [0.37, 0.58]. The paired difference full-history judge − tallystick is 0.14, 95% CI [0.01, 0.27]; about 1% of resamples (0.7–1.7% across seeds 0–4) put tallystick at or ahead. So "the judge beats tallystick on F1" is not sample noise, and "the one-hop judge sees nothing" holds with a wide margin.

Summary-step detection (claim level, first proposer run, the classic task): precision 0.49, recall 0.64, F1 0.55 over 542 claims. Granularity 1.11 claims per answer sentence (mean over traces and runs). Across both proposer runs on the 93 scored traces, 1697 claims were posted, 234 of them prior-only.

What the table says, in order of importance:

- **The one-hop judge cannot see laundering** — recall 0.05 on a task where the answer is verbatim, as the construction predicts. This is the number the benchmark exists to show.
- **A judge given the full history beats tallystick on F1, 0.62 to 0.48.** The gap is mostly recall. The main mechanism is that the verifier checks *where* a quote is, not *whether* it supports the claim: when the proposer attaches a real document span to a summary claim overlapping an annotated span, the audit accepts it, and every answer sentence quoting it is missed. Two further paths exist and the harness does not separate them out: a claim with several credits closes if *any one* of them closes, so an answer sentence whose summary sentence was segmented into a true clause and an invented clause passed on the true clause even when the invented one was correctly posted prior-only (found while reviewing these results; closed by entry groups in v0.5.3 — see Tradeoffs); and an answer sentence the segmenter failed to locate has no claim to fail and is never flagged. Nothing in the verdict path judges support, by design, and this is the price of that design measured for the first time.
- **The false positives come from the opposite case.** A summary sentence the proposer cannot locate verbatim (paraphrase, two passages fused) is posted prior-only, which is a failing status, so every answer sentence quoting it is flagged — whether the annotators marked it or not. That, plus the same failure one hop later (an answer claim whose summary quote the proposer did not return verbatim is itself posted prior-only), is where tallystick's FPR of 0.13 comes from — the "verbatim or nothing" tradeoff above, in numbers.
- **What tallystick buys for that price is a verdict that is a function of the file.** The audit path has no model, no randomness and no clock (a test walks the AST to enforce it), and the harness confirms per-claim statuses are identical when the posted file is re-read and audited again. So the 8% of sentences that flip between tallystick's two runs are differences between two posted files, on disk, that can be diffed; the judge's 7% cannot be attributed to anything on disk.
- Neither of the two rows that can see laundering is good enough to ship as a silent filter: both put a false flag on roughly one in seven or eight clean sentences.

### Results — v0.5.3 (September 2026)

Same command, traces rebuilt by v0.5.3's `build.py` (a line break is a sentence boundary; QA traces carry the question as a root). 100 traces built, 99 scored: 1 dropped because both proposer runs failed on it — the same trace, 885, that failed both runs at v0.5.2 (2 failed runs out of 200; 7 of 198 at v0.5.2, before the one-retry; judges 0). 290 final-answer sentences, 40 laundered (constructed prevalence 13.8%; natural 5.8%). Model on every side: `claude-sonnet-4-6`, API-default sampling. Rows: `bench/results/2026-09-n100-v0.5.3.json`.

| method | precision | recall | F1 | FPR | variance |
|---|---|---|---|---|---|
| one-hop judge (summary + answer only) | 0.12 | 0.04 | 0.06 | 0.05 | 3 runs, F1 spread 0.04, 3% of sentences flip |
| full-history judge (documents + summary + answer) | 0.45 | 0.81 | 0.57 | 0.16 | 3 runs, F1 spread 0.04, 7% of sentences flip |
| tallystick (propose + audit) | 0.38 | 0.65 | 0.48 | 0.17 | 2 runs, F1 spread 0.03, 8% of sentences flip; audit of one posted file: identical |

Bootstrap over traces (`bench/ci.py`, 2000 resamples, seed 0): 95% intervals on F1 — one-hop judge [0.00, 0.15], full-history judge [0.47, 0.67], tallystick [0.37, 0.58]. Paired difference full-history judge − tallystick: 0.10, 95% CI [−0.00, 0.21] (lower bound −0.01 to 0.00 across seeds 0–4; seed 3 gives [0.00, 0.19]); about 3% of resamples (2.3–4.2%) put tallystick at or ahead. Summary-step detection (claim level, first proposer run): precision 0.49, recall 0.62, F1 0.55 over 590 claims. Across both proposer runs on the 99 scored traces, 1815 claims were posted, 252 of them prior-only.

**What changed from v0.5.2, and what it cost.** The two runs are not the same sentences, so this is two versions of a benchmark, not one benchmark twice; with that caveat:

- **tallystick's F1 did not move (0.48 → 0.48), but its shape did.** Recall rose from 0.53 to 0.65 — 27 and 25 of 40 laundered sentences found in the two runs, against 25 and 21 of 43 — and precision fell from 0.44 to 0.38 (false flags 43 and 43 per run, against 30 and 29). The direction is what the group fix predicts — a quote now inherits the invented clause it covered (more true flags) and the paraphrased honest clause it covered (more false flags) — but the rows do not separate the fix from the rebuilt traces, and on the 84 traces whose labels are identical in both files the recall gain is smaller (0.57 → 0.63) and the false-flag rise larger (25 → 37 per run) than the aggregate suggests. The verdict got stricter in both directions, and the two effects cancelled on F1.
- **The gap to the full-history judge narrowed from 0.14 to 0.10, and is now on the edge of significance.** The judge's own F1 fell 0.62 → 0.57 with recall unchanged at 0.81 and its false-flag rate where it was (FPR 0.15 → 0.16): the precision drop (0.50 → 0.45) is mostly fewer laundered sentences in a larger scored set, not a change in how it reads them — on the 84 traces with identical labels its true and false flags are unchanged. Saying "the judge beats tallystick on F1" is still the best reading of the data; saying it is settled is not.
- **The judge is still not a gate.** Its 7% flip rate between runs at identical settings is unchanged; tallystick's audit of one posted file is byte-identical as before, and its two proposer runs now differ by 0.03 F1 (was 0.06).
- **Both rows put a false flag on roughly one in six clean sentences.** Neither is a silent filter. By design, tallystick's false flags each trace to one named place — a summary clause the proposer posted prior-only or whose quote the verifier rejected — and the posted file records the claim and the step it broke at; the harness does not tally those reasons, and the judge's are not attributable to anything on disk.

### Using the benchmark for your own detector

The traces are plain JSON and the labels are in them, so anything that flags sentences can be scored on the same construction without running tallystick:

```bash
python bench/build.py --limit 100   # -> bench/work/traces/<id>.json + manifest.json; seed 7, reproducible
```

Each trace has `artifacts` (root documents, an `intermediate` summary, a `final_answer`), `steps` (retrieve → summarize → answer, with inputs and outputs), and a `_truth` block: `answer_sentences` is a list of `{start, end, text, laundered}` over the answer text, and `summary_hallucinated_spans` are the RAGTruth annotations over the summary. Run your detector on the answer, produce one boolean per entry of `answer_sentences` in order, and score with `prf` from `bench/run.py` (from the repo root; the import needs no SDK):

```python
from bench.run import prf
pred, truth = [], []
for trace in traces:                          # manifest.json "order", first N
    flags = my_detector(trace)                # List[bool], one per answer sentence
    pred += flags
    truth += [s["laundered"] for s in trace["_truth"]["answer_sentences"]]
print(prf(pred, truth))                       # precision, recall, f1, fpr, counts
```

The per-trace rows of both runs are committed — `bench/results/2026-09-n100.json` (v0.5.2) and `bench/results/2026-09-n100-v0.5.3.json` (v0.5.3), each a JSON object with the rows under `"rows"` and the aggregate under `"summary"`: which traces, the per-sentence truth, and every run's flags on every side — a failed run is `null`, and a trace with any `null` run is one of the dropped (6 in the v0.5.2 file, 1 in the v0.5.3 file). To compare on exactly the same sentences as a table, keep only that file's rows with no `null` run rather than taking every trace in the manifest. `python bench/ci.py <results.json>` recomputes the table from the rows and adds bootstrap intervals and the paired difference, with no model calls. Labels are checked two ways in the test suite: every RAGTruth annotation's span must address the text the annotation carries (the test runs on the 6-item sample in `bench/sample`; the same check over all 14,289 labels in the dataset was run by hand at n=100 and passed), and every answer-sentence label is re-derived independently from the raw annotations and must agree with what `build.py` wrote. Keep the caveats attached to any number you publish: the last hop is verbatim by construction, prevalence is enriched, Data2txt is excluded, and the scored set is conditioned on tallystick's proposer not failing. If your detector only sees the last hop, expect the first row.

## Where This Sits

| | one-hop grounding | across steps | deterministic | names the step |
|---|---|---|---|---|
| Anthropic Citations API | ✅ documents only | ❌ | ✅ | ❌ |
| RAGAS faithfulness | ✅ | ❌ | ❌ | ❌ |
| LLM-as-judge, one-hop context | ✅ | ❌ *(F1 0.06 above)* | ❌ | ❌ |
| LLM-as-judge, full history | ✅ | ✅ *(F1 0.57 above)* | ❌ | ❌ |
| LEDGER ([arXiv 2608.18398](https://arxiv.org/abs/2608.18398)) | ✅ | ✅ | ❌ *(authors' own caveat)* | partly |
| **tallystick** | ✅ | ✅ *(F1 0.48 above)* | ✅ audit; proposer is a model | ✅ |

The Citations, RAGAS and LEDGER rows are my reading of their public documentation as of September 2026, not benchmark results; the two judge rows and the tallystick row carry the F1 from the Benchmark section.

Citations grounds one hop and does it well; the plan is to ingest those citations as pre-verified credits and spend effort on the hops citations cannot see. The 2026 survey [*From Agent Traces to Trust*](https://arxiv.org/abs/2606.04990) lists claim-level provenance and error propagation through execution chains as open problems; this is a shipped, tested answer to a narrow slice of both.

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
Built by Katia Engalycheva | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
