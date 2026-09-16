"""Failing tests for the proverka4 review. The code is not touched.

The review found the model's own text reaching the audit as silent evidence -
neither demoted nor reported - on ordinary logs, and a confirmation by name
landing on the wrong tool. Every test in groups N to P fails on the proverka4
code (48c8b8a) and passes under any fix that closes the hole, however it is
made. Group K runs the same harness on inputs proverka4 already handles, so a
red N-T test is known to be red for its finding and not for its scaffolding.

Groups:
  R  the answering call's own text comes back in a shape the rule misses (6)
  T  reading one wide turn without ids grows with the square of the turn (2)
  K  controls, passing on proverka4 (3)

Groups N, S, M, F, E, W and P were removed in round 18 with the echo warnings
they accepted or guarded (a notes store read back in a later turn, a note saved
and read in one message, results the reader could not match to a call,
confirmation by name, where a warning points, `audit()` raising). Each of those
logs is now read without a word, and README says so.

Group R accepts ONLY demotion. The text stands in the answering call's own
arguments, so the answer can be decided from the call itself.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick import audit
from tallystick.adapters import openai_chat as oc
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {"document", "tool_result"}
ECHO = "The capital of Australia is Sydney"
QUESTION = {"role": "user", "content": "What is the capital of Australia?"}
ANSWER = {"role": "assistant", "content": ECHO}


def _call(name, args, cid=None):
    call = {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    if cid:
        call["id"] = cid
    return call


def _assistant(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def _tool(content, name=None, cid=None):
    message = {"role": "tool", "content": content}
    if name is not None:
        message["name"] = name
    if cid:
        message["tool_call_id"] = cid
    return message


def _turn(name, args, cid, result):
    return [_assistant(_call(name, args, cid)), _tool(result, name, cid)]


SAVE = _turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved")


def _notes(result):
    """A note saved in one turn and read back in the next, answering `result`."""
    return [QUESTION, *SAVE, *_turn("read_note", {"key": "capital"}, "c2", result), ANSWER]


def _result(trace, content):
    hits = [a for a in trace["artifacts"]
            if a["artifact_id"].startswith("t") and a["content"] == content]
    assert len(hits) == 1, f"expected one tool artifact holding {content!r}, got {hits}"
    return hits[0]


def _assert_not_silent(messages, content):
    """The result is not a root. Only demotion counts: round 18 removed the
    warnings that groups N, S, M, F, E, W and P used to accept."""
    trace = oc.to_trace(messages)
    art = _result(trace, content)
    label = f"tool[{art['artifact_id'][1:]}]"
    assert art["kind"] not in ROOTS, (
        f"{label} hands back text the model wrote and is read as {art['kind']!r}")


def _check_trace(tmp_path, messages, *flags):
    log = tmp_path / "log.json"
    log.write_text(json.dumps(messages), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["check-trace", str(log), "--quiet", *flags])
    return code, err.getvalue()


# --- R: the answering call's own text, in a shape the rule misses -----------

@pytest.mark.parametrize("name, args, result", [
    pytest.param("echo", {"text": ECHO}, json.dumps({"echo": ECHO}), id="R1-json-envelope"),
    pytest.param("save_note", {"text": ECHO}, f"Saved note: {ECHO}", id="R2-prefix"),
    pytest.param("python", {"code": f'print("{ECHO}")'}, f"{ECHO}\n[Execution time: 0.01s]",
                 id="R3-status-line-after"),
    pytest.param("python", {"code": f'x = "{ECHO}"\nx'}, repr(ECHO), id="R4-repl-repr"),
    pytest.param("echo", {"text": "The capital of Australia\nis Sydney"}, ECHO, id="R5-reflowed"),
    pytest.param("save_note", {"text": ECHO}, json.dumps({"saved": True, "text": ECHO}, indent=2),
                 id="R6-json-indented"),
])
def test_the_answering_calls_own_text_is_not_silent_evidence(name, args, result):
    _assert_not_silent([QUESTION, *_turn(name, args, "c1", result), ANSWER], result)


# --- T: reading time without ids --------------------------------------------

BUDGET_SECONDS = 2.0      # the same budget as test_openai_chat_turn_width.py group E

_WIDE_TURN = r'''
import json, sys
from tallystick.adapters.openai_chat import to_trace
mode = sys.argv[1]
code = 'x = compute("value")\n' * 1000                       # about 21 KB
calls = []
for k in range(100):
    call = {"type": "function", "function": {"name": "python", "arguments": json.dumps({"code": code})}}
    if mode == "ids":
        call["id"] = f"c{k}"
    calls.append(call)
m = [{"role": "user", "content": "q"}, {"role": "assistant", "content": None, "tool_calls": calls}]
for k in range(100):
    result = {"role": "tool", "content": f"result {k}"}
    if mode == "ids":
        result.update(tool_call_id=f"c{k}", name="python")
    elif mode == "name":
        result["name"] = "python"
    m.append(result)
m.append({"role": "assistant", "content": "done"})
to_trace(m)
'''


def _read_wide_turn(mode):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    try:
        subprocess.run([sys.executable, "-c", _WIDE_TURN, mode], cwd=ROOT, env=env,
                       check=True, timeout=BUDGET_SECONDS, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail(f"reading one turn of 100 parallel 21 KB calls ({mode}) took longer "
                    f"than {BUDGET_SECONDS:.0f} s")


@pytest.mark.parametrize("mode", [
    # proverka4: about 7.9 s; with ids 0.15 s.
    pytest.param("name", id="T1-results-carry-a-name-but-no-id"),
    # proverka4: about 15.6 s.
    pytest.param("none", id="T2-results-carry-neither"),
])
def test_a_wide_turn_without_ids_reads_in_linear_time(mode):
    _read_wide_turn(mode)


# --- K: controls, passing on proverka4 --------------------------------------

def test_control_the_answering_calls_verbatim_text_is_demoted():
    _assert_not_silent([QUESTION, *_turn("python", {"code": f'print("{ECHO}")'}, "c1", ECHO), ANSWER],
                       ECHO)


def test_control_a_wide_turn_with_ids_reads_within_budget():
    _read_wide_turn("ids")


def test_control_python_audit_passes_a_balanced_trace_without_warnings():
    assert audit(str(ROOT / "examples" / "balanced_run.json")).books_balance
