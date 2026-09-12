"""Why did the audit flag a clean AgentHallu trajectory? No API, no model.

    python bench/diagnose_agenthallu.py bench/work-agenthallu

Reads results.json and the posted files of a `bench/agenthallu.py` run, takes
every clean trajectory (no human label) on which some final-answer claim
failed, and sorts each by the reason its earliest failing chain broke:

  quote_offered_not_verbatim   the proposer pointed at a source, but the words
                               it quoted are not in it word for word
  paraphrase_of_tool_result    no credit posted; 70%+ of the claim's words sit
                               in some tool result or document
  partial_paraphrase           no credit; 40-70% of the words do
  computation_or_formula       no credit; the claim is arithmetic, a formula,
                               or a unit conversion the agent worked out
  unsourced_knowledge          no credit; under 40% of the words in any root -
                               the agent said it from its own knowledge
  unaccounted_span             the cited span was not covered by claims

The word-overlap figures and the formula pattern are diagnostics, not
verifier rules: they say what kind of text the audit could not fund, not
whether it should have. Read the examples, not only the counts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tallystick import audit, load_run  # noqa: E402
from tallystick.ledger import FAILING  # noqa: E402

_WORD = re.compile(r"[a-z0-9]{3,}")
# An equals sign followed by a number or an expression ("1 gallon = 3,785.41
# cm³", "Pc = (3 GM^2) / (8π R^4)"), a TeX fraction, or a spaced operator
# between digits ("2 * 3", "6 + 7"). A date range "2020-2021", a ratio "3/4",
# a phone number or a chemical formula's "-CH=CH-" do not match.
_FORMULA = re.compile(r"=\s*[-(\d\\]|\\frac|\d\s+[=+\-*/^×]\s+\d|\d\s*[*^×]\s*\d")


def classify(posted: dict, claim_id: str, break_reason: str) -> tuple:
    arts = {a["artifact_id"]: a for a in posted["artifacts"]}
    c = next(x for x in posted["claims"] if x["claim_id"] == claim_id)
    text = arts[c["artifact_id"]]["content"][c["start"]:c["end"]]
    if "not fully accounted" in (break_reason or ""):
        return "unaccounted_span", text
    offered = [d for d in posted["_proposal"]["dropped_credits"]
               if d["claim_id"] == claim_id and d.get("proposed_by") != "verbatim"]
    if offered:
        return "quote_offered_not_verbatim", text
    roots = " ".join(a["content"].lower() for a in arts.values()
                     if a["kind"] in ("document", "tool_result"))
    root_words = set(_WORD.findall(roots))
    words = set(_WORD.findall(text.lower()))
    overlap = len(words & root_words) / max(1, len(words))
    if overlap >= 0.7:
        return "paraphrase_of_tool_result", text
    if _FORMULA.search(text):
        return "computation_or_formula", text
    if overlap >= 0.4:
        return "partial_paraphrase", text
    return "unsourced_knowledge", text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("work", help="the bench/agenthallu.py work directory")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args(argv)
    work = Path(args.work)
    data = json.loads((work / "results.json").read_text(encoding="utf-8"))
    rows = [r for r in data["rows"] if "score" in r]
    clean_flagged = [r for r in rows if not r["meta"]["is_hallucination"] and r["score"]["flagged"]]
    buckets: Counter = Counter()
    examples = defaultdict(list)
    for r in clean_flagged:
        posted = json.loads((work / "posted" / r["file"].replace("/", "__")).read_text(encoding="utf-8"))
        balance = audit(load_run(posted))
        step_of = r["meta"]["history_step_of"]
        answer_step = max(step_of.values(), default=0) + 1
        failing = [balance.audits[cid] for cid in balance.final_claim_ids
                   if balance.audits[cid].status in FAILING]
        if not failing:
            continue
        # The claim whose break set the row's break_step in score(): the
        # earliest breaking history step, first claim on a tie.
        a = min(failing, key=lambda x: step_of.get(x.break_step_id or "", answer_step))
        bucket, text = classify(posted, a.break_claim_id or a.claim_id, a.break_reason or "")
        buckets[bucket] += 1
        examples[bucket].append((r["file"], text[:100].replace("\n", " ")))
    n_clean = sum(1 for r in rows if not r["meta"]["is_hallucination"])
    print(f"{n_clean} clean trajectories, {len(clean_flagged)} flagged (false alarms)")
    print()
    print("False alarms by why the earliest failing chain broke:")
    for k, v in buckets.most_common():
        print(f"  {k:<30} {v:>4}  ({v / max(1, len(clean_flagged)):.0%})")
    print()
    for k, _ in buckets.most_common():
        print(f"--- {k}: examples")
        for f, t in examples[k][: args.examples]:
            print(f"  [{f}] {t!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
