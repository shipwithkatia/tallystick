"""Failing tests for the proverka4 review. The code is not touched.

The review found the model's own text reaching the audit as silent evidence -
neither demoted nor reported - on ordinary logs, and a confirmation by name
landing on the wrong tool. Every test in groups N to P fails on the proverka4
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
  W  a warning addresses the reader's expanded message list, not the file,
     and does not give the call id (1)
  P  `from tallystick import audit`, the path the README recommends for tests,
     returns a balance instead of refusing on an unreviewed echo (2)
  K  controls, passing on proverka4 (8)

"Not silent" is one of two outcomes, as in test_openai_chat_turn_width.py
group B, but checked more tightly: the result is not a root, OR
`_meta.echo_warning_details` holds a record for that very result. Being named
in some other `_meta` list does not count: the proverka4 mutation run
(bench/mutations/mutate.py, M5) showed group B passing once every result was
merely listed in guessed_tool_names with no warning at all.

Group R accepts ONLY demotion. The text stands in the answering call's own
arguments, so the answer can be decided from the call itself. Letting a warning
close these cases would move decidable cases into the gate: it would fire often,
and confirmations would end up attached to everything.

Group M accepts a warning. Without a usable id the reader does not know which
call a result answers, and a warning is the more honest reading there.
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


def _assert_not_silent(messages, content, *, a_warning_counts=True):
    """The result is not a root - or, where `a_warning_counts`, it has its own
    record in echo_warning_details. Group R passes False: only demotion."""
    trace = oc.to_trace(messages)
    art = _result(trace, content)
    label = f"tool[{art['artifact_id'][1:]}]"
    warned = [d for d in trace["_meta"].get("echo_warning_details") or []
              if isinstance(d, dict) and d.get("result") == label]
    assert art["kind"] not in ROOTS or (a_warning_counts and warned), (
        f"{label} hands back text the model wrote and is read as {art['kind']!r}"
        + (" with no record in echo_warning_details: silent evidence" if a_warning_counts
           else f" (warning record present: {bool(warned)}); here only demotion counts"))


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
    _assert_not_silent([QUESTION, *_turn(name, args, "c1", result), ANSWER], result,
                       a_warning_counts=False)


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


# --- W: where a warning points ----------------------------------------------

# Message [4] of this file carries BOTH tool results. The reader splits it into
# two tool messages, so the note's echo becomes tool[5] - and message [5] of the
# file is the final answer. The call id, u3, is not printed at all.
ANTHROPIC_TWO_RESULTS = [
    QUESTION,
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "u1", "name": "save_note", "input": {"text": ECHO}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "u1", "content": "saved"}]},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "u2", "name": "web_search", "input": {"q": "capital of australia"}},
        {"type": "tool_use", "id": "u3", "name": "read_note", "input": {"key": "capital"}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "u2", "content": "Canberra is the capital city of Australia."},
        {"type": "tool_result", "tool_use_id": "u3", "content": ECHO}]},
    ANSWER,
]


def _warning_lines(tmp_path, messages):
    """The printed warning lines - those quoting the matched text - from
    `check-trace --quiet` (stderr) and from the terminal's warning block."""
    log = tmp_path / "log.json"
    log.write_text(json.dumps(messages), encoding="utf-8")
    runs = {}
    for mode, flags in (("quiet", ["--quiet"]), ("terminal", [])):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            main(["check-trace", str(log), *flags])
        runs[mode] = (out.getvalue(), err.getvalue())
    quiet = [ln for ln in runs["quiet"][1].splitlines() if ECHO in ln]
    block = runs["terminal"][0].split("UNREVIEWED ECHO WARNINGS", 1)
    terminal = [ln for ln in block[1].splitlines() if ECHO in ln] if len(block) == 2 else []
    return {"--quiet stderr": quiet, "terminal": terminal}


def _names_position(line, k):
    """`k` stands in the line as a number of its own."""
    return re.search(rf"(?<!\d){k}(?!\d)", line) is not None


def test_a_warning_names_the_message_in_the_file_and_the_call_id(tmp_path):
    # DECIDED (2026-09-14): a warning's number is there so a person can find the
    # place in THEIR file, so it addresses the file - counted from 0, like the
    # existing tool[k] labels and a JSON array - not the list after parsing.
    # And it gives the call id when the log has one: an id can be searched for
    # and does not move when the file is reformatted.
    lines = _warning_lines(tmp_path, ANTHROPIC_TWO_RESULTS)
    assert all(lines.values()), f"the log must produce the echo warning in both outputs: {lines}"
    for where, found in lines.items():
        for line in found:
            assert "u3" in line, f"{where}: the warning does not give the call id u3: {line!r}"
            assert _names_position(line, 4) and not _names_position(line, 5), (
                f"{where}: the warning must point at message 4 of the file, which holds the "
                f"result, not 5, which is the final answer: {line!r}")


# --- P: the Python API the README recommends for tests ----------------------

def _posted_notes_trace(tmp_path):
    """The note log posted through the CLI, the way a person runs it: `propose`
    copies the reading's `_meta`, echo warning included, into the file."""
    log = tmp_path / "notes.json"
    log.write_text(json.dumps(_notes(ECHO)), encoding="utf-8")
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([{"claims": [ECHO]},
                                   {"credits": [{"artifact_id": "t4", "quote": ECHO}]}]),
                       encoding="utf-8")
    posted = tmp_path / "posted.json"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = main(["propose", str(log), "-o", str(posted), "--proposer", "fake",
                     "--script", str(answers), "--no-audit"])
    assert code == 0, "propose could not post the fixture"
    data = json.loads(posted.read_text(encoding="utf-8"))
    assert data["_meta"]["echo_warning_details"], "the fixture must carry an echo warning"
    return posted, data


@pytest.mark.parametrize("given", ["path", "dict"])
def test_readme_python_audit_refuses_an_unreviewed_echo(tmp_path, given):
    # README: `balance = audit("run.json"); assert balance.books_balance  # drop
    # straight into a test suite`. On this file `tallystick audit` exits 1 with
    # unreviewed_echo_warnings (control below); audit() says the books balance.
    #
    # DECIDED (2026-09-14), not open for the fixing session: while an echo
    # warning is unreviewed, audit() RAISES. Returning books_balance False
    # would mix "the books do not balance" with "this could not be checked",
    # the distinction exit codes 1 and 2 exist for. The error must name the
    # reason, unreviewed_echo_warnings, so that a crash for any other cause
    # does not pass this test.
    posted, data = _posted_notes_trace(tmp_path)
    source = str(posted) if given == "path" else data
    try:
        balance = audit(source)
    except Exception as exc:  # noqa: BLE001 - the type is not decided, the reason is
        assert "unreviewed_echo_warnings" in str(exc), (
            f"audit() raised, but not for the unreviewed echo warning: {exc!r}")
        return
    pytest.fail(f"audit() returned a balance (books_balance={balance.books_balance}) "
                f"instead of raising on an unreviewed echo warning")


# --- K: controls, passing on proverka4 --------------------------------------

def test_control_a_plain_note_read_back_is_warned():
    _assert_not_silent(_notes(ECHO), ECHO)


def test_control_the_answering_calls_verbatim_text_is_demoted():
    _assert_not_silent([QUESTION, *_turn("python", {"code": f'print("{ECHO}")'}, "c1", ECHO), ANSWER],
                       ECHO, a_warning_counts=False)


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


def test_control_the_cli_audit_exits_1_on_the_posted_notes_trace(tmp_path):
    posted, _data = _posted_notes_trace(tmp_path)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert main(["audit", str(posted)]) == 1


def test_control_an_openai_warning_names_its_file_message(tmp_path):
    # With one tool message per result, the reader's index and the file's agree:
    # the same position check passes on proverka4.
    lines = _warning_lines(tmp_path, _notes(ECHO))
    assert all(lines.values())
    assert all(_names_position(ln, 4) for found in lines.values() for ln in found)


def test_control_python_audit_passes_a_balanced_trace_without_warnings():
    assert audit(str(ROOT / "examples" / "balanced_run.json")).books_balance
