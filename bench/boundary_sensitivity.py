"""How much does the 53% move if you disagree with how it was counted?

    python bench/boundary_sensitivity.py --data <AgentHallu> [--rows <rows.jsonl>]

The headline finding of this project is a single number: the share of
human-labelled hallucinations that sit at a step where the trace recorded only
a tool's reply, and so cannot be reached by any audit of the file afterwards.
A single number invites a single objection, and the number rests on one
definition, so this script states the objections and answers each with a
figure rather than an argument.

The definition under test
-------------------------
A labelled step is *beyond the boundary* when every artifact recorded at it is
a tool result - when the model wrote no prose there. The audit can ask the
model what its own words rest on; it cannot ask a tool, and the page the tool
digested is not in the file.

The three ways to disagree
--------------------------
1. **"The model wrote the tool call, so there IS model text at that step."**
   True, and it is measured here. But a tool call is a query or a piece of
   code, not an assertion about the world: `{"query": "which two ASEAN
   capitals are furthest apart"}` states nothing that could be checked against
   a source. The script counts how many labelled steps carry a call at all,
   and how many carry one long enough to be prose rather than a search key, and
   prints the share that survives if every one of those is counted as
   reachable. That is the low end of the range.

2. **"You decided some tools hand back the model's own words."**
   `ECHO_TOOLS` in the adapter. Every one of those decisions moves a trace OUT
   of the boundary set, so they make the headline smaller, not larger. The
   count is printed.

3. **"You excluded the CodeAct runs."**
   Also printed, both ways. Including them gives the lower figure, which is the
   one the README quotes.

Which population, and why it matters
------------------------------------
The figures are printed twice: over all 443 labelled trajectories in the
corpus, and over the 115 the paid run scored. They do not move together. On
the scored subset, objection 1 takes the share from 53% to 43%; on the whole
corpus it takes it from 53% to 32%, because a larger share of those labelled
steps carry a long tool call. Quote the wider range unless you are speaking
about the scored subset specifically - the wider one is the honest one for a
claim about the corpus.

What this cannot answer
-----------------------
Whether AgentHallu's human labels point at the right step. Every figure here
inherits those labels, and nothing in this repository can check them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tallystick.adapters import agenthallu as ah   # noqa: E402

#: Longer than this, a tool call's arguments might be prose rather than a
#: search key. Read off nothing - it is a round number, and the point of the
#: script is that the answer is reported at both ends of the choice.
PROSE_CHARS = 200


def labelled(root: Path):
    for path in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        if str(obj.get("is_hallucination", "")).lower() != "true":
            continue
        yield path, obj


def call_text(obj: dict, step) -> str:
    for entry in obj.get("history") or []:
        if int(entry.get("step", -1)) == step:
            return " ".join(ah._text(c.get("arguments"))
                            for c in (entry.get("tool_calls") or [])
                            if isinstance(c, dict))
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="the AgentHallu directory")
    ap.add_argument("--rows", help="a run's rows .jsonl, to score its subset too")
    ap.add_argument("--out", metavar="PATH", help="write the report here too")
    args = ap.parse_args(argv)

    root = Path(args.data)
    subset = None
    if args.rows:
        subset = {r["file"] for r in
                  (json.loads(line) for line in
                   Path(args.rows).read_text(encoding="utf-8").splitlines() if line.strip())
                  if (r.get("meta") or {}).get("is_hallucination")}

    def tally(only: set | None):
        n = beyond = with_call = with_prose = echo_at_label = 0
        no_codeact_n = no_codeact_beyond = 0
        for path, obj in labelled(root):
            key = f"{path.parent.name}/{path.name}"
            if only is not None and key not in only:
                continue
            trace = ah.to_trace(obj, name=path.name)
            meta = trace["_meta"]
            n += 1
            at_boundary = meta["label_at_tool_boundary"]
            beyond += at_boundary
            if not meta["codeact"]:
                no_codeact_n += 1
                no_codeact_beyond += at_boundary
            step = meta["hallucination_step"]
            names = {ah._text(c.get("name"))
                     for e in (obj.get("history") or [])
                     if int(e.get("step", -1)) == step
                     for c in (e.get("tool_calls") or []) if isinstance(c, dict)}
            if names & set(ah.ECHO_TOOLS):
                echo_at_label += 1
            if at_boundary:
                text = call_text(obj, step)
                with_call += bool(text.strip())
                with_prose += len(text) > PROSE_CHARS
        return dict(n=n, beyond=beyond, with_call=with_call, with_prose=with_prose,
                    echo_at_label=echo_at_label, nc_n=no_codeact_n,
                    nc_beyond=no_codeact_beyond)

    lines = ["Beyond the boundary: how far the number moves if you disagree",
             "=" * 68]
    for title, only in (("all labelled trajectories", None),
                        ("the scored subset", subset)):
        if only is None or only:
            t = tally(only)
            if not t["n"]:
                continue
            pct = t["beyond"] / t["n"] * 100
            low = (t["beyond"] - t["with_prose"]) / t["n"] * 100
            lines += [
                "",
                f"{title}: {t['n']} labelled",
                f"  beyond the boundary                     "
                f"{t['beyond']:4d}/{t['n']:<4d} {pct:5.1f}%",
                "",
                "  1. if a tool call counted as model text at that step:",
                f"     labelled steps that carry a call at all       "
                f"{t['with_call']:4d} of {t['beyond']}",
                f"     of those, longer than {PROSE_CHARS} characters        "
                f"{t['with_prose']:4d} of {t['beyond']}",
                f"     share if every long one is called reachable   {low:5.1f}%",
                f"     -> the honest range is {low:.0f}% to {pct:.0f}%",
                "",
                "  2. traces whose labelled step called a tool this project reads",
                f"     as handing the model's own words back          "
                f"{t['echo_at_label']:4d}   (each one LOWERS the share)",
                "",
                "  3. CodeAct runs, which the harness excludes by default:",
                f"     with them      {t['beyond']:4d}/{t['n']:<4d} {pct:5.1f}%",
                f"     without them   {t['nc_beyond']:4d}/{t['nc_n']:<4d} "
                f"{t['nc_beyond'] / max(t['nc_n'], 1) * 100:5.1f}%"
                "   (the published figure is the lower of the two)",
            ]

    lines += [
        "",
        "Every figure above inherits AgentHallu's human labels, and nothing in",
        "this repository can check whether they point at the right step.",
    ]
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
