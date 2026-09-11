"""Read an AgentHallu trajectory as a raw tallystick trace.

AgentHallu (Liu et al., 2026, arXiv 2601.06818; CC BY 4.0) is 693 real runs of
seven agent frameworks with, for each hallucinated run, the step that
introduced the hallucination labelled by hand. A trajectory file is one JSON
object: the question, a `history` of numbered steps - each with the model's
text (`content`), the tools it called and what they returned - the final
`agent_answer`, and the labels.

How it becomes a ledger
-----------------------
  question           -> root artifact  (document)          `question`
  history[k].content -> derived artifact (intermediate)    `s{k}`
  tool_responses     -> root artifacts (tool_result)       `s{k}.t{j}`
                        (ECHO_TOOLS: intermediate instead, see below)
  agent_answer       -> derived artifact (final_answer)    `answer`

A model step `s{k}` receives everything recorded before it: the whole history
is in the prompt of an agent loop, so every earlier artifact is an input. Its
tool calls are a separate step `s{k}.tools`, with the same inputs plus `s{k}`,
that produces the tool results. The final `answer` step receives everything.

Where the audit stops, by design
--------------------------------
A tool result is a root: whatever it says, the agent did not make it up. In
AgentHallu some tools (OpenDeepSearch's `web_search` above all) return a
model-written digest of pages the trajectory does not contain, and some
labelled hallucinations live inside that digest. A provenance audit of the
file cannot see past that boundary - there is no page in the file to check
the digest against - so `_meta.label_at_tool_boundary` marks a trajectory
whose labelled step carries no model prose at all, only a tool call and its
result (the call's arguments are the model's, but they are a query or code,
not a claim). Those are counted apart from hits and misses. This is a limit
of the data, stated, not of the labels.

CodeAct agents (agent_type "…-CodeAct", tool "Code Action Space") call their
tools from inside model-written code, so a web result and a model-computed
string arrive in the same execution log: the boundary between the world's text
and the model's runs through the middle of one artifact, and the file does not
mark it. Such trajectories are flagged `_meta.codeact`; no reading of the log
as either root or model text audits them honestly, and the harness excludes
them by default and says so.

Tool results longer than `max_tool_chars` are cut at that length (the
proposer's prompt would not hold a 700 KB page); the cut is recorded in
`_meta.truncated`. Nothing else is altered.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

DEFAULT_MAX_TOOL_CHARS = 20_000

# Tools whose "result" is the model's own text handed back: SmolAgents' and
# OpenDeepSearch's `final_answer` echo the answer, Camel's notes store and
# return what the agent wrote, OpenManus' `terminate` returns its message. A
# root is text that entered the run from outside; these did not, so their
# results are posted as intermediate (model-written) artifacts and must be
# funded like any other model text; so is an interpreter output that prints
# back the answer literal from the model's own code (`_echoes_answer`). A
# computed interpreter output, by contrast, is a root: the code was the
# model's, but the number came from running it - the same boundary as a web
# search that returns a digest.
ECHO_TOOLS = frozenset({"final_answer", "append_note", "read_note", "terminate"})


def _echoes_answer(result: str, args: str, answer: str) -> bool:
    """A CodeAct interpreter prints `final_answer("...")` back as its last
    output line: the text is a literal in the model's own code, not something
    the run produced. If the result's last line is the eventual answer and the
    answer also sits in the call's arguments, the result is the model's text
    handed back. A search result that merely contains the answer somewhere
    (the query mentioned it) is not an echo and stays a root."""
    a = answer.strip()
    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    return len(a) >= 8 and a in args and bool(lines) and lines[-1] == a


def _text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    return json.dumps(x, ensure_ascii=False)


def _label_step(obj: Dict[str, Any]) -> Optional[int]:
    raw = obj.get("hallucination_step")
    if raw in (None, "", "null"):
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def to_trace(obj: Dict[str, Any], *, name: str = "",
             max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS) -> Dict[str, Any]:
    """Return a raw trace dict (`artifacts`, `steps`, `_meta`) for `obj`."""
    artifacts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    truncated: List[str] = []
    history_step_of: Dict[str, int] = {}  # our step_id -> history step number
    art_step: Dict[str, int] = {}         # artifact_id -> history step number

    answer = _text(obj.get("agent_answer"))
    question = _text(obj.get("question")).strip()
    if question:
        artifacts.append({"artifact_id": "question", "kind": "document",
                          "title": "question", "content": question})
    seen: List[str] = [a["artifact_id"] for a in artifacts]

    used: Dict[str, int] = {}
    for h in obj.get("history") or []:
        k = int(h.get("step"))
        content = _text(h.get("content"))
        sid = f"s{k}"
        # A few trajectories number two consecutive entries the same; the
        # second becomes s{k}_2 and still maps to history step k.
        used[sid] = used.get(sid, 0) + 1
        if used[sid] > 1:
            sid = f"{sid}_{used[sid]}"
        if content.strip():
            artifacts.append({"artifact_id": sid, "kind": "intermediate",
                              "title": _text(h.get("role")) or f"step {k}",
                              "content": content})
            steps.append({"step_id": sid, "kind": "generate",
                          "inputs": list(seen), "outputs": [sid]})
            history_step_of[sid] = k
            art_step[sid] = k
            seen.append(sid)
        calls = h.get("tool_calls") or []
        outs: List[str] = []
        for j, resp in enumerate(h.get("tool_responses") or []):
            text = _text(resp)
            if not text.strip():
                continue
            tid = f"{sid}.t{j}"
            if len(text) > max_tool_chars:
                text = text[:max_tool_chars]
                truncated.append(tid)
            call = calls[j] if j < len(calls) and isinstance(calls[j], dict) else {}
            tool = _text(call.get("name")) or "tool"
            title = tool
            args = call.get("arguments")
            if args:
                title += " " + _text(args)[:120]
            echo = tool in ECHO_TOOLS or _echoes_answer(text, _text(args), answer)
            kind = "intermediate" if echo else "tool_result"
            artifacts.append({"artifact_id": tid, "kind": kind,
                              "title": title, "content": text})
            outs.append(tid)
            art_step[tid] = k
        if outs:
            tsid = f"{sid}.tools"
            # The call was written by the model with the whole history in
            # view, so an echoed result (ECHO_TOOLS) may be funded by anything
            # before it; for a real tool result the inputs are moot (roots).
            steps.append({"step_id": tsid, "kind": "tool",
                          "inputs": list(seen), "outputs": outs})
            history_step_of[tsid] = k
            seen.extend(outs)

    if answer.strip():
        artifacts.append({"artifact_id": "answer", "kind": "final_answer",
                          "title": "agent_answer", "content": answer})
        steps.append({"step_id": "answer", "kind": "answer",
                      "inputs": list(seen), "outputs": ["answer"]})

    tools_used = {_text(c.get("name")) for h in obj.get("history") or []
                  for c in (h.get("tool_calls") or []) if isinstance(c, dict)}
    codeact = "Code Action Space" in tools_used or _text(obj.get("agent_type")).endswith("CodeAct")

    label = _label_step(obj)
    # Judged on what was posted, not on the raw history: a step whose only
    # artifact is an echoed note (ECHO_TOOLS) is model text and reachable.
    kinds_at_label = [a["kind"] for a in artifacts if art_step.get(a["artifact_id"]) == label]
    at_boundary = bool(kinds_at_label) and all(k == "tool_result" for k in kinds_at_label)

    meta = {
        "source": "AgentHallu",
        "file": name,
        "framework": _text(obj.get("agent_type")),
        "model_id": _text(obj.get("model_id")),
        "question_source": _text(obj.get("question_source")),
        "question_domain": _text(obj.get("question_domain")),
        "is_hallucination": str(obj.get("is_hallucination", "")).lower() == "true",
        "hallucination_step": label,
        "hallucination_category": obj.get("hallucination_category"),
        "hallucination_subcategory": obj.get("hallucination_subcategory"),
        "hallucination_reason": obj.get("hallucination_reason"),
        "label_at_tool_boundary": at_boundary,
        "codeact": codeact,
        "history_step_of": history_step_of,
        "history_steps": len(obj.get("history") or []),
        "truncated": truncated,
        "max_tool_chars": max_tool_chars,
    }
    return {"artifacts": artifacts, "steps": steps, "_meta": meta}
