"""Does the OpenAI reader see the same run the native reader sees?

    python bench/openai_roundtrip.py ~/AgentHallu

The OpenAI reader (`tallystick/adapters/openai_chat.py`) is the one a stranger
will actually use, and until now nothing said whether it reads a real agent run
correctly. This harness answers that with the only material available: the 693
AgentHallu trajectories, which already have a reader of their own written
against the raw files.

The test
--------
For each trajectory:

1. render it as an OpenAI chat log - the shape a person has on disk: a `user`
   message for the question, one `assistant` message per history step carrying
   its text and its tool calls, one `tool` message per tool response, and a
   final `assistant` message for the answer;
2. read that log with the OpenAI reader;
3. compare the trace against what `adapters/agenthallu.py` builds from the raw
   file - the same corpus, read two ways.

The headline comparison passes this corpus's four echo tools as
`--tool-returns-model-text`, because that is what the operator of these agents
knows and the native reader has hard-coded. The same run with no flags is
reported too: the gap between the two is the size of what a message list alone
cannot tell you.

What counts as agreement is the audit's own question: the *sequence of
artifact contents*, and for each one whether the audit may stop there (a root:
`document` / `tool_result`) or must fund it (`intermediate` /
`final_answer`). Titles, ids and step ids are free to differ - they are
bookkeeping. A disagreement about root-ness is not: it is the difference
between evidence and laundering.

What this cannot prove
----------------------
The rendering is written here, so a bug shared by both readers would pass. It
tests that the general reader recovers the same run from the shape a person
has, not that either reader is right about the run. The known and expected
disagreement is content-based echo detection: the native reader marks an
interpreter output that prints the answer literal from the model's own code as
model text (`_echoes_answer`), which nothing in a message list makes visible.

It also cannot see a bug both readers share - though this one it now half
sees. `_echoes_answer` only fires on an answer of eight characters or more, and
the OpenAI reader has no such floor for a value the answering call carried, so
those short echoes are read as model text by the general reader and as roots by
the native one. They appear above as *disagreements*, which is the wrong word
for them: the general reader is the stricter one there.

Every disagreement this harness has ever found has gone that way - the general
reader stricter, never laxer - and the direction is worth stating carefully.
The report below counts how many artifacts differ; it does not count which way,
so "laxer on none" is a claim about these 693 trajectories that has to be
measured separately, and it is a measurement rather than a property of the
code. The sixth review's three-character bypass was a laxer case, and this
corpus does not contain one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tallystick.adapters import agenthallu, openai_chat  # noqa: E402

ROOTS = {"document", "tool_result"}


def render(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """An AgentHallu trajectory as the chat log a person would have on disk."""
    messages: List[Dict[str, Any]] = []
    question = agenthallu._text(obj.get("question")).strip()
    if question:
        messages.append({"role": "user", "content": question})

    counter = 0
    for h in obj.get("history") or []:
        calls = h.get("tool_calls") or []
        responses = h.get("tool_responses") or []
        tool_calls = []
        ids = []
        for j, _resp in enumerate(responses):
            call = calls[j] if j < len(calls) and isinstance(calls[j], dict) else {}
            counter += 1
            cid = f"call_{counter}"
            ids.append((cid, agenthallu._text(call.get("name")) or "tool"))
            tool_calls.append({
                "id": cid, "type": "function",
                "function": {"name": ids[-1][1],
                             "arguments": agenthallu._text(call.get("arguments"))},
            })
        message: Dict[str, Any] = {
            "role": "assistant", "content": agenthallu._text(h.get("content"))}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
        for j, resp in enumerate(responses):
            cid, tool = ids[j]
            messages.append({"role": "tool", "tool_call_id": cid, "name": tool,
                             "content": agenthallu._text(resp)})

    answer = agenthallu._text(obj.get("agent_answer"))
    if answer.strip():
        messages.append({"role": "assistant", "content": answer})
    return messages


def shape(trace: Dict[str, Any]) -> List[Tuple[str, str]]:
    """What the audit actually consumes: each artifact's text, and whether a
    chain may stop on it."""
    return [("root" if a["kind"] in ROOTS else "derived", a["content"])
            for a in trace["artifacts"]]


def _classify(title: str, content: str) -> str:
    """What an interpreter output that the flag would demote actually is. The
    honest label matters: "genuinely computed" was the first word for this and
    it is true of under a quarter of them."""
    if "final_answer" in title:
        return "code calling final_answer - an echo the native reader missed"
    if re.search(r"web_search|visit_webpage|search\(", title):
        return "code that called a web tool - fetched text, not computed"
    if "Error" in content[:200]:
        return "an interpreter error message"
    return "no search and no final_answer - plausibly computed"


def _shared_blind_spot(files: List[Path]) -> int:
    """Echoes that only the native reader's 8-character guard lets through.
    Both readers call them roots, so the comparison counts them as agreement."""
    n = 0
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        answer = agenthallu._text(obj.get("agent_answer")).strip()
        if not answer or len(answer) >= 8:
            continue
        for step in obj.get("history") or []:
            calls = step.get("tool_calls") or []
            for j, resp in enumerate(step.get("tool_responses") or []):
                text = agenthallu._text(resp)
                call = calls[j] if j < len(calls) and isinstance(calls[j], dict) else {}
                args = agenthallu._text(call.get("arguments"))
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if answer in args and lines and lines[-1] == answer:
                    n += 1
    return n


def _without_ids(files: List[Path]) -> Tuple[int, int]:
    """The same comparison with every `tool_call_id` and `name` stripped off
    the tool messages - the shape a gateway that does not echo call ids
    writes, where the reader has nothing but the order of the calls to go on.

    Read together with `_mixed_batches` below, which says how much this corpus
    can decide about that path: almost nothing. It is reported anyway, because
    a reader who is told "97% on 693 real runs" will assume the reading was
    exercised, and this says which part of it was not."""
    same = 0
    flips = 0
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        messages = []
        for m in render(obj):
            if m.get("role") == "tool":
                # Both the id and the name go. Keeping the name routes every
                # result through the name tier and never reaches the positional
                # one, which is the code five rounds of review kept finding
                # defects in - measured on this corpus: with names kept, 3538
                # of 3538 results match by name and none by position. A pass
                # that cannot reach the code it was written for measures
                # nothing, and this one said so on its label for two commits.
                m = {"role": "tool", "content": m["content"]}
            messages.append(m)
        try:
            got = openai_chat.to_trace(messages, name=path.name,
                                       model_text_tools=agenthallu.ECHO_TOOLS)
        except ValueError:
            continue
        want, have = shape(agenthallu.to_trace(obj, name=path.name)), shape(got)
        if want == have:
            same += 1
            continue
        for (w, _), (h, _) in zip(want, have):
            if w != h:
                flips += 1
    return same, flips


def _mixed_batches(files: List[Path]) -> Tuple[int, int, int]:
    """How many parallel tool batches this corpus contains, how many call more
    than one distinct tool, and how many of those mix a tool whose result is the
    model's own text with one whose result is not.

    Only the last number can decide anything about matching a result to its
    call: where every tool in a batch is on the same side of the root line, a
    mis-assignment swaps two titles and changes no verdict. It is the honest
    ceiling on what the three passes above can prove about that code."""
    parallel = multi = mixed = 0
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        for step in obj.get("history") or []:
            calls = [c for c in (step.get("tool_calls") or []) if isinstance(c, dict)]
            names = [agenthallu._text(c.get("name")) or "tool" for c in calls]
            if len(names) < 2:
                continue
            parallel += 1
            if len(set(names)) < 2:
                continue
            multi += 1
            echo = [n in agenthallu.ECHO_TOOLS for n in names]
            if any(echo) and not all(echo):
                mixed += 1
    return parallel, multi, mixed


def _bare(files: List[Path]) -> int:
    """The same comparison with no flags - what a first-time user gets before
    they have told the reader which tools hand the model's words back."""
    same = 0
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        try:
            got = openai_chat.to_trace(render(obj), name=path.name)
        except ValueError:
            continue
        if shape(agenthallu.to_trace(obj, name=path.name)) == shape(got):
            same += 1
    return same


def _with_interpreter(files: List[Path]) -> Tuple[int, int, Counter]:
    """The same comparison, with the CodeAct interpreter declared as a tool that
    hands the model's own text back. Returns (identical, artifacts turned from
    root into model text, and what those artifacts actually are)."""
    same = 0
    flips = 0
    kinds: Counter = Counter()
    tools = set(agenthallu.ECHO_TOOLS) | {"Code Action Space"}
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            continue
        native = agenthallu.to_trace(obj, name=path.name)
        try:
            got = openai_chat.to_trace(render(obj), name=path.name,
                                       model_text_tools=tools)
        except ValueError:
            continue
        want, have = shape(native), shape(got)
        if want == have:
            same += 1
            continue
        for i, ((w, text), (h, _)) in enumerate(zip(want, have)):
            if w == "root" and h == "derived":
                flips += 1
                kinds[_classify(native["artifacts"][i].get("title", ""), text)] += 1
    return same, flips, kinds


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="the AgentHallu directory")
    ap.add_argument("--out", metavar="PATH", help="write the report here too")
    args = ap.parse_args(argv)

    files = sorted(Path(args.root).rglob("*.json"))
    if not files:
        print(f"no trajectories under {args.root}", file=sys.stderr)
        return 2

    same = 0
    differ: List[Tuple[str, str]] = []
    reasons: Counter = Counter()
    unreadable = 0
    kinds_native: Counter = Counter()
    kinds_openai: Counter = Counter()

    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            unreadable += 1
            continue
        if not isinstance(obj, dict) or "history" not in obj:
            unreadable += 1
            continue

        native = agenthallu.to_trace(obj, name=path.name)
        messages = render(obj)
        try:
            got = openai_chat.to_trace(
                messages, name=path.name,
                model_text_tools=agenthallu.ECHO_TOOLS)
        except ValueError as exc:
            differ.append((str(path), f"reader refused: {exc}"))
            reasons["reader refused the rendered log"] += 1
            continue

        for a in native["artifacts"]:
            kinds_native[a["kind"]] += 1
        for a in got["artifacts"]:
            kinds_openai[a["kind"]] += 1

        want, have = shape(native), shape(got)
        if want == have:
            same += 1
            continue

        if [t for t, _ in want] != [t for t, _ in have]:
            if len(want) == len(have):
                flips = [(w, h) for (w, _), (h, _) in zip(want, have) if w != h]
                reasons[f"root/derived differs on {len(flips)} artifact(s)"] += 1
            else:
                reasons["a different number of artifacts"] += 1
        else:
            reasons["same kinds, different text"] += 1
        differ.append((str(path), f"{len(want)} vs {len(have)} artifacts"))

    total = same + len(differ)
    lines = [
        "OpenAI reader against the native AgentHallu reader",
        "=" * 64,
        f"trajectories read      {total}"
        + (f"  ({unreadable} file(s) skipped: not a trajectory)" if unreadable else ""),
        f"identical              {same}  ({same / total:.0%})"
        if total else "identical              0",
        f"different              {len(differ)}",
        "",
    ]
    if reasons:
        lines.append("where they differ:")
        for reason, n in reasons.most_common():
            lines.append(f"  {n:4d}  {reason}")
        lines.append("")
    lines.append("artifact kinds, native reader vs OpenAI reader:")
    for kind in sorted(set(kinds_native) | set(kinds_openai)):
        lines.append(f"  {kind:<14} {kinds_native[kind]:6d}  {kinds_openai[kind]:6d}")
    if differ:
        lines += ["", "first disagreements:"]
        for path, why in differ[:10]:
            lines.append(f"  {Path(path).parent.name}/{Path(path).name}: {why}")

    # The obvious fix for the residual disagreement is to name the interpreter
    # with --tool-returns-model-text. Measured, it is worse, and by an order of
    # magnitude: a code interpreter returns the model's own literal sometimes
    # and a computed value the rest of the time, and nothing in a message list
    # separates the two. Printed because a reader will think of it.
    lines += ["", "with every tool_call_id and tool name stripped:"]
    noid_same, noid_flips = _without_ids(files)
    lines.append(f"  identical              {noid_same}  ({noid_same / total:.0%})"
                 if total else "  identical 0")
    lines.append(f"  artifacts read as the wrong kind  {noid_flips}")
    parallel, multi, mixed = _mixed_batches(files)
    lines.append("  With neither, a result can only be matched to its call by the")
    lines.append("  order the calls were declared in. Three of the laundering")
    lines.append("  defects found in review of this release lived on that matching")
    lines.append("  code - and this corpus can decide almost nothing about it:")
    lines.append(f"      {parallel:4d}  parallel tool batches")
    lines.append(f"      {multi:4d}  of them calling more than one distinct tool")
    lines.append(f"      {mixed:4d}  of those mixing a tool that hands back the model's")
    lines.append("            own text with one that does not - the only shape where")
    lines.append("            a mis-assignment can move an artifact across the root")
    lines.append("            line and change a verdict")
    lines.append("  So this pass is a regression guard, not evidence: the coverage for")
    lines.append("  that code is in tests/test_convert.py, written from the failing")
    lines.append("  inputs review found, because the corpus does not contain them.")

    lines += ["", "with no --tool-returns-model-text flags at all:"]
    bare_same = _bare(files)
    lines.append(f"  identical              {bare_same}  ({bare_same / total:.0%})"
                 if total else "  identical 0")
    lines.append("  The headline above names this corpus's four echo tools. Without")
    lines.append("  them a final_answer tool's output is a root, and the two readers")
    lines.append("  part on a third of the corpus. That is not a defect in either:")
    lines.append("  it is the size of what only the operator knows, and the reason")
    lines.append("  the flag exists.")

    lines += ["", "if the interpreter were named --tool-returns-model-text:"]
    over_same, over_flips, over_kinds = _with_interpreter(files)
    lines.append(f"  identical              {over_same}  ({over_same / total:.0%})"
                 if total else "  identical 0")
    lines.append(f"  roots turned into model text  {over_flips}, of which:")
    for kind, n in over_kinds.most_common():
        lines.append(f"      {n:4d}  {kind}")
    lines.append("  -> worse, and not for the obvious reason: most of what the flag")
    lines.append("     would demote is web text surfaced through the interpreter, not")
    lines.append("     a computed value - and a few are echoes the native reader's own")
    lines.append("     8-character guard let through. The flag is for a tool that")
    lines.append("     ALWAYS hands back the model's words. An interpreter does not.")

    lines += ["", "what this comparison cannot see:"]
    lines.append(f"  {_shared_blind_spot(files)} interpreter output(s) in this corpus "
                 "meet every condition of")
    lines.append("  the native reader's echo test except its 8-character minimum. Both")
    lines.append("  readers call them roots, so they agree, and they are counted above")
    lines.append("  as agreement. Agreement is not correctness.")

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
