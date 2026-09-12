# bench/ — the benchmark, its files, and how to score your own detector

`build.py` constructs the traces, `run.py` scores three methods on them, `ci.py` adds bootstrap intervals, `diagnose.py` sorts a run's false flags by cause, and `agenthallu.py` runs the audit against AgentHallu's real trajectories. The construction and its caveats are under Benchmark in the top-level README; earlier runs are in `HISTORY.md`.

## Using the benchmark for your own detector

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

The per-trace rows of all three runs are committed — `bench/results/2026-09-n100.json` (v0.5.2), `bench/results/2026-09-n100-v0.5.3.json` (v0.5.3) and `bench/results/2026-09-n100-v0.6.json` (v0.6, same traces as v0.5.3), each a JSON object with the rows under `"rows"` and the aggregate under `"summary"`: which traces, the per-sentence truth, and every run's flags on every side — a failed run is `null`, and a trace with any `null` run is one of the dropped (6 in the v0.5.2 file, 1 in the v0.5.3 and v0.6 files). To compare on exactly the same sentences as a table, keep only that file's rows with no `null` run rather than taking every trace in the manifest. `python bench/ci.py <results.json>` recomputes the table from the rows and adds bootstrap intervals and the paired difference, with no model calls. Labels are checked two ways in the test suite: every RAGTruth annotation's span must address the text the annotation carries (the test runs on the 6-item sample in `bench/sample`; the same check over all 14,289 labels in the dataset was run by hand at n=100 and passed), and every answer-sentence label is re-derived independently from the raw annotations and must agree with what `build.py` wrote. Keep the caveats attached to any number you publish: the last hop is verbatim by construction, prevalence is enriched, Data2txt is excluded, and the scored set is conditioned on tallystick's proposer not failing. If your detector only sees the last hop, expect the first row.

## Re-measuring the auditability line

`tallystick check-trace` reports the share of the artifacts a chain passes through
that hold the model's own words rather than a tool's output, and the README quotes
an 80% line from the AgentHallu run. That line is read off that data, not held out,
and stratified by agent framework it is not significant. To re-measure on any
labelled corpus, compute the share with `tallystick.check_trace` per trace and sort
the labelled trajectories by whether the labelled step is beyond the audit's
boundary (`_meta.label_at_tool_boundary` in the AgentHallu adapter). On the v0.7.3
run: at or above 80%, 5 of 24 beyond the boundary; below, 56 of 91; a permutation
test stratified by framework gives p ≈ 0.19, so check that stratification on your
own corpus before trusting the line. A corpus of your own traces will not have the
labels; what it will have is the share, which is the point.
