"""Run the benchmark: tallystick vs. two LLM judges on two-hop traces.

    python bench/build.py --limit 100            # traces + manifest, no model needed
    python bench/run.py --limit 100              # needs ANTHROPIC_API_KEY
    python bench/run.py --dry-run                # count calls, estimate, no network

Three rows come out, at the final answer, sentence level. Ground truth: a quoted
sentence overlaps a human-annotated hallucinated span.

  one-hop judge     sees summary + answer only. The industry default. Scores
                    ~0 recall BY CONSTRUCTION - the answer is a verbatim quote
                    of its context. Not a competitor; the reason to look further.
  full-history judge  sees documents + summary + answer. The fair opponent.
  tallystick        propose with Claude, audit deterministically. Because the
                    last hop is trivially verifiable, this row is the summary-
                    step detection carried through the chain - the mechanism,
                    not extra detection power. Stated here so nobody has to
                    discover it.

A fourth number, at the summary step, claim level, is the classic hallucination-
detection task where fine-tuned detectors live. tallystick's known false-positive
source there: an abstractive sentence that fuses two source passages has no single
verbatim quote, is posted PRIOR, and is counted as flagged against a sentence the
annotators left alone.

Variance is measured the same way on every side. Judges are rerun `--judge-runs`
times. The proposer is rerun `--proposer-runs` times, so tallystick's END-TO-END
spread is reported next to the fact that auditing one posted file (re-read from
disk) is identical. Sampling is at the API default for every side; nobody gets
temperature 0.

Failures - bad JSON (twice for a judge; the proposer gets one attempt), a reply
without an "unsupported" list, a rate limit, a network error - are counted per side and stored as a placeholder at their run
index. A trace is scored only if EVERY run on EVERY side succeeded, so all rows
and all run indices are computed over exactly the same sentences; that count is
printed once. Each trace's result is appended to rows.jsonl as it finishes, so a
crash loses at most one trace, and rerunning the same command resumes - with the
same model and run counts, and the same build of the traces: all three are
stamped on every row and checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge import JudgeFailed, judge_full_history, judge_one_hop  # noqa: E402

from tallystick import __version__ as TALLYSTICK_VERSION, audit, load_run  # noqa: E402
from tallystick.ledger import FAILING  # noqa: E402


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def prf(pred: List[bool], truth: List[bool]) -> Dict[str, float]:
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    fn = sum(t and not p for p, t in zip(pred, truth))
    tn = sum((not p) and (not t) for p, t in zip(pred, truth))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "fpr": fpr,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": len(truth)}


def sentence_flags_from_audit(posted: Dict[str, Any], balance) -> List[bool]:
    """A constructed answer sentence is flagged if any posted claim overlapping
    it did not close. ANY-of: a sentence with one invented clause is contaminated.
    The judges answer at the same sentence level; tallystick's finer claims are
    reported (claims per sentence) so the granularity is visible."""
    sents = posted["_truth"]["answer_sentences"]
    claims = [c for c in posted["claims"] if c["artifact_id"] == "answer"]
    flags = []
    for s in sents:
        hit = False
        for c in claims:
            if c["start"] < s["end"] and s["start"] < c["end"]:
                if balance.audits[c["claim_id"]].status in FAILING:
                    hit = True
        flags.append(hit)
    return flags


def summary_claim_eval(posted: Dict[str, Any], balance) -> Tuple[List[bool], List[bool]]:
    spans = [(x["start"], x["end"]) for x in posted["_truth"]["summary_hallucinated_spans"]]
    pred, truth = [], []
    for c in posted["claims"]:
        if c["artifact_id"] != "summary":
            continue
        truth.append(any(c["start"] < e and s < c["end"] for s, e in spans))
        pred.append(balance.audits[c["claim_id"]].status in FAILING)
    return pred, truth


# --------------------------------------------------------------------------- #
# one trace
# --------------------------------------------------------------------------- #


def trace_sha(trace: Dict[str, Any]) -> str:
    """Fingerprint of a built trace. Stamped on every row so that a rows.jsonl
    from an earlier build cannot be resumed onto rebuilt traces: seen live at
    v0.5.3, where a resume silently kept 99 v0.5.2 rows and scored one new
    trace, printing a table that meant nothing."""
    blob = json.dumps(trace, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def run_trace(trace: Dict[str, Any], proposer, args, work: Path, name: str) -> Dict[str, Any]:
    from tallystick.propose import post_run
    truth = [s["laundered"] for s in trace["_truth"]["answer_sentences"]]
    row: Dict[str, Any] = {"file": name, "meta": trace["_meta"], "truth": truth,
                           "trace_sha": trace_sha(trace),
                           "tallystick_version": TALLYSTICK_VERSION,
                           "model": args.model, "proposer_runs": args.proposer_runs,
                           "judge_runs": args.judge_runs,
                           "tallystick_runs": [], "summary_runs": [],
                           "audit_identical": None, "claims_per_sentence": [],
                           "proposal": [], "one_hop_runs": [], "full_runs": [],
                           "failures": {"proposer": 0, "one_hop": 0, "full": 0}}

    for k in range(args.proposer_runs):
        try:
            posted = post_run(load_run(trace), proposer)
        except Exception as exc:  # noqa: BLE001 - counted, not hidden
            row["failures"]["proposer"] += 1
            row["tallystick_runs"].append(None)      # keep run indices aligned
            row["summary_runs"].append(None)
            print(f"    proposer failed ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        posted["_truth"] = trace["_truth"]
        posted["_meta"] = trace["_meta"]
        (work / "posted").mkdir(exist_ok=True)
        out = work / "posted" / (name if k == 0 else name.replace(".json", f".run{k}.json"))
        out.write_text(json.dumps(posted, ensure_ascii=False), encoding="utf-8")

        # The audit half: re-load from the file and audit again; must match.
        balance = audit(posted)
        again = audit(json.loads(out.read_text(encoding="utf-8")))
        same = all(balance.audits[c].status == again.audits[c].status
                   for c in balance.audits)
        row["audit_identical"] = same if row["audit_identical"] is None \
            else (row["audit_identical"] and same)

        row["tallystick_runs"].append(sentence_flags_from_audit(posted, balance))
        row["summary_runs"].append(summary_claim_eval(posted, balance))
        n_ans = sum(1 for c in posted["claims"] if c["artifact_id"] == "answer")
        row["claims_per_sentence"].append(n_ans / max(1, len(truth)))
        row["proposal"].append({k2: posted["_proposal"].get(k2, 0) for k2 in
                                ("claims_posted", "credits_posted", "prior_posted",
                                 "coverage_claims", "tolerant_locates",
                                 "self_evident_credits")})

    if not args.skip_judge:
        for _ in range(args.judge_runs):
            for key, fn in (("one_hop", judge_one_hop), ("full", judge_full_history)):
                try:
                    picks = fn(trace, proposer)
                except JudgeFailed:
                    row["failures"][key] += 1
                    row[f"{key}_runs"].append(None)   # keep run indices aligned
                    continue
                row[f"{key}_runs"].append([i in picks for i in range(len(truth))])
    return row


def _usable(row: Dict[str, Any], skip_judge: bool) -> bool:
    """A trace counts only if every run on every side succeeded, so each row and
    each run index of the table is computed over the same sentences."""
    def complete(key, expected):
        runs = row[key]
        return len(runs) == expected and all(r is not None for r in runs)
    if not complete("tallystick_runs", row["proposer_runs"]):
        return False
    if skip_judge:
        return True
    return complete("one_hop_runs", row["judge_runs"]) and \
        complete("full_runs", row["judge_runs"])


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #


def _per_run(rows, key) -> List[Dict[str, float]]:
    """Metric per run index, over the traces where that run succeeded."""
    n_runs = max((len(r[key]) for r in rows), default=0)
    out = []
    for k in range(n_runs):
        pred, truth = [], []
        for r in rows:
            if k < len(r[key]) and r[key][k] is not None:
                pred += r[key][k]
                truth += r["truth"]
        if truth:
            out.append(prf(pred, truth))
    return out


def _spread(runs: List[Dict[str, float]]) -> Optional[float]:
    return (max(x["f1"] for x in runs) - min(x["f1"] for x in runs)) if len(runs) > 1 else None


def _flip_rate(rows, key) -> Optional[float]:
    n = flips = 0
    for r in rows:
        runs = [x for x in r[key] if x is not None]
        if len(runs) < 2:
            continue
        for j in range(len(r["truth"])):
            n += 1
            flips += len({run[j] for run in runs}) > 1
    return flips / n if n else None


def summarise(all_rows, args, elapsed, manifest) -> Dict[str, Any]:
    rows = [r for r in all_rows if _usable(r, args.skip_judge)]
    dropped = len(all_rows) - len(rows)
    truth = [t for r in rows for t in r["truth"]]
    ts = _per_run(rows, "tallystick_runs")
    one = _per_run(rows, "one_hop_runs")
    full = _per_run(rows, "full_runs")
    s_pred = [p for r in rows for p in r["summary_runs"][0][0]]
    s_truth = [t for r in rows for t in r["summary_runs"][0][1]]
    cps = [c for r in rows for c in r["claims_per_sentence"]]
    props = [p for r in rows for p in r["proposal"]]
    ai = [r["audit_identical"] for r in rows if r["audit_identical"] is not None]
    return {
        "model": args.model, "traces": len(rows), "dropped_traces": dropped,
        "answer_sentences": len(truth),
        "laundered_truth": sum(truth), "elapsed_s": round(elapsed),
        "prevalence": {"constructed": sum(truth) / max(1, len(truth)),
                       "natural": manifest.get("natural_sentence_prevalence")},
        "tallystick": {"runs": ts, "f1_spread": _spread(ts),
                       "flip_rate": _flip_rate(rows, "tallystick_runs"),
                       "audit_identical": all(ai) if ai else None},
        "one_hop": {"runs": one, "f1_spread": _spread(one),
                    "flip_rate": _flip_rate(rows, "one_hop_runs")},
        "full": {"runs": full, "f1_spread": _spread(full),
                 "flip_rate": _flip_rate(rows, "full_runs")},
        "summary_step": prf(s_pred, s_truth),
        "granularity": {"claims_per_answer_sentence": statistics.mean(cps) if cps else None},
        "proposer": {
            "claims_posted": sum(p["claims_posted"] for p in props),
            "credits_posted": sum(p["credits_posted"] for p in props),
            "prior_posted": sum(p["prior_posted"] for p in props),
            # v0.6: how much of the posting was search rather than the model.
            "coverage_claims": sum(p.get("coverage_claims", 0) for p in props),
            "tolerant_locates": sum(p.get("tolerant_locates", 0) for p in props),
            "self_evident_credits": sum(p.get("self_evident_credits", 0) for p in props),
        },
        "failures": {k: sum(r["failures"][k] for r in all_rows)
                     for k in ("proposer", "one_hop", "full")},
        "proposer_runs": args.proposer_runs, "judge_runs": args.judge_runs,
    }


def _mean(runs, key) -> float:
    return statistics.mean(x[key] for x in runs) if runs else float("nan")


def render(s: Dict[str, Any]) -> str:
    def row(label, block, extra):
        runs = block["runs"]
        if not runs:
            return f"| {label} | — | — | — | — | not run |"
        return (f"| {label} | {_mean(runs, 'precision'):.2f} | {_mean(runs, 'recall'):.2f} | "
                f"{_mean(runs, 'f1'):.2f} | {_mean(runs, 'fpr'):.2f} | {extra} |")

    def var(block, n):
        if len(block["runs"]) < 2:
            return "1 run"
        flip = block["flip_rate"]
        flip_s = f"{flip:.0%}" if flip is not None else "n/a"
        return (f"{n} runs, F1 spread {block['f1_spread']:.2f}, "
                f"{flip_s} of sentences flip")

    ts, one, full = s["tallystick"], s["one_hop"], s["full"]
    ts_var = var(ts, s["proposer_runs"])
    ai = ts["audit_identical"]
    ts_var += "; audit of one posted file: " + (
        "identical" if ai else ("DIFFERS" if ai is False else "not run"))
    prev = s["prevalence"]
    nat = f"{prev['natural']:.1%}" if prev.get("natural") is not None else "n/a"
    gran = s["granularity"]["claims_per_answer_sentence"]
    gran_s = f"{gran:.2f}" if gran is not None else "n/a"
    lines = [
        "# Benchmark — laundering detection on two-hop RAGTruth traces",
        "",
        f"{s['traces']} traces, {s['answer_sentences']} final-answer sentences, "
        f"{s['laundered_truth']} laundered by human annotation "
        f"(constructed prevalence {prev['constructed']:.1%}; natural sentence-level "
        f"prevalence in the eligible pool {nat} — 60% of items carry at least one "
        f"annotation, sentences within an item are chosen at random). "
        f"{s['dropped_traces']} trace(s) dropped because some run on some side failed; "
        f"every row and every run below is scored on the same {s['answer_sentences']} "
        f"sentences. "
        f"Model for proposer and judges: `{s['model']}`, API-default sampling on every "
        f"side. Wall clock {s['elapsed_s']}s.",
        "",
        "Sentence level, final answer. Means over runs; variance in the last column.",
        "",
        "| method | precision | recall | F1 | FPR | variance |",
        "|---|---|---|---|---|---|",
        row("one-hop judge (summary + answer only)", one, var(one, s["judge_runs"])),
        row("full-history judge (documents + summary + answer)", full, var(full, s["judge_runs"])),
        row("tallystick (propose + audit)", ts, ts_var),
        "",
        "The one-hop row is ~0 recall **by construction** — the answer quotes its "
        "context verbatim — and is the reason to look further back. The tallystick row "
        "is the summary-step detection carried through the chain; the last hop adds no "
        "detection power of its own.",
        "",
        f"Summary-step hallucination detection (claim level, first proposer run, the "
        f"classic task): precision {s['summary_step']['precision']:.2f}, recall "
        f"{s['summary_step']['recall']:.2f}, F1 {s['summary_step']['f1']:.2f} "
        f"over {s['summary_step']['n']} claims. Known false-positive source: abstractive "
        f"sentences fusing two passages have no single verbatim quote and are posted PRIOR.",
        "",
        f"Granularity: {gran_s} tallystick claims per answer sentence. Proposer totals: {s['proposer']['claims_posted']} "
        f"claims, {s['proposer']['credits_posted']} credits, {s['proposer']['prior_posted']} "
        f"prior-only (of which, by search not model: {s['proposer']['coverage_claims']} coverage "
        f"claims, {s['proposer']['tolerant_locates']} tolerant locates, "
        f"{s['proposer']['self_evident_credits']} self-evident credits). "
        f"Failures excluded from metrics: proposer {s['failures']['proposer']}, "
        f"one-hop judge {s['failures']['one_hop']}, full-history judge {s['failures']['full']}.",
        "",
        "Reproduce: `python bench/build.py --limit N && python bench/run.py --limit N`.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default="bench/work")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--judge-runs", type=int, default=3)
    ap.add_argument("--proposer-runs", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args(argv)

    work = Path(args.work)
    manifest_path = work / "manifest.json"
    if not manifest_path.exists():
        print("no manifest; run bench/build.py first", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = manifest["order"][: args.limit]
    traces = [(n, json.loads((work / "traces" / n).read_text(encoding="utf-8")))
              for n in names]

    # estimate: per proposer run, 2 segmentations + ~6 summary claims + answer
    # sentences; per judge run, 2 calls (one-hop + full). Credit calls carry the
    # whole document set, so the per-call price is a rough mean, not a floor.
    est = 0
    for _, t in traces:
        est += args.proposer_runs * (2 + 6 + len(t["_truth"]["answer_sentences"]))
        est += 0 if args.skip_judge else 2 * args.judge_runs
    judge_note = "judges skipped" if args.skip_judge else f"judge runs {args.judge_runs}"
    print(f"{len(traces)} traces, ~{est} model calls, roughly ${est * 0.004:.0f}–"
          f"${est * 0.009:.0f} at Sonnet prices and {est * 3 / 3600:.1f}–"
          f"{est * 5 / 3600:.1f} h sequential; proposer runs {args.proposer_runs}, "
          f"{judge_note}")
    if args.dry_run:
        return 0

    from tallystick.propose import AnthropicProposer
    proposer = AnthropicProposer(model=args.model)

    # Resume: rows already on disk for the traces in this invocation are kept and
    # their traces skipped. Rows must carry the same model, run counts and trace
    # fingerprint, or the table would be labelled with settings (or data) it was
    # not run at.
    rows_path = work / "rows.jsonl"
    rows: List[Dict[str, Any]] = []
    if rows_path.exists():
        lines = rows_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    print("warning: last line of rows.jsonl is truncated (killed "
                          "mid-write); that trace will be rerun", file=sys.stderr)
                    continue
                raise
        wanted = {n: trace_sha(t) for n, t in traces}
        rows = [r for r in rows if r["file"] in wanted]
        mismatch = [r["file"] for r in rows if (r.get("model"), r.get("proposer_runs"),
                                                r.get("judge_runs"))
                    != (args.model, args.proposer_runs, args.judge_runs)]
        if mismatch:
            print(f"rows.jsonl was produced with different --model/--proposer-runs/"
                  f"--judge-runs ({len(mismatch)} row(s)); delete it or rerun with the "
                  f"same settings", file=sys.stderr)
            return 2
        stale = [r["file"] for r in rows if r.get("trace_sha") != wanted[r["file"]]]
        if stale:
            print(f"rows.jsonl was produced from different traces ({len(stale)} row(s) "
                  f"do not match the files in {work / 'traces'}; the benchmark was "
                  f"rebuilt, or the rows predate trace fingerprints). Move rows.jsonl "
                  f"aside and rerun; posted/ and results.json are rewritten by the run.",
                  file=sys.stderr)
            return 2
        old = [r["file"] for r in rows if r.get("tallystick_version") != TALLYSTICK_VERSION]
        if old:
            print(f"rows.jsonl was produced by a different tallystick version "
                  f"({len(old)} row(s); this is {TALLYSTICK_VERSION}). The traces are the "
                  f"same but the code that scores them is not; a resumed table would mix "
                  f"the two. Move rows.jsonl aside and rerun.", file=sys.stderr)
            return 2
        done = {r["file"] for r in rows}
        traces = [(n, t) for n, t in traces if n not in done]
        print(f"resuming: {len(rows)} trace(s) already done, {len(traces)} to go")

    t0, consecutive_failures = time.time(), 0
    with rows_path.open("a", encoding="utf-8") as fh:
        for i, (name, trace) in enumerate(traces, 1):
            print(f"[{i}/{len(traces)}] {name}")
            row = run_trace(trace, proposer, args, work, name)
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            fmt = lambda runs: [sum(x) if x is not None else None for x in runs]  # noqa: E731
            print(f"    truth={sum(row['truth'])}/{len(row['truth'])} "
                  f"tallystick={fmt(row['tallystick_runs'])} "
                  f"one-hop={fmt(row['one_hop_runs'])} full={fmt(row['full_runs'])}")
            consecutive_failures = 0 if _usable(row, args.skip_judge) else consecutive_failures + 1
            if consecutive_failures >= 5:
                print("five unusable traces in a row - stopping; check the key, quota "
                      "or network, then rerun the same command to resume",
                      file=sys.stderr)
                break

    report = summarise(rows, args, time.time() - t0, manifest)   # wall clock: this session only
    (work / "results.json").write_text(
        json.dumps({"rows": rows, "summary": report}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    (work / "RESULTS.md").write_text(render(report), encoding="utf-8")
    print()
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
