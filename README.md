# tallystick

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

**In short.** An agent that summarises its sources and then answers from the summary can invent a fact in the middle and quote it faithfully at the end; every one-hop check then says "grounded". tallystick walks the chain back to the documents with plain code — no model in the verdict — and names the step where it breaks. On 290 answer sentences built from RAGTruth, that audit is level with an LLM judge shown the full history on F1 (0.59 vs 0.58; the paired difference spans zero) and puts a false flag on fewer than half as many clean sentences (FPR 0.07 vs 0.16). The benchmark is constructed, not natural; its limits are stated under [Benchmark](#benchmark), and a run on real agent trajectories is in progress.

![One hop is not enough: the answer quotes the summary, the summary invented a sentence, and the chain breaks at the summarise step](docs/chain.svg)

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

**The proposer is a pointer generator, not a judge.** It is asked for claims and quotes as verbatim substrings of text it was shown. Each one is then *located*: exact substring search first, then (since v0.6) a match on the sequence of words that forgives punctuation, spacing, case and quote or dash variants and never a changed letter, digit or symbol — `14%` does not find `15%` or `14`. Whatever cannot be located is logged in the posted file and dropped. A claim with no locatable support is posted as `PRIOR:model`, which never funds anything, and a quote into text no claim vouches for is refused — so an invented sentence fails closed downstream, whether or not the segmenter picked it up. The model never answers "is this faithful?"; it only answers "where?", and the answer is checked.

**Two searches back the model up (v0.6).** A sentence of an intermediate artifact the segmenter did not return is posted as a claim anyway — *coverage* — so text a later step quotes always has an account, funded or not; the final answer is not covered, because its segmenter decides what the audit checks and a hedge it skipped is not a claim to fund. And for every claim, each source of the step is searched for the claim's own text; a word-for-word hit is posted as a credit marked `proposed_by: "verbatim"`, ahead of whatever the model offers. Both are substring searches, not judgements, and both are counted in the posted file's `_proposal` block.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every verdict-path module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping. Found while reviewing the v0.5.2 benchmark results: the proposer never posted a wide span. It snapped a quote that covers two summary claims into one credit per claim, and a claim with several credits closes if any one does — so on proposer-posted files the all-of rule was bypassed by the pipeline that feeds it, and a sentence made of one true clause and one invented clause closed on the true one. The rule was right; the pipeline undercut it. v0.5.3 closes it with **entry groups**: the entries that came from one quote share a `group` id, and the ledger closes a group only if every member closes — the quote inherits the worst claim it covered, as a single wide entry would — while independent entries still close on the best (a real second source is a real second source). Hand-written traces without groups are unchanged. What stays open: a narrow second quote that is a sub-span of the group's own quote still closes the claim on its own, under the independent-entries rule — the same where-not-whether cost stated under Benchmark. The v0.5.2 table was measured with the leak in place; the v0.5.3 rerun that followed is in `bench/HISTORY.md`.
- **The model proposes into a file; the verdict reads the file.** Segmenting text into claims and proposing credits are model tasks by nature. Putting them in a separate package with a materialised output means the audit stays reproducible byte for byte and the model's work can be inspected, diffed and re-run without touching the verdict. The cost is one extra artefact on disk and one more command.
- **Verbatim or nothing on the model side.** The proposer could have been allowed to paraphrase. That would have raised recall and quietly reintroduced a judgement call into the credit. Every span the proposer posts is located word for word (punctuation, spacing and case at most — see the next point); paraphrase is dropped and logged. The verifier, which also audits hand-written traces, separately allows 2% edit distance for typographic drift with no floor — two rules, both stated.
- **v0.6: the false flags had causes on disk, so they were fixed on disk.** `bench/diagnose.py` re-audits a run's posted files offline and sorts every false flag by the reason the chain broke. On the v0.5.3 run (86 false flags in 198 posted files) the top causes were not paraphrase: the model returned no credit at all (45, of which 18 at the answer's own hop, where the answer sentence is a summary sentence by construction); the answer's quote cut across the summary's claim boundaries or landed on a sentence the segmenter had skipped (26); a quote dropped for a full stop, quotation marks or a `(Passage 1)` tail the model had added or left off (15). Each got a deterministic fix — the word-level locate, coverage claims for intermediate artifacts, the self-evident credit — and the containment rule now judges on words, so a trailing full stop the segmenter and the credit prompt disagree about is no longer a straddle. Two things were considered and refused: a character-level tolerance, because a locate that forgave one character would let `14` find `15`; and coverage on the final answer, because on the posted files it turned skipped hedges ("it is difficult to give an exact answer") into flagged claims. Review of the first cut found two more holes, both closed: a dash is a separator, so `-5%` was found by `5%` (a dash or bracket hugging a number is now part of it), and the sentence regex let a sentence begin after any full stop, so `3.5%` produced the claim "5% compared to last year" and the search grounded it — the splitter, in `bench/build.py` too, now starts each sentence where the last one ended and does not break after an abbreviation. Two limits are stated up front. On this benchmark the answer is made of summary sentences, so the self-evident credit settles the answer hop by bookkeeping: the 44 flags that broke there should go away without any detection improving (at the rerun, 3 of them remained — two of those on one sentence the v0.5.3 splitter had cut inside "3.2%", a cut the splitter fix above cannot reach because the traces were deliberately not rebuilt), and the summary→document hop is where detection is measured — the rerun carries the three counters (`coverage_claims`, `tolerant_locates`, `self_evident_credits`) in every row so the two can be told apart. And the search is as blind as the verifier to *whether*: a summary sentence found inside a document sentence that negates it is grounded, as a model's quote of it was before; the difference is that now nothing declines to quote. What the fixes did to the table is measured under Benchmark: false flags 86 → 37 on the same sentences, with the recall and summary-step costs stated there.
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
- **Sort the errors by cause before touching the model.** At v0.5.3, one clean sentence in six was flagged, and the obvious reading was "the proposer paraphrases". An offline pass over the posted files said otherwise: the top causes were a credit the model did not bother to offer for text sitting verbatim in its source, a sentence the segmenter skipped, and a full stop the model added — in that order. Three searches, no new judgement anywhere, and the false flags halved on the same sentences. The paraphrase problem is real — it is most of what is left — but it was a third of the story, not the story.

## Next Steps

Done so far: v0.2 model-side proposers outside the verdict path; v0.3 LangChain recorder; v0.5–v0.5.3 the benchmark, its intervals and the entry-group fix; v0.6 precision from the false-flag diagnosis; v0.7 the AgentHallu adapter and harness. The version-by-version record, with what each one cost, is in [`bench/HISTORY.md`](bench/HISTORY.md).

- [ ] v0.7 run — 228 AgentHallu trajectories (115 labelled, of which 61 at a tool-result boundary; 113 clean), CodeAct runs excluded and counted — the first measurement of this audit against human step attribution, on traces nobody built for it
- [ ] v0.8 — the recall gap is the verifier checking *where*, not *whether*. One deterministic candidate: require the funding span to contain the claim's content words, measured on this benchmark before it is adopted. Separately: let the proposer post a paraphrase together with the verbatim span behind it, and verify the span
- [ ] coverage for the remainder of a sentence the segmenter claimed only in part ("Revenue rose, and the CEO resigned" with a claim over the first clause): today the remainder is logged as uncredited characters, not posted as a claim
- [ ] adapters: LangSmith and OpenTelemetry exports, so a team can audit yesterday's logs without changing code — and a benchmark on such traces, three or more steps, where the last hop is not verbatim by construction; LlamaIndex; Claude Citations ingested as pre-verified credits
- [ ] HTML ledger view: the answer colour-coded by status, click a sentence to unfold its chain to the root
- [ ] PyPI release

## Benchmark

When this benchmark was built, no multi-step dataset with sentence-level labels of unsupported claims was available — the hallucination corpora are one hop, and [AgentHallu](https://arxiv.org/abs/2601.06818) (2026), which does have real multi-step trajectories, labels the responsible *step*, not the sentence; it is the basis of the v0.7 run below. So `bench/build.py` constructs two-hop traces from RAGTruth (test split, Summary and QA tasks, human-annotated hallucinated spans; MIT) without any model: the RAGTruth response becomes the intermediate summary, and a final answer is built by quoting up to three of its sentences, chosen uniformly at random, verbatim. Selection and quoting are seeded (`--seed`, default 7): the item shuffle takes the seed, and each trace's sentence choice is seeded per item, so a trace is byte-identical whatever `--limit` built it, and `--limit N` takes a random prefix of one fixed order rather than a different sample. The seed is recorded in `manifest.json`. Ground truth follows from the annotations alone — a quoted sentence that overlaps an annotated span is laundered, one that overlaps none is grounded.

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
- **The gain came from bookkeeping, and the diagnosis says so.** `bench/diagnose.py` on the v0.5.3 posted files found 86 false flags across the two runs; on the v0.6 files, 37 on the scored sentences (38 over all 199 posted files, which include the one surviving run of the dropped trace). Its output for the v0.6 run is committed as `bench/results/diagnose-v0.6.txt`; for v0.5.3, the bucket counts are transcribed from the run's output into `bench/results/diagnose-v0.5.3.txt`, since the posted files it read are gone; posted files are not committed. The three causes v0.6 targeted — no credit offered, a quote that straddled the summary's claim boundaries, a quote dropped for punctuation — accounted for 45 + 26 + 15 of the 86 and for 33 + 1 + 0 of the 37. The punctuation drops are gone, one straddle remains, and 41 of the 44 flags that broke at the answer's own hop are gone. Of the 37, 31 are a summary claim the proposer offered no credit for — a paraphrase of a passage, or a hedge or abstention ("this passage does not address…"), that the annotators do not mark; the self-evident credit cannot help because the text is not in the passage word for word — and the other six are scattered. That is a paraphrase problem, not a locate problem, and nothing deterministic in this design addresses it.
- **Recall slipped by a net three sentences in one run and none in the other** — six flags lost on four sentences, three gained on two. The likeliest reason, from reading the lost ones: at v0.5.3 a quote the pipeline could not locate happened to sit on a laundered sentence, so the flag was right by accident; those quotes locate now and the flag depends on the summary hop like every other. That is a reading of four cases, not a measurement. The misses (31 across two runs, 28 at v0.5.3) are the same shape as before: 28 close as grounded at depth 2, the verifier accepting a real document span that does not support the claim it funds — the *where*-not-*whether* limit stated under Tradeoffs and, first, in the v0.5.2 discussion in `bench/HISTORY.md`.
- **The summary-step number got worse, and that is a cost of v0.6.** Claim-level precision on the summary fell 0.49 → 0.39 over 759 claims in the first run (590 at v0.5.3). Two things added claims: 70 coverage claims per run over sentences the segmenter skipped — closing lines, hedges, list tails — which the proposer can rarely fund and which, on inspection of the posted files, the annotators mostly left unmarked; and roughly 80–100 more model claims per run, most plausibly ones the word-level locate now keeps instead of dropping (`tolerant_locates` counts claims and quotes together, and the two v0.5.3 runs already differ by 14 claims from segmenter variance, so this is an inference). The rows do not mark which is which, so the split of the 45 extra false flags between them is not measured. Coverage claims exist so that an answer quote always has an account to land on; together with the self-evident credit and the locate they are what cut the false flags on the chain, and at the summary step they are noise. Both numbers are reported because they pull in opposite directions.
- **Two proposer runs now agree on F1 to two decimals** (spread 0.004, was 0.03), and 5% of sentences flip between runs (was 8%). Less of the posting is the model's: coverage, self-evident credits and the word-level locate are searches, and searches do not vary. The judge's flip rate at identical settings is also 5% this run (7% at v0.5.3).

### Real trajectories: AgentHallu (v0.7 — run in progress; the table lands here)

Everything above is on traces `build.py` constructed. [AgentHallu](https://arxiv.org/abs/2601.06818) (Liu et al., 2026; CC BY 4.0) is 693 real runs of seven agent frameworks — SmolAgents, OpenDeepSearch, OpenManus, Magentic-One, OWL/Camel, OctoTools, function-calling agents — of which 443 carry a human label naming the step that introduced the hallucination and 250 are clean. `tallystick/adapters/agenthallu.py` reads a trajectory as a trace (question → document; each step's model text → intermediate; tool results → roots; the answer → final_answer; every step sees everything before it — which makes the reachability gate vacuous on this data, so what the audit tests here is the quote gate and the chain, not conservation), and `bench/agenthallu.py` posts a selection with the proposer, audits, and compares the audit's `break_step_id` with the labelled step: any final claim flagged, earliest breaking step equal to the label, within one step, and the false-alarm rate on clean runs. Credits are proposed on demand — the answer's claims first, then only the claims their credits reach — so the cost is one credit call per claim the answer rests on, not per sentence the agent ever wrote.

Two boundaries are stated before any number exists, because reading the files settled them:

- **The audit stops at a tool result, and 61 of the 115 labelled runs in the default selection are labelled at a step whose only artifacts are tool results.** OpenDeepSearch's `web_search`, above all, returns a model-written digest of pages the file does not contain, and the label sits inside that digest. There is no page in the file to check it against; no post-hoc audit of the file can reach it. Those rows are reported apart — neither hits nor misses; a flag on one is a flag on something else in the run. Tools that hand the agent's own text back — `final_answer`, Camel's notes, `terminate` — are posted as model text, not roots, or every answer would ground on itself; five runs labelled at a note-writing step are reachable for that reason and are not in the 61. (An interpreter printing the answer literal back from the model's code is treated the same way; that only occurs in CodeAct runs, excluded by default.)
- **CodeAct agents (55 of 693) call their tools from inside model-written code**, so one execution log holds a web result and a model-computed string side by side, and the file does not mark where one ends and the other begins. No reading of that log — root or model text — audits it honestly; those runs are excluded by default and counted.

The default selection is the categories where the label and the audit ask the same question — a fact stated in model text with nothing behind it: Planning/Fact Derive, Reasoning/Factual Reasoning, and the three Retrieval sub-categories — plus as many clean runs from the same frameworks as exist (OpenManus has 20 for its 22): 115 labelled and 113 clean, 228 trajectories, roughly $17–34 of proposer calls — an estimate from input tokens alone, assuming about six credit calls per trajectory. The table reports, for the reachable rows, whether any final claim was flagged, whether the earliest breaking step is the labelled one, whether the labelled step is among the breaks at all (an unfunded hedge quoted from an earlier step drags the earliest break forward without making the audit wrong about the labelled step), and within one step. AgentHallu's abstract reports its best model judge localising the step in 41.1% of cases over all categories; that number is on a different set and a different question, and will be quoted next to ours only with that said. Five trajectories are committed under `bench/sample-agenthallu/` as test fixtures, unchanged, with attribution.

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
Built by Katia Engalycheva | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
