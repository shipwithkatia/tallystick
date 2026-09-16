"""proverka13, section 5: numbers in README and docs, recounted.

Both tests fail on f731bdb.
"""

from __future__ import annotations

import json
from pathlib import Path

from tallystick.adapters.openai_chat import to_trace
from tallystick.convert import json_bytes

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_has_one_in_short_paragraph():
    """README.md lines 5 and 6 both open with **In short.**; line 6 repeats
    the first three sentences of line 5."""
    n = README.count("**In short.**")
    assert n == 1, f"README opens with {n} 'In short' paragraphs"


def test_docs_trace_growth_holds_for_the_chat_it_describes():
    """docs/auditable-traces.md:75: "Measured with the OpenAI reader on a chat
    of one tool call per turn: with tool replies of 40 characters the trace
    reaches ten times the size of the log at 116 turns". No script in the repo
    produces that number. A chat of exactly that description - one call per
    turn, 40-character replies, nothing else - reaches 2.9x at 116 turns
    (and 0.70 MB -> 33.7 MB at 2,000 turns, not 127 MB). The published figures
    appear only when every assistant turn also carries text."""
    m = [{"role": "user", "content": "q"}]
    for i in range(116):
        m.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "lookup", "arguments": json.dumps({"k": i})}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "name": "lookup",
                  "content": "x" * 30 + f"{i:010d}"})
    m.append({"role": "assistant", "content": "done"})
    log = {"messages": m}
    ratio = json_bytes(to_trace(log)) / json_bytes(log)
    assert ratio >= 10, f"116 turns of one tool call each: {ratio:.1f}x, docs say 10x"
