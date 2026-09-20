# bench/ — the benchmark, its files, and how to score your own detector

`build.py` constructs the traces, `run.py` scores three methods on them, `ci.py` adds bootstrap intervals, `diagnose.py` sorts a run's false flags by cause, and `agenthallu.py` runs the audit against AgentHallu's real trajectories. The construction and its caveats are in [`docs/benchmark.md`](../docs/benchmark.md); earlier runs are in `HISTORY.md`.

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

The per-trace rows of all three runs are committed — `bench/results/2026-09-n100.json` (v0.5.2), `bench/results/2026-09-n100-v0.5.3.json` (v0.5.3) and `bench/results/2026-09-n100-v0.6.json` (v0.6, same traces as v0.5.3), each a JSON object with the rows under `"rows"` and the aggregate under `"summary"`: which traces, the per-sentence truth, and every run's flags on every side — a failed run is `null`, and a trace with any `null` run is one of the dropped (6 in the v0.5.2 file, 1 in the v0.5.3 and v0.6 files). To compare on exactly the same sentences as a table, keep only that file's rows with no `null` run rather than taking every trace in the manifest. `python bench/ci.py <results.json>` recomputes the table from the rows and adds bootstrap intervals and the paired difference, with no model calls. Labels are checked two ways in the test suite: every RAGTruth annotation's span must address the text the annotation carries (the test runs on the 6-item sample in `bench/sample`; `python bench/check_labels.py` runs the same check over every label in the dataset — 14,289, all matching — once `bench/build.py` has cloned RAGTruth into `bench/work/RAGTruth`), and every answer-sentence label is re-derived independently from the raw annotations and must agree with what `build.py` wrote. Keep the caveats attached to any number you publish: the last hop is verbatim by construction, prevalence is enriched, Data2txt is excluded, and the scored set is conditioned on tallystick's proposer not failing. If your detector only sees the last hop, expect the first row.

## Re-measuring the auditability line

`bench/auditability_agenthallu.py` does it, over every trajectory in AgentHallu
rather than the benchmark's subset — `check-trace` calls no model, so the whole
dataset is free. It prints the 2x2 table at five cuts, the per-framework tables at
the chosen one, and a permutation test that shuffles within each framework, which
is the test that matters: a framework that both records thinly and hallucinates
past the boundary would otherwise manufacture a pooled association carrying no
information about any single trace. Read the per-framework tables before the
p-value. `--exclude-codeact` drops the runs whose tools execute inside
model-written code; `--cut` moves the line; `--json` writes every row.

Nothing in it is held out. On another corpus, run it before trusting the 80%.

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
