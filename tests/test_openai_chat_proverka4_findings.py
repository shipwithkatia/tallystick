"""Failing tests for the proverka4 review. The code is not touched.

The review found the model's own text reaching the audit as silent evidence -
neither demoted nor reported - on ordinary logs, and a confirmation by name
landing on the wrong tool. Every test in groups N to T fails on the proverka4
code (48c8b8a) and passes under any fix that closes the hole, however it is
made. Group K runs the same harness on inputs proverka4 already handles, so a
red N-T test is known to be red for its finding and not for its scaffolding.

Groups:
  N  a notes store answers in a shape the earlier-turn lookup cannot see (6)
  S  the note is saved and read back inside ONE assistant message (3)
  R  the answering call's own text comes back in a shape the rule misses (6)
  M  matching without a usable id: a renamed tool, a rewritten id (2)
  F  confirming the tool a positional guess named accepts another tool's echo (1)
  E  a missing tool name is recorded as "tool", and "tool" confirms it (2)
  T  reading one wide turn without ids grows with the square of the turn (2)
  K  controls, passing on proverka4 (5)

"Not silent" is one of two outcomes, as in test_openai_chat_turn_width.py
group B, but checked more tightly: the result is not a root, OR
`_meta.echo_warning_details` holds a record for that very result. Being named
in some other `_meta` list does not count: the proverka4 mutation run
(bench/mutations/mutate.py, M5) showed group B passing once every result was
merely listed in guessed_tool_names with no warning at all.

For R and M the agreed scheme says demote, because the text stands in the
answering call's own arguments. A structured warning is accepted too - it also
keeps the gate from passing - so these tests fix the outcome, not the mechanism.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

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
    trace = oc.to_trace(messages)
    art = _result(trace, content)
    label = f"tool[{art['artifact_id'][1:]}]"
    warned = [d for d in trace["_meta"].get("echo_warning_details") or []
              if isinstance(d, dict) and d.get("result") == label]
    assert art["kind"] not in ROOTS or warned, (
        f"{label} hands back text the model wrote, is read as {art['kind']!r}, and "
        f"echo_warning_details has no record for it: silent evidence")


def _check_trace(tmp_path, messages, *flags):
    log = tmp_path / "log.json"
    log.write_text(json.dumps(messages), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["check-trace", str(log), "--quiet", *flags])
    return code, err.getvalue()


# --- N: a notes store answering in a shape the lookup cannot see ------------

@pytest.mark.parametrize("result", [
    pytest.param(json.dumps({"key": "capital", "text": ECHO}), id="N1-json-one-line"),
    pytest.param(json.dumps({"key": "capital", "text": ECHO}, indent=2), id="N2-json-indented"),
    pytest.param(f"capital: {ECHO}", id="N3-key-prefix"),
    pytest.param(ECHO + ".", id="N4-trailing-period"),
    pytest.param(f"{ECHO}\n(1 note, 3 ms)", id="N5-status-line-after"),
    pytest.param(repr(ECHO), id="N6-quoted-repr"),
])
def test_a_note_read_back_in_a_common_shape_is_not_silent_evidence(result):
    _assert_not_silent(_notes(result), result)


# --- S: saved and read back in one assistant message ------------------------

GROUP_S = [
    pytest.param(
        [QUESTION,
         _assistant(_call("save_note", {"key": "capital", "text": ECHO}, "c1"),
                    _call("read_note", {"key": "capital"}, "c2")),
         _tool("saved", "save_note", "c1"), _tool(ECHO, "read_note", "c2"), ANSWER],
        id="S1-openai-save-and-read"),
    pytest.param(
        [QUESTION,
         _assistant(_call("write_file", {"path": "a.txt", "content": ECHO}, "c1"),
                    _call("bash", {"cmd": "cat a.txt"}, "c2")),
         _tool("written", "write_file", "c1"), _tool(ECHO, "bash", "c2"), ANSWER],
        id="S2-openai-write-then-cat"),
    pytest.param(
        [QUESTION,
         {"role": "assistant", "content": [
             {"type": "tool_use", "id": "u1", "name": "save_note", "input": {"text": ECHO}},
             {"type": "tool_use", "id": "u2", "name": "read_note", "input": {"key": "capital"}}]},
         {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "u1", "content": "saved"},
             {"type": "tool_result", "tool_use_id": "u2", "content": ECHO}]},
         ANSWER],
        id="S3-anthropic-tool-use-blocks"),
]


@pytest.mark.parametrize("messages", GROUP_S)
def test_a_note_saved_and_read_in_one_message_is_not_silent_evidence(messages):
    _assert_not_silent(messages, ECHO)


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


# --- M: matching without a usable id ----------------------------------------

@pytest.mark.parametrize("messages", [
    pytest.param([QUESTION, _assistant(_call("python", {"code": f'print("{ECHO}")'})),
                  _tool(ECHO, "functions.python"), ANSWER],
                 id="M1-no-id-gateway-renamed-the-tool"),
    pytest.param([QUESTION, _assistant(_call("python", {"code": f'print("{ECHO}")'}, "call_1")),
                  _tool(ECHO, "python", "toolu_1"), ANSWER],
                 id="M2-gateway-rewrote-the-id"),
])
def test_an_echo_whose_call_the_reader_cannot_match_is_not_silent_evidence(messages):
    _assert_not_silent(messages, ECHO)


# --- F: a confirmation lands on the tool a positional guess named -----------

POSITIONAL = [QUESTION, *SAVE,
              _assistant(_call("get_weather", {"city": "Canberra"}),
                         _call("read_note", {"key": "capital"})),
              _tool(ECHO),                    # read_note finished first
              _tool("Sunny, 18 C"),
              ANSWER]


def test_confirming_the_guessed_tool_does_not_accept_another_tools_echo(tmp_path):
    # No ids and no names: the note's echo is placed on get_weather by position,
    # the warning names get_weather, and confirming get_weather passes the gate.
    code, err = _check_trace(tmp_path, POSITIONAL, "--accept-echo-warning", "get_weather")
    art = _result(oc.to_trace(POSITIONAL), ECHO)
    assert code == 1 or art["kind"] not in ROOTS, (
        f"exit {code} with the read_note echo still read as {art['kind']!r}: a confirmation "
        f"for get_weather accepted it\n{err}")


# --- E: a missing tool name is confirmable as "tool" ------------------------

def _unnamed_call(args, cid):
    return {"id": cid, "type": "function", "function": {"arguments": json.dumps(args)}}


@pytest.mark.parametrize("messages", [
    pytest.param([QUESTION, *_turn("", {"text": ECHO}, "c1", "saved"),
                  *_turn("", {"key": "capital"}, "c2", ECHO), ANSWER],
                 id="E1-empty-string-name"),
    pytest.param([QUESTION, _assistant(_unnamed_call({"text": ECHO}, "c1")), _tool("saved", cid="c1"),
                  _assistant(_unnamed_call({"key": "capital"}, "c2")), _tool(ECHO, cid="c2"), ANSWER],
                 id="E2-no-name-key"),
])
def test_a_warning_about_an_unnamed_tool_is_not_confirmed_by_the_placeholder(tmp_path, messages):
    code, err = _check_trace(tmp_path, messages, "--accept-echo-warning", "tool")
    art = _result(oc.to_trace(messages), ECHO)
    assert code == 1 or art["kind"] not in ROOTS, (
        f"exit {code}: the log names no tool, and --accept-echo-warning tool confirmed "
        f"its echo\n{err}")


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

def test_control_a_plain_note_read_back_is_warned():
    _assert_not_silent(_notes(ECHO), ECHO)


def test_control_the_answering_calls_verbatim_text_is_demoted():
    _assert_not_silent([QUESTION, *_turn("python", {"code": f'print("{ECHO}")'}, "c1", ECHO), ANSWER],
                       ECHO)


def test_control_with_ids_confirming_another_tool_confirms_nothing(tmp_path):
    messages = [QUESTION, *SAVE,
                _assistant(_call("get_weather", {"city": "Canberra"}, "c2"),
                           _call("read_note", {"key": "capital"}, "c3")),
                _tool(ECHO, "read_note", "c3"), _tool("Sunny, 18 C", "get_weather", "c2"),
                ANSWER]
    assert _check_trace(tmp_path, messages, "--accept-echo-warning", "get_weather")[0] == 1


def test_control_a_named_tool_is_not_confirmed_by_the_placeholder(tmp_path):
    assert _check_trace(tmp_path, _notes(ECHO), "--accept-echo-warning", "tool")[0] == 1


def test_control_a_wide_turn_with_ids_reads_within_budget():
    _read_wide_turn("ids")
