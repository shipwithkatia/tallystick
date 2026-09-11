# tallystick — version history and earlier benchmark runs

The README keeps the current benchmark table. Earlier runs are kept here in full — tables, intervals, and what each change cost — because the honest record of a measurement includes the versions where it went the wrong way.

## Versions

- **v0.1** — deterministic core: artifacts, steps, claims, entries; three verifier gates; the chain walk; exit codes 0/1/2; an AST test that no verdict-path module imports a model SDK.
- **v0.2** — model-side proposers (`propose/`), kept outside the verdict path; the model posts claims and quotes into a file, the audit reads the file; verbatim-or-nothing locate; containment rule for credits into a summary.
- **v0.3** — LangChain callback recorder: inputs of a step recovered from what was verbatim in its prompt; then, before v0.5, exit 2 as a hard boundary for unreadable input.
- **v0.5** — benchmark harness: two-hop traces from RAGTruth, tallystick vs. a one-hop and a full-history LLM judge, one command.
- **v0.5.1** — harness survives a failed proposer run; JSON parse ignores trailing text.
- **v0.5.2** — first run at n=100; bootstrap intervals, paired difference, label-integrity tests. Result below.
- **v0.5.3** — entry groups close the any-of leak the v0.5.2 review found; a proposer reply that is not JSON is asked for once more; the sentence splitter treats a line break as a boundary; QA traces carry the question as a root; rows carry a trace fingerprint and a mismatch refuses to resume. Result below.
- **v0.6** — precision, from `bench/diagnose.py` on the v0.5.3 posted files: word-level locate (punctuation and case forgiven, never a character), coverage claims for intermediate artifacts, self-evident credits, containment judged on words; rows refuse to resume across versions. Result in the README.
- **v0.7** — real agent trajectories: `tallystick/adapters/agenthallu.py` and `bench/agenthallu.py`; credits proposed on demand from the final answer.

## Earlier benchmark results

The construction, the caveats and the scoring are described under Benchmark in the README; they were the same for these runs except where a section says otherwise.

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
- **Both rows put a false flag on roughly one in six clean sentences.** Neither is a silent filter. By design, tallystick's false flags each trace to one named place — a summary clause the proposer posted prior-only or whose quote the verifier rejected — and the posted file records the claim and the step it broke at, and since v0.6 `python bench/diagnose.py bench/work/posted bench/work/traces` tallies those reasons for a whole run; the judge's are not attributable to anything on disk.

