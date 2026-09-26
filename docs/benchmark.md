[← README](../README.md)

# Benchmark

**Which version measured what.** The constructed benchmark below is a v0.6 run
(`bench/results/2026-09-n100-v0.6.json`); the real-trajectory tables are a v0.7.3
run (`bench/results/agenthallu-v0.7.3-rows.jsonl`, and every row carries its
`tallystick_version`). Both tables recompute from the committed rows with no
model and no key — `python bench/ci.py bench/results/2026-09-n100-v0.6.json`.

**What the quote-gate changes reach here, and what they do not.** Every quality
figure on this page — precision, recall, F1, the bootstrap intervals, and both
AgentHallu tables — was measured under the quote gate as it stood before v0.8.1.
That gate is the one thing on this page a rule change could move, and the path is
traceable rather than suspected: `sentence_flags_from_audit` in `bench/run.py`
builds its per-sentence flags from `balance.audits[...].status`, and those
statuses are what the quote gate in `tallystick/verify.py` decides. The gate has
changed in two releases since: v0.8.1 removed an edit budget of 2% of the span's
length, and v0.9.0 (the working versions v0.8.2 to v0.8.4 never reached `main`)
names what a quote may differ by rather
than what it may not, and asks where the span was cut — a span whose boundary
falls inside a number or a word, as the check reads them, is refused, even when
the quote matches it word for word. The shapes it does not read as a number or a
word that the reviews have found are under Known limitations in the README, and
that list is not complete.

On the instrument in
[`bench/quote_gate_corpus.py`](../bench/quote_gate_corpus.py), with 305,499
tampering pairs and 284,010 typographic pairs built from the 5,615 real quotes
of both published runs, the v0.8.1 signature refuses 25.23% of the
tampering and v0.9.0 refuses all of it, accepting every typographic pair. That
is a statement about the classes the instrument asks about. The ones it does not
ask about that the reviews have found — forgeries still accepted, honest quotes
refused, with frequencies where they were counted — are under Known limitations
in the [README](../README.md#known-limitations), a list that is not complete.

**It does not move the numbers below, and that was re-derived rather than
argued.** All 5,615 EVIDENCE quotes in the 424 posted files of both runs are exact
slices of their sources, character for character (a count that never calls the
gate). Up to v0.8.4 that was enough: `verify_entry` settled an exact slice before
any rule. It no longer is. The boundary checks run before that equality, and they
refuse 8 of the 5,615 exact slices: one real cut (`1/2` cited as `2`) and seven
named prices of the rule. So the posted files were audited again on v0.9.0 and
compared with the committed row files: all 580 sentence flags of the v0.6 run (290
sentences, two proposer runs) and all 225 trajectory scores of the v0.7.3 run are
reproduced. One of the 11,547 claims changes status (`Magentic_One__004`, an
intermediate claim); no scored sentence or trajectory rests on it. The judges'
rows do not depend on tallystick and were not rerun; nor was the proposer, which
is a model. Those posted files are not in this repository — see the note on
`bench/diagnose.py` below and [Reproducing the numbers](../README.md#reproducing-the-numbers)
— so the re-audit was run on the owner's copies, offline and at no cost.

Two changes that do **not** reach these numbers, for reasons as specific: the
half-answer rule of v0.8.0 (`answer_mostly_unclaimed`) moves an exit code, not a
claim status, and nothing here is scored from exit codes; and v0.8.1's fix to the
break reason names a different reason without moving a status or a breaking step
(2,000 shuffles of every array move neither).

When this benchmark was built, we found no multi-step dataset with sentence-level labels of unsupported claims — the hallucination corpora are one hop, and [AgentHallu](https://arxiv.org/abs/2601.06818) (2026), which does have real multi-step trajectories, labels the responsible *step*, not the sentence; it is the basis of the v0.7 run below. So `bench/build.py` constructs two-hop traces from RAGTruth (test split, Summary and QA tasks, human-annotated hallucinated spans; MIT) without any model: the RAGTruth response becomes the intermediate summary, and a final answer is built by quoting up to three of its sentences, chosen uniformly at random, verbatim. Selection and quoting are seeded (`--seed`, default 7): the item shuffle takes the seed, and each trace's sentence choice is seeded per item, so a trace is byte-identical whatever `--limit` built it, and `--limit N` takes a random prefix of one fixed order rather than a different sample. The seed is recorded in `manifest.json`. Ground truth follows from the annotations alone — a quoted sentence that overlaps an annotated span is laundered, one that overlaps none is grounded.

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

## Earlier runs

Two earlier runs on this benchmark — v0.5.2 (the first number, the LLM judge ahead by 0.14) and v0.5.3 (the fix that closed a leak, recall up, precision down, F1 unchanged) — are kept in full, tables, intervals and what each change cost, in [`bench/HISTORY.md`](../bench/HISTORY.md). Their row files are in `bench/results/`. The current table is below and is measured on the same sentences as v0.5.3.

## Results — v0.6 (September 2026)

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
- **Recall slipped by a net three sentences in one run and none in the other** — six flags lost on four sentences, three gained on two. The likeliest reason, from reading the lost ones: at v0.5.3 a quote the pipeline could not locate happened to sit on a laundered sentence, so the flag was right by accident; those quotes locate now and the flag depends on the summary hop like every other. That is a reading of four cases, not a measurement. The misses (31 across two runs, 28 at v0.5.3) are the same shape as before: 28 close as grounded at depth 2, the verifier accepting a real document span that does not support the claim it funds — the *where*-not-*whether* limit stated under [Tradeoffs](design.md#tradeoffs-and-decisions) and, first, in the v0.5.2 discussion in `bench/HISTORY.md`.
- **The summary-step number got worse, and that is a cost of v0.6.** Claim-level precision on the summary fell 0.49 → 0.39 over 759 claims in the first run (590 at v0.5.3). Two things added claims: 70 coverage claims per run over sentences the segmenter skipped — closing lines, hedges, list tails — which the proposer can rarely fund and which, on inspection of the posted files, the annotators mostly left unmarked; and roughly 80–100 more model claims per run, most plausibly ones the word-level locate now keeps instead of dropping (`tolerant_locates` counts claims and quotes together, and the two v0.5.3 runs already differ by 14 claims from segmenter variance, so this is an inference). The rows do not mark which is which, so the split of the 45 extra false flags between them is not measured. Coverage claims exist so that an answer quote always has an account to land on; together with the self-evident credit and the locate they are what cut the false flags on the chain, and at the summary step they are noise. Both numbers are reported because they pull in opposite directions.
- **Two proposer runs now agree on F1 to two decimals** (spread 0.004, was 0.03), and 5% of sentences flip between runs (was 8%). Less of the posting is the model's: coverage, self-evident credits and the word-level locate are searches, and searches do not vary. The judge's flip rate at identical settings is also 5% this run (7% at v0.5.3).

## Real trajectories: AgentHallu (v0.7.3, September 2026)

Everything above is on traces `build.py` constructed. [AgentHallu](https://arxiv.org/abs/2601.06818) (Liu et al., 2026; CC BY 4.0) is 693 real runs of seven agent frameworks — SmolAgents, OpenDeepSearch, OpenManus, Magentic-One, OWL/Camel, OctoTools, function-calling agents — of which 443 carry a human label naming the step that introduced the hallucination and 250 are clean. `tallystick/adapters/agenthallu.py` reads a trajectory as a trace (question → document; each step's model text → intermediate; tool results → roots; the answer → final_answer; every step sees everything before it — which makes the reachability gate vacuous on this data, so what the audit tests here is the quote gate and the chain, not conservation), and `bench/agenthallu.py` posts a selection with the proposer, audits, and compares the audit's `break_step_id` with the labelled step: any final claim flagged, earliest breaking step equal to the label, within one step, and the false-alarm rate on clean runs. Credits are proposed on demand — the answer's claims first, then only the claims their credits reach — so the cost is one credit call per claim the answer rests on, not per sentence the agent ever wrote.

Two boundaries are stated before any number exists, because reading the files settled them:

- **The audit stops at a tool result, and 61 of the 115 labelled runs in the default selection are labelled at a step whose only artifacts are tool results.** OpenDeepSearch's `web_search`, above all, returns a model-written digest of pages the file does not contain, and the label sits inside that digest. There is no page in the file to check it against; no post-hoc audit of the file can reach it. Those rows are reported apart — neither hits nor misses; a flag on one is a flag on something else in the run. Tools that hand the agent's own text back — `final_answer`, Camel's notes, `terminate` — are posted as model text, not roots, or every answer would ground on itself; five runs labelled at a note-writing step are reachable for that reason and are not in the 61. (An interpreter printing the answer literal back from the model's code is treated the same way; that only occurs in CodeAct runs, excluded by default.)
- **CodeAct agents (55 of 693) call their tools from inside model-written code**, so one execution log holds a web result and a model-computed string side by side, and the file does not mark where one ends and the other begins. No reading of that log — root or model text — audits it honestly; those runs are excluded by default and counted.

The default selection is the categories where the label and the audit ask the same question — a fact stated in model text with nothing behind it: Planning/Fact Derive, Reasoning/Factual Reasoning, and the three Retrieval sub-categories — plus as many clean runs from the same frameworks as exist (OpenManus has 20 for its 22): 115 labelled and 113 clean, 228 trajectories, roughly $17–34 of proposer calls at the first attempt — an estimate from input tokens alone, assuming about six credit calls per trajectory. Two earlier runs were withdrawn in review; the published run answered most questions from their posted files through `--reuse` and sent only what was new to the model, so it cost a small fraction of that. The harness prints the split but does not record it, so the exact number of live calls is not on file. The table reports, for the reachable rows, whether any final claim was flagged, whether the earliest breaking step is the labelled one, whether the labelled step is among the breaks at all (an unfunded hedge quoted from an earlier step drags the earliest break forward without making the audit wrong about the labelled step), and within one step. AgentHallu's abstract reports its best model judge localising the step in 41.1% of cases over all categories; that number is on a different set and a different question, and will be quoted next to ours only with that said. What went wrong in each withdrawn run, and how it was fixed, is in [`bench/HISTORY.md`](../bench/HISTORY.md). Five trajectories are committed under `bench/sample-agenthallu/` as test fixtures, unchanged, with attribution.

**The result.** 225 of the 228 scored; 3 failed in the proposer and are excluded. Rows are in [`bench/results/agenthallu-v0.7.3-rows.jsonl`](../bench/results/agenthallu-v0.7.3-rows.jsonl), the report in [`bench/results/agenthallu-v0.7.3.md`](../bench/results/agenthallu-v0.7.3.md), the false-alarm diagnosis in [`bench/results/diagnose-agenthallu-v0.7.3.txt`](../bench/results/diagnose-agenthallu-v0.7.3.txt).

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

  The 53% rests on one definition — a labelled step whose only recorded artifacts are tool results — and that definition is worth arguing with, so the argument is measured rather than left open ([`bench/boundary_sensitivity.py`](../bench/boundary_sensitivity.py), output in [`bench/results/boundary-sensitivity.txt`](../bench/results/boundary-sensitivity.txt)). The objection that moves it: the model wrote the tool call at that step, so a reader can say there *is* model text there. Every one of the 236 labelled steps beyond the boundary carries a call, and 96 of them carry more than 200 characters of it; counting each of those as reachable takes the share from 53% to 32% on the whole corpus, and from 53% to 43% on the 115 scored here. Read next to what the calls say, the objection is weak — `{"query": "which two ASEAN capitals are furthest apart"}` asserts nothing that could be checked against a source — but weak is not unmeasured, so treat the finding as **32–53%**, not as one number. The other two objections move it the other way: reading `final_answer`, note stores and `terminate` as model text costs 12 traces out of the boundary set, and including the CodeAct runs gives 53.3% where excluding them gives 57.3%, so the published figure is already the lower of the two. Nothing here checks AgentHallu's labels themselves.
- **23 reachable misses, and they are not one failure.** 16 of the 23 are labelled at step 1 — the plan. The agent read the question wrong or chose the wrong rule before doing anything, and everything after it, the answer included, is faithfully derived from that choice. The chain to a root is intact and the audit is right to close it; the error is in the reasoning, which a provenance audit does not judge. Five more are misreadings of a real source (1946 taken for 1937; the wrong actor from a cast list): the words are in the source, the meaning is not, and those need a check of *whether* a span supports a claim — the next open question in [design.md](design.md#next-steps). The last two had no claim to check. The three groups do not overlap.
- **35% false alarms, and paraphrase is most of them.** `bench/diagnose_agenthallu.py` sorts them by why the chain broke: a paraphrase of a source, a quote the proposer offered that is not verbatim in the source, a computation or formula the agent derived, something the agent stated from its own knowledge. It reads the posted files of the run, which are not in this repository, so the counts per cause are not quoted here. Only the last cause is the audit working as designed on a claim with no external support — and AgentHallu calls those runs clean because the answer was *true*, which is the other question. The rest is the cost of verbatim verification against agents that paraphrase what they read and compute what they report.

**Two changes were measured on the previous run's files and refused** rather than shipped, with the numbers in [`bench/HISTORY.md`](../bench/HISTORY.md): skipping the model's narration of itself in coverage, and requiring the claim's numbers to appear in the funding span. One was adopted: a bare-value answer the segmenter returns nothing locatable for is posted whole, which closed a silent pass on 15 answers that carried no claim at all and so could not be flagged whatever they said.

## Using the benchmark for your own detector

The traces are plain JSON with the labels inside, so anything that flags sentences can be scored on the same construction without running tallystick; the format, the scoring rule, the committed row files and the checks on the labels are described in [`bench/README.md`](../bench/README.md).

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
