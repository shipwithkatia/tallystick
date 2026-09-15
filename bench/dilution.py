"""The measured price of diluting an echo below half.

    .venv/bin/python bench/dilution.py

The OpenAI reader warns when at least WARN_SHARE of a tool's reply is text the
model itself wrote. That catches an agent quoting itself by accident. It does
not stop someone who knows the rule: text added to a reply lowers the share
without removing a character of the model's words. This script says how much
added text is enough, so the README can state the limit with a number.

Two measurements:

1. For echoes of several lengths, the shortest line of junk put IN FRONT of the
   echo that leaves the reader silent - found by trying every length in turn,
   on the call the reply answers and on a note read back in a later turn. The
   echo is split by a newline so that no whole line is the value and only the
   share can see it.
2. Every tool result of AgentHallu rewritten the same way, junk longer than the
   result, then counted: demotions, warnings, trajectories warned. Needs the
   corpus (not in git): TALLYSTICK_AGENTHALLU, or bench/work-agenthallu/AgentHallu.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from tallystick.adapters import agenthallu, openai_chat as oc  # noqa: E402

PROSE = ("The plant recorded four point six million tonnes of output last year and the "
         "board approved the expansion without further review after the regional "
         "inspectors signed off on the safety audit that the company had commissioned "
         "from an outside firm in the spring, citing the drop in incidents since the "
         "new shift rotation began and the lower cost of insurance cover for the site")

CORPUS = Path(os.environ.get("TALLYSTICK_AGENTHALLU",
                             ROOT / "bench" / "work-agenthallu" / "AgentHallu"))


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def answering(echo, reply):
    """The echo is in the code of the very call the reply answers."""
    return [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "run",
             "tool_calls": [_call("python", {"code": f'print("{echo}")'}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "python", "content": reply},
            {"role": "assistant", "content": "done"}]


def cross_turn(echo, reply):
    """The echo was saved in one turn and is read back in the next."""
    return [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "save",
             "tool_calls": [_call("save_note", {"note": echo}, "c0")]},
            {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": "read",
             "tool_calls": [_call("read_note", {"key": "n"}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "read_note", "content": reply},
            {"role": "assistant", "content": "done"}]


def noticed(messages):
    meta = oc.to_trace(messages)["_meta"]
    return bool(meta["echo_warning_details"] or meta["echoed_back_tool_results"])


def diluted(text, junk):
    half = len(text) // 2
    return "x" * junk + "\n" + text[:half] + "\n" + text[half:]


def main() -> int:
    print(" echo chars   path         shortest junk that silences it   junk / echo")
    for chars in (60, 128, 200, 380):
        echo = PROSE[:chars].rsplit(" ", 1)[0]
        for label, build in (("answering", answering), ("cross-turn", cross_turn)):
            if not noticed(build(echo, diluted(echo, 0))):
                print(f" {len(echo):10d}   {label:11s}  not caught even undiluted")
                continue
            junk = next((n for n in range(4 * len(echo))
                         if not noticed(build(echo, diluted(echo, n)))), None)
            print(f" {len(echo):10d}   {label:11s}  {junk:>10}                       "
                  f"{junk / len(echo):.2f}")

    files = sorted(CORPUS.rglob("*.json")) if CORPUS.is_dir() else []
    if not files:
        print(f"\ncorpus not found at {CORPUS}; skipped")
        return 0
    from openai_roundtrip import render
    print()
    for label, rewrite in (("as recorded", None),
                           ("junk in front, longer than the result", True)):
        demoted = warned = seen = 0
        flagged = set()
        for path in files:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict) or "history" not in obj:
                continue
            seen += 1
            messages = render(obj)
            if rewrite:
                for m in messages:
                    if m.get("role") == "tool" and str(m.get("content") or "").strip():
                        text = str(m["content"])
                        m["content"] = diluted(text, oc._alnum(text) + 8)
            meta = oc.to_trace(messages, name=str(path),
                               model_text_tools=agenthallu.ECHO_TOOLS)["_meta"]
            demoted += len(meta["echoed_back_tool_results"])
            warned += len(meta["echo_warning_details"])
            if meta["echo_warning_details"]:
                flagged.add(str(path))
        print(f"corpus, {label:38s} demotions {demoted:4d}  warnings {warned:4d}  "
              f"trajectories warned {len(flagged)}/{seen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
