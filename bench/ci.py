"""Confidence intervals for a finished benchmark run. No API, no model.

    python bench/ci.py bench/results/2026-09-n100.json

Reads the per-trace rows that bench/run.py wrote, recomputes precision, recall
and F1 of the three table rows from them (a check that the file and the table
agree), then bootstraps over TRACES - not sentences, since sentences from one trace share a summary and
are not independent - to put a 95% interval on each F1 and on the paired
difference between the full-history judge and tallystick.

Two kinds of variance are reported by the benchmark and they must not be
confused. The "variance" column in bench/work/RESULTS.md is model noise: the same
trace, rerun. The intervals here are sample noise: the same method, different traces.
The second is what decides whether "A beats B" means anything.

The paired difference is the right comparison: both methods are scored on the
same traces, so a bootstrap sample is drawn once and both are scored on it.
Comparing two separate intervals for overlap would throw that pairing away.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import _usable, prf  # noqa: E402

SIDES = (("one_hop_runs", "one-hop judge"),
         ("full_runs", "full-history judge"),
         ("tallystick_runs", "tallystick"))


def mean_f1(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, float]:
    """The table's number: score each run index over all rows, then average."""
    n_runs = min(len(r[key]) for r in rows)
    per_run = []
    for i in range(n_runs):
        pred = [x for r in rows for x in r[key][i]]
        truth = [t for r in rows for t in r["truth"]]
        per_run.append(prf(pred, truth))
    return {m: statistics.mean(p[m] for p in per_run)
            for m in ("precision", "recall", "f1", "fpr")}


def bootstrap(rows: List[Dict[str, Any]], n: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    draws: Dict[str, List[float]] = {k: [] for k, _ in SIDES}
    diff: List[float] = []
    for _ in range(n):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        f1 = {k: mean_f1(sample, k)["f1"] for k, _ in SIDES}
        for k in draws:
            draws[k].append(f1[k])
        diff.append(f1["full_runs"] - f1["tallystick_runs"])

    def ci(xs: List[float]):
        xs = sorted(xs)
        n = len(xs)
        return xs[round(0.025 * (n - 1))], xs[round(0.975 * (n - 1))]

    return {
        "n_bootstrap": n, "seed": seed,
        "f1_ci": {k: ci(v) for k, v in draws.items()},
        "diff_full_minus_tallystick": {
            "ci": ci(diff),
            "share_le_zero": sum(d <= 0 for d in diff) / n,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", help="results.json written by bench/run.py")
    ap.add_argument("--n", type=int, default=2000, help="bootstrap samples")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    all_rows = data["rows"]
    rows = [r for r in all_rows if _usable(r, skip_judge=False)]
    dropped = [r for r in all_rows if not _usable(r, skip_judge=False)]
    if not rows:
        print("no usable rows", file=sys.stderr)
        return 2

    sents = sum(len(r["truth"]) for r in rows)
    pos = sum(sum(r["truth"]) for r in rows)
    print(f"{len(rows)} traces scored, {len(dropped)} dropped; "
          f"{sents} sentences, {pos} laundered ({pos / sents:.1%})")
    if dropped:
        d_s = sum(len(r["truth"]) for r in dropped)
        d_p = sum(sum(r["truth"]) for r in dropped)
        kinds = sorted({r["meta"]["task_type"] for r in dropped})
        print(f"dropped traces: {d_s} sentences, {d_p} laundered "
              f"({d_p / max(1, d_s):.1%}); task types {kinds}")

    point = {k: mean_f1(rows, k) for k, _ in SIDES}
    boot = bootstrap(rows, args.n, args.seed)
    print()
    print("| method | precision | recall | F1 | 95% CI on F1 (bootstrap over traces) |")
    print("|---|---|---|---|---|")
    for k, label in SIDES:
        p = point[k]
        lo, hi = boot["f1_ci"][k]
        print(f"| {label} | {p['precision']:.2f} | {p['recall']:.2f} | {p['f1']:.2f} "
              f"| [{lo:.2f}, {hi:.2f}] |")
    d = boot["diff_full_minus_tallystick"]
    pd = point["full_runs"]["f1"] - point["tallystick_runs"]["f1"]
    print()
    print(f"F1(full-history judge) - F1(tallystick): {pd:.2f}, "
          f"95% CI [{d['ci'][0]:.2f}, {d['ci'][1]:.2f}]; "
          f"share of bootstrap samples with difference <= 0: {d['share_le_zero']:.1%} "
          f"({args.n} samples, seed {args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
