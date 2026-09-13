"""Does the auditability share track whether a hallucination is reachable?

    git clone https://github.com/liuxuannan/AgentHallu bench/work-agenthallu/AgentHallu
    python bench/auditability_agenthallu.py

No model, no API key, no cost: `tallystick check-trace` reads the file and
nothing else, so this runs over every trajectory in the dataset rather than the
subset a paid run could afford.

For each of AgentHallu's 693 trajectories it computes the share `check-trace`
reports - of the artifacts a chain passes through, how many hold the model's own
words rather than a tool's output - and compares it with whether the human label
sits at a step the audit cannot reach (`_meta.label_at_tool_boundary`).

The headline table is descriptive and useful: if a trace is below the cut, a
hallucination in it lands beyond the audit more often than if it is above. Three
things are true about it and they belong together, so this script prints all
three.

The share is counting. Nothing about it is in doubt.

Most of the banding is arithmetic, and the placebo below shows it: replace the
human label with a step drawn at random from the same trajectory - a label that
knows nothing about the hallucination - and the same ordering appears, because a
trace with more tool-only steps makes any step more likely to be tool-only.

But the placebo's rates are lower than the real ones in both bands. Real
hallucinations do sit at tool boundaries more often than chance puts them, which
is a fact about agents rather than about the share, and it is why the boundary is
worth measuring at all. `--enrichment` prints that comparison: observed against
what each trace's own composition predicts.

The framework-stratified permutation test is printed beside the placebo and
nowhere else. It rules out "some frameworks record thinly and also hallucinate
past the boundary", which is a real confounder but not the dominant one, and it
rejects for the placebo too, so it is not evidence for the cut.

Nothing is held out; the cut is chosen on the same data it is scored on.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agenthallu import to_trace  # noqa: E402
from tallystick import check_trace, load_run  # noqa: E402

Row = Dict[str, object]


def rows_for(data: Path, *, include_codeact: bool = True) -> List[Row]:
    out: List[Row] = []
    for path in sorted(data.glob("*/*.json")):
        name = f"{path.parent.name}/{path.name}"
        trace = to_trace(json.loads(path.read_text(encoding="utf-8")), name=name)
        meta = trace["_meta"]
        if meta["codeact"] and not include_codeact:
            continue
        a = check_trace(load_run({k: trace[k] for k in ("artifacts", "steps")}),
                        meta=meta)
        if a.reachable_share is None:
            continue
        # Which history steps recorded a tool result and nothing else - the same
        # rule `label_at_tool_boundary` applies to the labelled step. Kept so the
        # placebo can ask "would a step picked at random look beyond the audit?"
        art_step = {o: meta["history_step_of"].get(st["step_id"])
                    for st in trace["steps"] for o in st["outputs"]}
        per: Dict[int, List[str]] = defaultdict(list)
        for art in trace["artifacts"]:
            h = art_step.get(art["artifact_id"])
            if h is not None:
                per[h].append(art["kind"])
        out.append({"file": name, "framework": meta["framework"] or path.parent.name,
                    "hallucinated": meta["is_hallucination"],
                    "beyond": meta["label_at_tool_boundary"],
                    "share": a.reachable_share,
                    "history_steps": sorted(per),
                    "tool_only_steps": {h for h, ks in per.items()
                                        if ks and all(k == "tool_result" for k in ks)}})
    return out


def table(rows: List[Row], cut: float) -> Tuple[int, int, int, int]:
    hi = [r for r in rows if r["share"] >= cut]
    lo = [r for r in rows if r["share"] < cut]
    return len(hi), sum(1 for r in hi if r["beyond"]), len(lo), sum(1 for r in lo if r["beyond"])


def enrichment(rows: List[Row], cut: float) -> List[Tuple[str, int, int, float]]:
    """Observed labels at a tool boundary against what each trace's own
    composition predicts. `p_tool` is that trace's share of artifact-bearing
    history steps that recorded a tool result and nothing else - exactly the
    probability the placebo draws from - so the ratio says how much more often
    the human label lands there than a step picked at random would."""
    def block(rs: List[Row], name: str) -> Tuple[str, int, int, float]:
        obs = sum(1 for r in rs if r["beyond"])
        exp = sum(len(r["tool_only_steps"]) / len(r["history_steps"]) for r in rs
                  if r["history_steps"])
        return name, obs, len(rs), (obs / exp if exp else float("nan"))
    return [block(rows, "all labelled"),
            block([r for r in rows if r["share"] >= cut], f"at or above {cut:.0%}"),
            block([r for r in rows if r["share"] < cut], f"below {cut:.0%}")]


def within_trace_mc(rows: List[Row], *, draws: int = 20000, seed: int = 0
                    ) -> Tuple[int, int, int]:
    """Is the label at a tool boundary more often than composition predicts?

    Returns (observed, draws_at_least_observed, draws).

    This is a **Monte-Carlo test, not a permutation test**, and the difference is
    not pedantry. Nothing is shuffled here: each trace independently draws a coin
    at its own rate of tool-only steps, and the draws are summed. That is a
    parametric null - "every trace hallucinates at a step picked at its own
    composition rate" - and it is the null the placebo also draws from, which is
    why this test is the one that can see past the arithmetic.

    `stratified_p` below IS a permutation test: it shuffles `beyond` inside each
    framework. Calling this one a permutation test (as an earlier version of this
    script, the README and the committed report all did) claims a null that was
    never sampled. `test_bench.py` now fails the build if the word comes back.

    The count is returned rather than a p-value because with 20000 draws and zero
    hits the p-value is (0+1)/(20000+1) - the floor of what this many draws can
    resolve, not a measured magnitude. Report it as "none of N draws reached the
    observed count", and let the reader see N.
    """
    observed = sum(1 for r in rows if r["beyond"])
    rnd = random.Random(seed)
    at_least = 0
    for _ in range(draws):
        total = sum(1 for r in rows if r["history_steps"]
                    and rnd.random() < len(r["tool_only_steps"]) / len(r["history_steps"]))
        if total >= observed:
            at_least += 1
    return observed, at_least, draws


def placebo_rows(rows: List[Row], data: Path, seed: int) -> List[Row]:
    """The same rows with `beyond` replaced by "a step drawn at random from this
    trajectory is tool-only". Carries no information about the hallucination."""
    rnd = random.Random(seed)
    out: List[Row] = []
    for r in rows:
        steps = r["tool_only_steps"], r["history_steps"]
        tool_only, all_steps = steps
        if not all_steps:
            continue
        out.append(dict(r, beyond=rnd.choice(sorted(all_steps)) in tool_only))
    return out


def stratified_p(rows: List[Row], cut: float, *, draws: int = 20000, seed: int = 0
                 ) -> Tuple[float, Dict[str, Tuple[int, int, int, int]]]:
    """Permutation test, shuffling `beyond` within each framework.

    The statistic is the pooled count of high-share traces that are reachable,
    summed over the informative strata only - a framework whose traces all sit
    on one side of the cut can say nothing and is dropped, from the statistic
    and from the shuffle alike.
    """
    by: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by[r["framework"]].append(r)
    informative = {k: v for k, v in by.items()
                   if len({r["share"] >= cut for r in v}) > 1}
    observed = sum(1 for v in informative.values() for r in v
                   if r["share"] >= cut and not r["beyond"])
    rnd = random.Random(seed)
    at_least = 0
    for _ in range(draws):
        total = 0
        for v in informative.values():
            highs = [r["share"] >= cut for r in v]
            beyond = [r["beyond"] for r in v]
            rnd.shuffle(beyond)
            total += sum(1 for h, b in zip(highs, beyond) if h and not b)
        if total >= observed:
            at_least += 1
    # (r + 1) / (n + 1): a permutation p is an estimate, and 0/20000 is not 0.
    return (at_least + 1) / (draws + 1), {k: table(v, cut) for k, v in informative.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="bench/work-agenthallu/AgentHallu/AgentHallu")
    ap.add_argument("--cut", type=float, default=0.8)
    ap.add_argument("--draws", type=int, default=20000)
    ap.add_argument("--exclude-codeact", action="store_true",
                    help="drop the runs whose tools execute inside model-written code")
    ap.add_argument("--json", dest="json_out", metavar="PATH")
    args = ap.parse_args(argv)

    data = Path(args.data)
    if not data.is_dir():
        print(f"not a directory: {data}", file=sys.stderr)
        return 2
    rows = rows_for(data, include_codeact=not args.exclude_codeact)
    labelled = [r for r in rows if r["hallucinated"]]
    print(f"{len(rows)} trajectories, {len(labelled)} labelled, "
          f"{sum(1 for r in labelled if r['beyond'])} of them beyond the audit's boundary")
    print()
    print("share cut   at or above: beyond/n        below: beyond/n")
    for cut in (0.5, 0.6, 0.7, 0.8, 0.9):
        n_hi, b_hi, n_lo, b_lo = table(labelled, cut)
        print(f"  {cut:.1f}        {b_hi:>4}/{n_hi:<4} {b_hi / max(1, n_hi):>4.0%}"
              f"            {b_lo:>4}/{n_lo:<4} {b_lo / max(1, n_lo):>4.0%}")
    print()
    print(f"PLACEBO at the {args.cut:.0%} cut - the same table with the human label replaced")
    print("by 'a step drawn at random from this trajectory is tool-only', which knows")
    print("nothing about the hallucination:")
    for seed in range(5):
        pl = placebo_rows(labelled, data, seed)
        n_hi, b_hi, n_lo, b_lo = table(pl, args.cut)
        print(f"  draw {seed}      {b_hi:>4}/{n_hi:<4} {b_hi / max(1, n_hi):>4.0%}"
              f"            {b_lo:>4}/{n_lo:<4} {b_lo / max(1, n_lo):>4.0%}")
    print("The placebo reproduces the ORDERING, not the rates: a trace with more")
    print("tool-only steps makes ANY step more likely to be tool-only, so most of the")
    print("banding above is arithmetic. But the real labels sit at a tool boundary")
    print("more often than the placebo does, in both bands - hallucinations really do")
    print("land at tool boundaries more than chance puts them, which is a fact about")
    print("agents rather than about the share. The table is 'how much of my run is")
    print("out of reach'; it is not a predictor of where the hallucination is.")
    print()
    print("ENRICHMENT - the real label against what each trace's own composition")
    print("predicts, which is the probability the placebo draws from:")
    for name, obs, n, ratio in enrichment(labelled, args.cut):
        exp = obs / ratio if ratio == ratio and ratio else float("nan")
        print(f"  {name:20} n={n:<4} observed {obs:>4}   expected {exp:6.1f}   {ratio:.2f}x")
    obs, hits, draws = within_trace_mc(labelled, draws=args.draws)
    print(f"  within-trace Monte Carlo (each trace drawn at its own tool-only rate,")
    print(f"  nothing shuffled): {hits} of {draws} draws reached {obs}"
          + ("  -> below this test's resolution" if hits == 0 else ""))
    print("So the banding is mostly arithmetic and the labels are still enriched:")
    print("hallucinations land at tool boundaries more than chance puts them.")
    p, strata = stratified_p(labelled, args.cut, draws=args.draws)
    print()
    print(f"At the {args.cut:.0%} cut, by framework (only those with traces on both sides):")
    for fw, (n_hi, b_hi, n_lo, b_lo) in sorted(strata.items()):
        print(f"  {fw:26} at or above {b_hi}/{n_hi:<3}  below {b_lo}/{n_lo}")
    print(f"\npermutation test stratified by framework, {args.draws} draws: p = {p:.3f}.")
    print("Read it next to the placebo, not on its own: it rules out a between-framework")
    print("confounder and rejects for the placebo too, so it is not evidence for the cut.")
    print("Nothing here is held out; the cut is chosen on the data it is scored on.")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"cut": args.cut, "draws": args.draws, "p": p,
             "rows": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()}
                      for r in rows]}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
