"""tallystick against AgentHallu's human step labels. Real agent runs, not built.

    git clone https://github.com/liuxuannan/AgentHallu bench/work-agenthallu/AgentHallu
    python bench/agenthallu.py --dry-run
    python bench/agenthallu.py                      # needs ANTHROPIC_API_KEY
    python bench/agenthallu.py --select factual     # the reachable subset (default)

AgentHallu (Liu et al., 2026; CC BY 4.0) has 693 runs of seven agent
frameworks; 443 carry a human label naming the step that introduced the
hallucination, 250 are clean. This harness converts a selection of them with
`tallystick.adapters.agenthallu`, posts each with the proposer (one run,
credits on demand from the final answer), audits, and compares the audit's
`break_step_id` with the labelled step.

What is compared, and what is not
---------------------------------
The label answers "which step made the answer wrong"; the audit answers "at
which step does the chain from the answer to a root break". Those are the same
question only where the hallucination is a fact stated in model text with
nothing behind it. So the selection defaults to the categories where that is
plausible - Planning/Fact Derive, Reasoning/Factual Reasoning, the three
Retrieval sub-categories - and every trajectory is also tagged with whether
its labelled step carries model prose at all. If the labelled step is a tool
call and its result only (`label_at_tool_boundary`), the hallucination lives
inside a tool result, which is a root for this audit: there is no page in the
file to check the tool's digest against, so the audit cannot reach it by
construction. Those rows are counted apart, not as misses. CodeAct
trajectories (55 of 693), whose tools run inside model-written code so that
one execution log mixes the world's text with the model's, are excluded by
default (`--include-codeact` keeps them): the file does not mark where the
boundary runs inside the log, and no reading of it is honest.

Per trajectory the row records: whether any final-answer claim failed
(`flagged`), the earliest failing history step (`break_step`), the labelled
step, and whether they match exactly and within one step. Clean trajectories
give the false-alarm rate. Rows append to rows.jsonl and resume; a rows file
from another tallystick version is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tallystick import __version__ as TALLYSTICK_VERSION, audit, load_run  # noqa: E402
from tallystick.adapters.agenthallu import DEFAULT_MAX_TOOL_CHARS, to_trace  # noqa: E402
from tallystick.ledger import FAILING  # noqa: E402

FACTUAL = {("Planning Hallucination", "Fact Derive"),
           ("Reasoning Hallucination", "Factual Reasoning"),
           ("Retrieval Hallucination", "Summarize Misalign"),
           ("Retrieval Hallucination", "Context Misalign"),
           ("Retrieval Hallucination", "Query Misalign")}


def select(data: Path, which: str, seed: int, limit: Optional[int],
           include_codeact: bool = False) -> List[Path]:
    """Hallucinated trajectories of the chosen kind, plus as many clean ones
    from the same frameworks (per framework, seeded), in a fixed order."""
    files = sorted(data.glob("*/*.json"))
    bad: List[Path] = []
    clean: Dict[str, List[Path]] = defaultdict(list)
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        if not include_codeact and to_trace(obj)["_meta"]["codeact"]:
            continue
        if str(obj.get("is_hallucination", "")).lower() != "true":
            clean[f.parent.name].append(f)
            continue
        key = (obj.get("hallucination_category"), obj.get("hallucination_subcategory"))
        if which == "all" or (which == "retrieval" and key[0] == "Retrieval Hallucination") \
                or (which == "factual" and key in FACTUAL):
            bad.append(f)
    rng = random.Random(seed)
    rng.shuffle(bad)
    if limit:
        bad = bad[:limit]     # limit the labelled runs, then match clean to them
    need = Counter(f.parent.name for f in bad)
    chosen_clean: List[Path] = []
    for fw, n in sorted(need.items()):
        pool = sorted(clean[fw])
        rng.shuffle(pool)
        chosen_clean.extend(pool[:n])
        if len(pool) < n:
            print(f"note: {fw} has {len(pool)} clean trajectories for {n} labelled ones",
                  file=sys.stderr)
    out = bad + chosen_clean
    rng.shuffle(out)
    return out


class ReuseProposer:
    """Answer from previous runs' posted files where the same question was
    asked; send only new questions to the real proposer.

    A posted file records everything the model returned for a trajectory:
    the claims it segmented (posted and dropped) and, for every claim it was
    asked about, the credits it offered (posted as entries, or dropped with a
    reason). A rerun after a change to the deterministic side of the pipeline
    - the locate, the containment rule - needs the same answers to the same
    questions; asking the model again costs money and adds model noise for
    no information. So a segment call for an artifact whose text matches the
    old file is answered from it, and a credit call for a claim the old run
    asked about (it has entries, a PRIOR:model entry counts) is answered from
    it. A claim no old run reached, or a trajectory with no old file, goes to
    `inner`. Several directories are searched in the order given; the first
    that can answer does. `calls` counts what went where.

    Only what the model said is replayed. A claim the pipeline added itself
    (`proposed_by: "coverage"`, a sentence of a model step the segmenter did
    not return; `"whole-answer"`, a bare-value answer posted whole) is left
    out: the pipeline adds coverage again on its own, and
    replaying it as the segmenter's word would post it on any artifact with
    the same text - in AgentHallu the final answer usually repeats the last
    step word for word, and a final answer gets no coverage by design. Files
    written before claims were tagged (tallystick < 0.7.2) cannot be told
    apart that way, so for them the artifact of kind `final_answer` is
    preferred among those with the same text (it never carried coverage),
    and a directory that was itself written by a reuse run (its
    rows.meta.json says so) is used for credits only, never for segments:
    such files may carry replayed coverage on the answer already. Claims
    replayed from an untagged file are posted as `proposed_by: "replay"`, not
    as the model's, since which of them were coverage cannot be told; the
    `coverage_claims` counter of such a rerun counts only what the pipeline
    added on top, and is not comparable with a fresh run's."""

    def __init__(self, inner, posted_dirs):
        self.inner = inner
        self.name = getattr(inner, "name", type(inner).__name__)
        if isinstance(posted_dirs, (str, Path)):
            posted_dirs = [posted_dirs]
        self.posted_dirs = [Path(d) for d in posted_dirs]
        self.calls = Counter()
        self.claim_tag: Optional[str] = None       # read by the pipeline after each segment reply
        self._olds: List[Dict[str, Any]] = []      # one per directory that has the file
        self._used: set = set()                     # (directory index, claim_id)

    @staticmethod
    def _written_by_reuse(posted_dir: Path, old: Dict[str, Any]) -> bool:
        if old["_proposal"].get("reused_from"):
            return True
        meta = posted_dir.parent / "rows.meta.json"
        if not meta.exists():
            return False
        try:
            return bool(json.loads(meta.read_text(encoding="utf-8")).get("reuse"))
        except (ValueError, OSError):
            return False

    def start(self, name: str) -> None:
        self._olds = []
        self._used = set()
        for i, d in enumerate(self.posted_dirs):
            path = d / name.replace("/", "__")
            if not path.exists():
                continue
            old = json.loads(path.read_text(encoding="utf-8"))
            arts = {a["artifact_id"]: a["content"] for a in old["artifacts"]}
            old["_claim_text"] = {c["claim_id"]: arts[c["artifact_id"]][c["start"]:c["end"]]
                                  for c in old["claims"]}
            old["_tagged"] = bool(old["claims"]) and all("proposed_by" in c for c in old["claims"])
            old["_segments_ok"] = old["_tagged"] or not self._written_by_reuse(d, old)
            old["_index"] = i
            self._olds.append(old)

    def complete(self, system: str, user: str) -> str:
        m = re.search(r"(<+)\n(.*?)\n>+\n", user, re.S)
        body = m.group(2) if m else None
        self.claim_tag = None
        if body is not None and user.startswith("TEXT:"):
            for old in self._olds:
                if not old["_segments_ok"]:
                    continue
                found = self._segment_of(old, body)
                if found is not None:
                    claims, replayed, from_answer = found
                    self.calls["segment_reused"] += 1
                    # An untagged file that passed `_segments_ok` is a fresh
                    # run's; its final answer never carried coverage, so those
                    # claims are the model's. Elsewhere in it they may not be.
                    self.claim_tag = ("replay" if replayed or not (old["_tagged"] or from_answer)
                                      else None)
                    return json.dumps({"claims": claims}, ensure_ascii=False)
        elif body is not None:
            for old in self._olds:
                cid = self._asked_claim(old, body, user)
                if cid is not None:
                    self.calls["credits_reused"] += 1
                    return json.dumps({"credits": self._credits_of(old, cid)}, ensure_ascii=False)
        self.calls["model"] += 1
        reply = self.inner.complete(system, user)
        self.claim_tag = getattr(self.inner, "claim_tag", None)
        return reply

    @staticmethod
    def _segment_of(old: Dict[str, Any], body: str) -> Optional[Tuple[List[str], bool, bool]]:
        """The segmenter's claims for an artifact with this text; whether any
        of them was itself replayed from an untagged file (then the model is
        not known to have said them, and they stay `replay`); and whether they
        were read from the final answer."""
        # A root with the same text (OpenManus copies a `browser_use` digest
        # into its answer) has no claims and is not an answer to the question.
        twins = [a for a in old["artifacts"] if a["content"] == body
                 and a["kind"] in ("intermediate", "final_answer")]
        if not twins:
            return None
        # The segment prompt is the text alone, so any twin's answer is the
        # model's answer to this question; the final answer never carries
        # coverage, so it is the safe one to read in an untagged file.
        art = next((a for a in twins if a["kind"] == "final_answer"), twins[0])
        aid = art["artifact_id"]
        mine = [c for c in old["claims"] if c["artifact_id"] == aid
                and c.get("proposed_by") not in ("coverage", "whole-answer")]
        claims = [old["_claim_text"][c["claim_id"]] for c in mine]
        claims += [d["text"] for d in old["_proposal"]["dropped_claims"]
                   if d["artifact_id"] == aid]
        return (claims, any(c.get("proposed_by") == "replay" for c in mine),
                art["kind"] == "final_answer")

    def _asked_claim(self, old: Dict[str, Any], text: str, user: str) -> Optional[str]:
        srcs = set(re.findall(r"\[artifact_id: ([^\]]+)\]", user))
        inputs: Dict[str, set] = {}
        for st in old["steps"]:
            for o in st["outputs"]:
                inputs[o] = set(st["inputs"])
        asked = {e["claim_id"] for e in old["entries"]}
        for c in old["claims"]:
            cid = c["claim_id"]
            key = (old["_index"], cid)
            if key in self._used or cid not in asked or old["_claim_text"][cid] != text:
                continue
            if inputs.get(c["artifact_id"]) == srcs:
                self._used.add(key)
                return cid
        return None

    @staticmethod
    def _credits_of(old: Dict[str, Any], cid: str) -> List[Dict[str, str]]:
        arts = {a["artifact_id"]: a["content"] for a in old["artifacts"]}
        out: List[Dict[str, str]] = []
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in old["entries"]:
            if e["claim_id"] != cid or not e["account"].startswith("EVIDENCE:"):
                continue
            if e.get("proposed_by") == "verbatim":
                continue   # the pipeline finds these again by itself
            groups[e.get("group") or e["entry_id"]].append(e)
        for es in groups.values():
            aid = es[0]["account"][len("EVIDENCE:"):].rpartition("#")[0]
            spans = [tuple(int(x) for x in e["account"].rpartition("#")[2].split("-")) for e in es]
            lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
            out.append({"artifact_id": aid, "quote": arts[aid][lo:hi]})
        seen = {(c["artifact_id"], c["quote"]) for c in out}
        for d in old["_proposal"]["dropped_credits"]:
            if d["claim_id"] != cid or d.get("proposed_by") == "verbatim" or not d.get("artifact_id"):
                continue
            key = (d["artifact_id"], d["quote"])
            if key in seen:
                continue
            # A quote that straddled a claim was posted for the part it covered
            # (a group above) and dropped for the part it clipped; offering the
            # original quote again would post it twice.
            if d["reason"].startswith("quote only partially overlaps") and any(
                    d["artifact_id"] == c["artifact_id"] and c["quote"] in d["quote"] for c in out):
                continue
            seen.add(key)
            out.append({"artifact_id": d["artifact_id"], "quote": d["quote"]})
        return out


def trace_sha(trace: Dict[str, Any]) -> str:
    blob = json.dumps({k: trace[k] for k in ("artifacts", "steps")}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def score(trace: Dict[str, Any], posted: Dict[str, Any]) -> Dict[str, Any]:
    meta = trace["_meta"]
    balance = audit(load_run(posted))
    step_of = meta["history_step_of"]
    # A break at the answer step itself (the answer's claim has nothing behind
    # it) comes after every history step: number it one past the last step
    # (step numbers are not always contiguous, so not len(history) + 1).
    answer_step = max(step_of.values(), default=0) + 1
    breaks: List[int] = []
    for cid in balance.final_claim_ids:
        a = balance.audits[cid]
        if a.status in FAILING:
            breaks.append(step_of.get(a.break_step_id or "", answer_step))
    label = meta["hallucination_step"]
    earliest = min(breaks) if breaks else None
    return {
        "flagged": bool(breaks),
        "break_steps": sorted(set(breaks)),
        "break_step": earliest,
        "label_step": label,
        # `exact`: the earliest break is the labelled step. `label_hit`: the
        # labelled step is among the breaks - an unrelated unfunded hedge
        # quoted from an earlier step drags `earliest` forward without making
        # the audit wrong about the labelled step.
        "exact": label is not None and earliest == label,
        "label_hit": label is not None and label in breaks,
        "within_one": label is not None and earliest is not None
        and abs(earliest - label) <= 1,
        "answer_step": answer_step,
        "final_claims": len(balance.final_claim_ids),
        "laundering_rate": balance.laundering_rate,
    }


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    bad = [r for r in rows if r["meta"]["is_hallucination"]]
    clean = [r for r in rows if not r["meta"]["is_hallucination"]]
    reach = [r for r in bad if not r["meta"]["label_at_tool_boundary"]]
    beyond = [r for r in bad if r["meta"]["label_at_tool_boundary"]]

    def block(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rs)
        return {"n": n,
                "flagged": sum(r["score"]["flagged"] for r in rs),
                "exact": sum(r["score"]["exact"] for r in rs),
                "label_hit": sum(r["score"]["label_hit"] for r in rs),
                "within_one": sum(r["score"]["within_one"] for r in rs)}

    by_sub: Dict[str, Dict[str, Any]] = {}
    for key in sorted({(r["meta"]["hallucination_category"], r["meta"]["hallucination_subcategory"])
                       for r in reach}):
        by_sub[" / ".join(str(k) for k in key)] = block(
            [r for r in reach if (r["meta"]["hallucination_category"],
                                  r["meta"]["hallucination_subcategory"]) == key])
    return {
        "tallystick_version": TALLYSTICK_VERSION,
        "hallucinated": block(bad),
        "reachable": block(reach),
        "beyond_tool_boundary": block(beyond),
        "by_subcategory_reachable": by_sub,
        "clean": {"n": len(clean), "flagged": sum(r["score"]["flagged"] for r in clean)},
        # A trajectory whose final answer got no claim (the segmenter returned
        # none, or none it returned was in the text) cannot be flagged; it is
        # counted as not flagged above and named here so the reader can see it.
        "whole_answer_claims": sum(r.get("proposal", {}).get("whole_answer_claims", 0) for r in rows),
        "no_final_claim": {"reachable": sum(1 for r in reach if r["score"].get("final_claims") == 0),
                           "beyond_tool_boundary": sum(1 for r in beyond if r["score"].get("final_claims") == 0),
                           "clean": sum(1 for r in clean if r["score"].get("final_claims") == 0)},
        "failures": sum(1 for r in rows if r.get("error")),
    }


def render(s: Dict[str, Any], model: str, which: str) -> str:
    def pct(a: int, b: int) -> str:
        return f"{a}/{b} ({a / b:.0%})" if b else "0/0"
    h, r, b, c = s["hallucinated"], s["reachable"], s["beyond_tool_boundary"], s["clean"]
    lines = [
        "# tallystick on AgentHallu (real agent trajectories, human step labels)",
        f"Selection `{which}`: {h['n']} hallucinated trajectories, {c['n']} clean from the same "
        f"frameworks. Proposer `{model}`, one run, credits on demand from the final answer. "
        f"tallystick {s['tallystick_version']}.",
        "",
        "| set | n | any final claim flagged | earliest break = labelled step | "
        "labelled step among the breaks | within one step |",
        "|---|---|---|---|---|---|",
        f"| hallucinated, label in model text (reachable) | {r['n']} | {pct(r['flagged'], r['n'])} | "
        f"{pct(r['exact'], r['n'])} | {pct(r['label_hit'], r['n'])} | {pct(r['within_one'], r['n'])} |",
        f"| hallucinated, label at a tool-only step (beyond the audit's boundary) | {b['n']} | "
        f"{pct(b['flagged'], b['n'])} | — | — | — |",
        f"| clean | {c['n']} | {pct(c['flagged'], c['n'])} (false alarms) | — | — | — |",
        "",
        "Reachable rows by AgentHallu sub-category:",
        "",
        "| category / sub-category | n | flagged | exact step | label among breaks | within one |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in s["by_subcategory_reachable"].items():
        lines.append(f"| {k} | {v['n']} | {v['flagged']} | {v['exact']} | {v['label_hit']} | {v['within_one']} |")
    lines += [
        "",
        "The label names the step that made the answer wrong; the audit names the step where "
        "the chain from the answer to a root breaks. Where the labelled step is a tool call and "
        "its result only, the hallucination is inside a tool result - a root for this audit, with "
        "no page in the file to check it against - and those rows are reported apart, not as misses; "
        "a flag on them is a flag on something else in the run. "
        "AgentHallu's own best model judge localises the step in 41.1% of cases over all "
        "categories (Liu et al., 2026); that figure is over a different set and is not "
        "comparable to any cell above without that caveat.",
    ]
    if s.get("whole_answer_claims"):
        lines.append(f"\n{s['whole_answer_claims']} short final answer(s) the segmenter returned no "
                     f"locatable claim for were posted whole as one claim, so that an answer of a bare "
                     f"number is audited rather than passed for having nothing to audit.")
    nfc = s.get("no_final_claim") or {}
    if any(nfc.values()):
        lines.append(f"\n{sum(nfc.values())} trajectory(ies) had no final-answer claim posted (the "
                     f"segmenter returned none that could be located, and the answer was not a bare "
                     f"value) and count as not flagged: {nfc['reachable']} reachable, "
                     f"{nfc['beyond_tool_boundary']} beyond the boundary, {nfc['clean']} clean.")
    if s["failures"]:
        lines.append(f"\n{s['failures']} trajectory(ies) failed in the proposer and are excluded.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="bench/work-agenthallu/AgentHallu/AgentHallu",
                    help="the AgentHallu/ data directory inside the cloned repo")
    ap.add_argument("--work", default="bench/work-agenthallu")
    ap.add_argument("--select", choices=["factual", "retrieval", "all"], default="factual")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-tool-chars", type=int, default=DEFAULT_MAX_TOOL_CHARS)
    ap.add_argument("--reuse", action="append", default=[], metavar="POSTED_DIR",
                    help="answer from a previous run's posted files where the same "
                         "question was asked; only new questions go to the model. "
                         "May be given more than once; the first directory that "
                         "can answer does")
    ap.add_argument("--include-codeact", action="store_true",
                    help="keep CodeAct trajectories (tool boundary inside the execution log)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    data = Path(args.data)
    if not data.is_dir():
        print(f"not a directory: {data} (clone https://github.com/liuxuannan/AgentHallu "
              f"under {args.work}/)", file=sys.stderr)
        return 2
    files = select(data, args.select, args.seed, args.limit, args.include_codeact)
    if not files:
        print("nothing selected", file=sys.stderr)
        return 2

    traces = []
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        name = f"{f.parent.name}/{f.name}"
        traces.append((name, to_trace(obj, name=name, max_tool_chars=args.max_tool_chars)))

    n_codeact_all = sum(1 for f in data.glob("*/*.json")
                        if to_trace(json.loads(f.read_text(encoding="utf-8")))["_meta"]["codeact"])
    n_bad = sum(t["_meta"]["is_hallucination"] for _, t in traces)
    n_beyond = sum(t["_meta"]["label_at_tool_boundary"] for _, t in traces)
    n_derived = sum(1 for _, t in traces for a in t["artifacts"]
                    if a["kind"] in ("intermediate", "final_answer"))
    # A credit call carries every artifact the step could see - on an agent
    # trace, the whole history. Assume ~6 credit calls per trajectory (the
    # answer's claims plus the claims they reach) at ~4 characters per token.
    chars = sum(len(a["content"]) for _, t in traces for a in t["artifacts"])
    in_tokens = (chars * 6 + sum(len(a["content"]) for _, t in traces
                                 for a in t["artifacts"] if a["kind"] != "document") ) / 4
    print(f"{len(traces)} trajectories ({n_bad} hallucinated, {n_beyond} of them labelled at "
          f"a tool-only step; {len(traces) - n_bad} clean; CodeAct trajectories "
          f"{'kept' if args.include_codeact else 'excluded'}: {n_codeact_all} in the dataset); "
          f"{n_derived} model-written artifacts to segment; roughly {in_tokens / 1e6:.1f}M input "
          f"tokens, ${in_tokens / 1e6 * 3:.0f}–${in_tokens / 1e6 * 6:.0f} at Sonnet prices")
    if args.dry_run:
        return 0

    from tallystick.propose import AnthropicProposer, post_run
    proposer = AnthropicProposer(model=args.model)
    reuse: Optional[ReuseProposer] = None
    work = Path(args.work)
    if args.reuse:
        for d in args.reuse:
            if not Path(d).is_dir():
                print(f"not a directory: {d}", file=sys.stderr)
                return 2
            if Path(d).resolve() == (work / "posted").resolve():
                print(f"--reuse {d} is this run's own posted directory; move the earlier "
                      f"run into a subdirectory (rows.jsonl, rows.meta.json, posted/ "
                      f"together) and point --reuse there", file=sys.stderr)
                return 2
            # Whether a run used --reuse is recorded beside its posted files
            # (rows.meta.json) and, from 0.7.2, inside them. An untagged file
            # with neither cannot be told from one whose answer already carries
            # replayed coverage, and the reuse would replay it again, silently.
            untagged = [f for f in Path(d).glob("*.json") if not all(
                "proposed_by" in c for c in json.loads(f.read_text(encoding="utf-8")).get("claims", []))]
            if untagged and not (Path(d).parent / "rows.meta.json").exists():
                print(f"--reuse {d}: {len(untagged)} file(s) written before claims were tagged and no "
                      f"rows.meta.json beside the directory to say whether that run used --reuse; "
                      f"move the run's rows.meta.json next to posted/ and rerun", file=sys.stderr)
                return 2
        reuse = ReuseProposer(proposer, [Path(d) for d in args.reuse])
        proposer = reuse

    (work / "posted").mkdir(parents=True, exist_ok=True)
    params = {"select": args.select, "seed": args.seed, "limit": args.limit,
              "include_codeact": args.include_codeact, "max_tool_chars": args.max_tool_chars,
              "model": args.model, "tallystick_version": TALLYSTICK_VERSION,
              "reuse": [str(Path(d).resolve()) for d in args.reuse] or None}
    meta_path = work / "rows.meta.json"
    if meta_path.exists():
        before = json.loads(meta_path.read_text(encoding="utf-8"))
        if before != params:
            diff = {k: (before.get(k), v) for k, v in params.items() if before.get(k) != v}
            print(f"rows.jsonl was produced with different settings {diff}; move rows.jsonl, "
                  f"rows.meta.json and posted/ aside and rerun", file=sys.stderr)
            return 2
    else:
        meta_path.write_text(json.dumps(params, indent=1), encoding="utf-8")
    rows_path = work / "rows.jsonl"
    rows: List[Dict[str, Any]] = []
    stray = [] if rows_path.exists() else list((work / "posted").glob("*.json"))
    if stray:
        # An earlier run's posted files with no rows.jsonl beside them would be
        # overwritten one by one, and nothing would say so.
        print(f"{work / 'posted'} holds {len(stray)} file(s) from an earlier run but there is no "
              f"rows.jsonl beside them; move that run into a subdirectory (rows.jsonl, "
              f"rows.meta.json, results.json, RESULTS.md and posted/ together) and rerun",
              file=sys.stderr)
        return 2
    if rows_path.exists():
        for line in rows_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        old = [r for r in rows if r.get("tallystick_version") != TALLYSTICK_VERSION
               or r.get("model") != args.model]
        if old:
            print(f"rows.jsonl was produced by another tallystick version or model "
                  f"({len(old)} row(s)); move it aside and rerun", file=sys.stderr)
            return 2
        wanted = {n: trace_sha(t) for n, t in traces}
        rows = [r for r in rows if r["file"] in wanted and r.get("trace_sha") == wanted[r["file"]]]
        # A row that failed (a proposer error, an exhausted API balance) is
        # not done: it is dropped here and redone, and the rows file is
        # rewritten without it so the failure is not kept beside the result.
        failed = [r for r in rows if r.get("error")]
        rows = [r for r in rows if not r.get("error")]
        if failed:
            rows_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                 encoding="utf-8")
        done = {r["file"] for r in rows}
        todo = [(n, t) for n, t in traces if n not in done]
        if done or failed:
            print(f"resuming: {len(done)} done, {len(failed)} failed last time and will be "
                  f"redone, {len(todo)} to go")
    else:
        todo = traces

    with rows_path.open("a", encoding="utf-8") as fh:
        for i, (name, trace) in enumerate(todo, 1):
            print(f"[{len(rows) + 1}/{len(traces)}] {name}")
            row: Dict[str, Any] = {"file": name, "meta": trace["_meta"],
                                   "trace_sha": trace_sha(trace),
                                   "tallystick_version": TALLYSTICK_VERSION,
                                   "model": args.model}
            try:
                if reuse is not None:
                    reuse.start(name)
                run = load_run({k: trace[k] for k in ("artifacts", "steps")})
                posted = post_run(run, proposer, on_demand=True)
                posted["_meta"] = trace["_meta"]
                if reuse is not None:
                    posted["_proposal"]["reused_from"] = params["reuse"]
                out = work / "posted" / (name.replace("/", "__"))
                out.write_text(json.dumps(posted, ensure_ascii=False, indent=1), encoding="utf-8")
                row["score"] = score(trace, posted)
                row["proposal"] = {k: posted["_proposal"][k] for k in
                                   ("claims_posted", "credits_posted", "prior_posted",
                                    "unasked_claims", "coverage_claims", "tolerant_locates",
                                    "self_evident_credits", "whole_answer_claims")}
                sc = row["score"]
                print(f"    label={sc['label_step']} break={sc['break_step']} "
                      f"flagged={sc['flagged']} exact={sc['exact']}"
                      + ("  [label at tool boundary]" if trace["_meta"]["label_at_tool_boundary"] else ""))
            except Exception as exc:  # noqa: BLE001 - one bad trajectory must not end the run
                row["error"] = f"{type(exc).__name__}: {exc}"
                print(f"    failed: {row['error'][:160]}")
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

    if reuse is not None:
        print(f"reuse: {dict(reuse.calls)}")
    scored = [r for r in rows if "score" in r]
    s = summarise(scored)
    s["failures"] = sum(1 for r in rows if r.get("error"))
    (work / "results.json").write_text(
        json.dumps({"summary": s, "params": params, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    report = render(s, args.model, args.select)
    (work / "RESULTS.md").write_text(report, encoding="utf-8")
    print()
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
