"""How big a trace gets against the chat log it was read from.

    python bench/trace_growth.py                 # the synthetic chats, no data needed
    python bench/trace_growth.py --corpus DIR    # also AgentHallu's largest ratio

The numbers README.md and docs/auditable-traces.md give for trace growth come
from here. Both sides are measured as `tallystick convert` writes JSON
(`tallystick.convert.json_bytes`), so a log's own formatting cannot move them.

The synthetic chat: a user question, then N turns of one tool call each and the
tool's reply, then a closing assistant message. Two shapes, because the growth
depends on how many artifacts a turn records:

  tool call only   the assistant turn carries the call and no text
  text and call    the assistant turn also says `Checking {i}.`

A turn with text records one more artifact, and every later step lists it.
Tool replies are 40 or 2,000 characters.

For each shape and reply length it prints the first turn count at which the
trace is at least TRACE_SIZE_NOTE_RATIO times the log (where `convert` and
`check-trace` start saying so), and the log and trace sizes at 2,000 turns.

--corpus: the largest trace-to-log ratio over AgentHallu, read by the OpenAI
reader as bench/openai_roundtrip.py renders it. The corpus is not in this
repository: git clone https://github.com/liuxuannan/AgentHallu and pass the
directory holding its trajectories.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from tallystick.adapters import agenthallu, openai_chat  # noqa: E402
from tallystick.convert import TRACE_SIZE_NOTE_RATIO, json_bytes  # noqa: E402

MB = 1024 * 1024


def chat(turns: int, reply_chars: int, with_text: bool) -> dict:
    m = [{"role": "user", "content": "Look these up one by one."}]
    for i in range(turns):
        m.append({"role": "assistant", "content": f"Checking {i}." if with_text else None,
                  "tool_calls": [{"id": f"call_{i}", "type": "function",
                                  "function": {"name": "lookup",
                                               "arguments": json.dumps({"q": f"item {i}"})}}]})
        m.append({"role": "tool", "tool_call_id": f"call_{i}", "name": "lookup",
                  "content": (f"result {i} " + "x" * reply_chars)[:reply_chars]})
    m.append({"role": "assistant", "content": "Done."})
    return {"messages": m}


def sizes(turns: int, reply_chars: int, with_text: bool):
    log = chat(turns, reply_chars, with_text)
    return json_bytes(log), json_bytes(openai_chat.to_trace(log))


def crossing(reply_chars: int, with_text: bool, high: int = 2000):
    """The first turn count whose trace is TRACE_SIZE_NOTE_RATIO times the log.
    The ratio only grows with the turns, so a bisection finds it."""
    def over(n):
        log, trace = sizes(n, reply_chars, with_text)
        return trace >= TRACE_SIZE_NOTE_RATIO * log
    if not over(high):
        return None
    lo, hi = 1, high
    while lo < hi:
        mid = (lo + hi) // 2
        if over(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def corpus_ratio(root: Path):
    sys.path.insert(0, str(ROOT / "bench"))
    from openai_roundtrip import render
    best, where, n = 0.0, None, 0
    for p in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not (isinstance(obj, dict) and "history" in obj):
            continue
        n += 1
        log = render(obj)
        for flags in ({}, {"model_text_tools": agenthallu.ECHO_TOOLS}):
            r = json_bytes(openai_chat.to_trace(log, name=p.name, **flags)) / json_bytes(log)
            if r > best:
                best, where = r, str(p.relative_to(root))
    return n, best, where


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path)
    args = ap.parse_args(argv)
    print(f"trace against log, both as `tallystick convert` writes JSON; "
          f"the note starts at {TRACE_SIZE_NOTE_RATIO}x")
    for with_text in (False, True):
        shape = "text and call" if with_text else "tool call only"
        for reply in (40, 2000):
            first = crossing(reply, with_text)
            log, trace = sizes(2000, reply, with_text)
            print(f"  {shape:14s} replies of {reply:5,d} chars: "
                  f"{TRACE_SIZE_NOTE_RATIO}x at {first if first else 'more than 2,000'} turns; "
                  f"2,000 turns: log {log / MB:.1f} MB, trace {trace / MB:.1f} MB "
                  f"({trace / log:.0f}x)")
    if args.corpus:
        n, best, where = corpus_ratio(args.corpus)
        print(f"AgentHallu, {n} trajectories: largest trace is {best:.1f}x its log ({where})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
