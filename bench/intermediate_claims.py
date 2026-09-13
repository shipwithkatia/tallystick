"""Would auditing the model's drafts, and not only its answer, find more?

    python bench/intermediate_claims.py --posted <dir> --rows <rows.jsonl>

The question this answers
-------------------------
An audit starts at the final answer and walks back along the credits the model
posted. A draft the answer never cites is therefore never examined - and 16 of
the 23 hallucinations this tool misses on AgentHallu were introduced in one:
the agent invents something while planning, the invention shapes the work, and
the answer states a result without repeating it.

The obvious fix is to close the books on every artifact the model wrote, not
only the answer. This script measures whether that would help, from the posted
files of a run that has already been paid for.

What it can and cannot see
--------------------------
A run made with credits proposed on demand only ever asks "what supports this?"
about claims a chain from the answer reached. Every other claim carries no
entry - and a claim with no entry audits as unsupported whatever it says. So
`unsupported` here means "nobody asked" far more often than "asked and found
nothing", and counting all of them would measure the harness, not the idea.
This project has been caught by that shape before: an offline replay that
stubbed the model measured the stub (`HISTORY.md`, v0.7.3).

So the script reports both, separately and by name: the whole population, and
the subset where a credit was actually requested. Only the second is evidence,
and it is evidence about 12% of the surface on the v0.7.3 run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tallystick import load_run                        # noqa: E402
from tallystick.ledger import FAILING, close_books     # noqa: E402


def group_of(row: dict) -> str:
    """The four populations the answer depends on telling apart."""
    meta = row.get("meta") or {}
    if not meta.get("is_hallucination"):
        return "clean"
    if meta.get("label_at_tool_boundary"):
        return "beyond the boundary"
    return "reachable, flagged" if row["score"].get("flagged") else "reachable, missed"


ORDER = ("reachable, missed", "reachable, flagged", "beyond the boundary", "clean")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--posted", required=True,
                    help="directory of posted traces, named framework__NNN.json")
    ap.add_argument("--rows", required=True, help="the run's rows .jsonl")
    ap.add_argument("--out", metavar="PATH", help="write the report here too")
    args = ap.parse_args(argv)

    posted = Path(args.posted)
    rows = [json.loads(line) for line in
            Path(args.rows).read_text(encoding="utf-8").splitlines() if line.strip()]

    claims = Counter()
    by_group: Counter = Counter()
    reproduced = mismatched = skipped = 0

    for row in rows:
        if "score" not in row:
            skipped += 1
            continue
        path = posted / row["file"].replace("/", "__")
        if not path.exists():
            skipped += 1
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "claims" not in data:
            skipped += 1
            continue

        run = load_run(data)
        balance = close_books(run)

        # The posted files must be the run the rows describe, or none of this
        # means anything. Cheapest check that would fail if they were not.
        want = row["score"].get("final_claims")
        if want is not None:
            if len(balance.final_claim_ids) == want:
                reproduced += 1
            else:
                mismatched += 1
                continue

        asked = {e["claim_id"] for e in data.get("entries", [])}
        finals = set(balance.final_claim_ids)
        failing_asked = False
        for cid, audit in balance.audits.items():
            if cid in finals:
                continue
            artifact = run.artifacts[run.claims[cid].artifact_id]
            if artifact.kind.value != "intermediate":
                continue
            claims["draft claims"] += 1
            if cid in asked:
                claims["  a credit was requested"] += 1
                claims["    and it failed" if audit.status in FAILING
                       else "    and it closed"] += 1
                if audit.status in FAILING:
                    failing_asked = True
            else:
                claims["  never asked - no credit was ever requested"] += 1
        by_group[(group_of(row), failing_asked)] += 1

    lines = [
        "Auditing the model's drafts, not only its answer",
        "=" * 64,
        f"posted files that reproduce the rows: {reproduced}"
        + (f"   (mismatched and skipped: {mismatched})" if mismatched else "")
        + (f"   (no data: {skipped})" if skipped else ""),
        "",
        "Claims on artifacts the model wrote that are not the final answer:",
    ]
    for key, n in claims.items():
        lines.append(f"  {key:<48s} {n:6d}")
    total = claims["draft claims"] or 1
    share = claims["  a credit was requested"] / total
    lines += [
        "",
        f"Only {share:.0%} of them was ever asked about. The rest audit as "
        "unsupported because",
        "nobody asked, not because an answer was looked for and not found - so "
        "the whole",
        "population below is a measurement of the harness, and only the asked "
        "subset is",
        "evidence about the idea.",
        "",
        "Traces with at least one FAILING draft claim that was actually asked:",
        f"  {'group':<24s} {'with':>6s} {'without':>8s}   share",
    ]
    for group in ORDER:
        yes, no = by_group[(group, True)], by_group[(group, False)]
        if yes + no:
            lines.append(f"  {group:<24s} {yes:6d} {no:8d}   {yes / (yes + no):.0%}")
    lines += [
        "",
        "Read the first and last rows against each other. If auditing drafts "
        "found what",
        "the audit of answers misses, the missed traces would fire more often "
        "than the",
        "clean ones. The 'flagged' row is not evidence either way: those traces "
        "are",
        "flagged already, so their chains are exactly the ones credits were "
        "requested for.",
    ]

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
