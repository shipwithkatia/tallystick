"""`check-trace` does not pass a run on an echo warning nobody has looked at.

The OpenAI reader keeps a tool result as evidence when its last line is text
the model wrote in an EARLIER call - a note read back, a variable an interpreter
kept - because the log cannot tell a value handed back from a value confirmed.
It says so in `_meta.echoes_from_earlier_turns`. Before this gate, a log that
laundered the model's words through a notes store exited 0, and with `--quiet`
the warning was never printed: a CI job certified a place nobody had checked.

Now: any such warning makes the exit 1 with the reason
`unreviewed_echo_warnings`, printed even under `--quiet`, until the operator
confirms it BY TOOL NAME with `--accept-echo-warning NAME` (repeatable). A
blanket confirmation was tried and removed: accepted once, it would pass next
month's new warning about a different tool without a word. Confirming one tool
clears the warnings about that tool and nothing else. A log with no such warning
behaves exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
REASON = "unreviewed_echo_warnings"
ACCEPT = "--accept-echo-warning"
ECHO = "The capital of Australia is Sydney"
POPULATION = "Canberra has 467,000 residents"


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _turn(name, args, cid, result):
    return [{"role": "assistant", "content": None, "tool_calls": [_call(name, args, cid)]},
            {"role": "tool", "tool_call_id": cid, "name": name, "content": result}]


def _write(tmp_path, messages, name="log.json"):
    path = tmp_path / name
    path.write_text(json.dumps(messages), encoding="utf-8")
    return str(path)


def _notes_log(tmp_path, *, ends_on_the_note=False, reader="read_note"):
    """A note saved in one turn and read back in the next: the reader keeps the
    read as evidence and reports it as an echo from an earlier turn."""
    messages = [{"role": "user", "content": "What is the capital of Australia?"},
                *_turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved"),
                *_turn(reader, {"key": "capital"}, "c2", ECHO)]
    if not ends_on_the_note:
        messages.append({"role": "assistant", "content": ECHO})
    return _write(tmp_path, messages)


def _two_warnings_log(tmp_path):
    """Two echoes from earlier turns, about two different tools: a note read
    back (tool[4], read_note) and a variable an interpreter kept (tool[8],
    python)."""
    messages = [{"role": "user", "content": "Tell me about Canberra."},
                *_turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved"),
                *_turn("read_note", {"key": "capital"}, "c2", ECHO),
                *_turn("python", {"code": f'population = "{POPULATION}"\nprint("ok")'}, "c3", "ok"),
                *_turn("python", {"code": "print(population)"}, "c4", POPULATION),
                {"role": "assistant", "content": f"{ECHO}. {POPULATION}."}]
    return _write(tmp_path, messages)


def _run(capsys, *argv):
    code = main(["check-trace", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _gate(capsys, tmp_path, log, *extra):
    report = tmp_path / "report.json"
    code = _run(capsys, log, "--quiet", "--json", str(report), *extra)[0]
    return code, json.loads(report.read_text(encoding="utf-8"))["gate"]


# --- one warning -------------------------------------------------------------

def test_an_echo_warning_without_confirmation_exits_1_and_names_the_reason(tmp_path, capsys):
    code, out, _err = _run(capsys, _notes_log(tmp_path))
    assert code == 1
    assert REASON in out
    assert "tool[4] (read_note)" in out
    assert f"{ACCEPT} read_note" in out      # says exactly how to confirm


def test_the_same_log_confirmed_by_name_exits_0(tmp_path, capsys):
    code, out, _err = _run(capsys, _notes_log(tmp_path), ACCEPT, "read_note")
    assert code == 0
    assert REASON not in out
    assert "tool[4] (read_note)" in out      # still shown, now as accepted


def test_quiet_still_prints_the_warning_and_exits_1(tmp_path, capsys):
    code, out, err = _run(capsys, _notes_log(tmp_path), "--quiet")
    assert code == 1
    assert out == ""                         # stdout stays empty for scripts
    assert REASON in err
    assert "tool[4] (read_note)" in err


def test_quiet_confirmed_exits_0_and_still_says_what_was_accepted(tmp_path, capsys):
    code, out, err = _run(capsys, _notes_log(tmp_path), "--quiet", ACCEPT, "read_note")
    assert code == 0
    assert out == ""
    assert "tool[4] (read_note)" in err
    assert "accepted" in err
    assert REASON not in err


def test_json_carries_the_exit_its_reason_and_each_warning_by_tool(tmp_path, capsys):
    log = _notes_log(tmp_path)
    warning = {"result": "tool[4]", "tool": "read_note", "line": ECHO}

    assert _gate(capsys, tmp_path, log) == (1, {
        "exit_code": 1, "reasons": [REASON],
        "echo_warnings": {"accepted": [], "unreviewed": [warning]}})
    assert _gate(capsys, tmp_path, log, ACCEPT, "read_note") == (0, {
        "exit_code": 0, "reasons": [],
        "echo_warnings": {"accepted": [warning], "unreviewed": []}})


def test_confirmation_clears_only_its_own_reason(tmp_path, capsys):
    # The note is the last thing in the log: no answer is recorded, so the
    # verdict is CANNOT BE CHECKED. Confirming the echo must not make that pass.
    code, gate = _gate(capsys, tmp_path, _notes_log(tmp_path, ends_on_the_note=True),
                       ACCEPT, "read_note")
    assert code == 1
    assert gate["reasons"] == ["verdict:unauditable"]


# --- confirmation is by name, and only by name --------------------------------

def test_two_warnings_with_one_confirmed_exits_1_on_the_other(tmp_path, capsys):
    # The reason the flag takes a name: a confirmation given for read_note last
    # month must not pass this month's warning about python.
    log = _two_warnings_log(tmp_path)
    code, out, _err = _run(capsys, log, ACCEPT, "read_note")
    assert code == 1
    assert REASON in out
    assert "tool[8] (python)" in out

    code, gate = _gate(capsys, tmp_path, log, ACCEPT, "read_note")
    assert code == 1
    assert REASON in gate["reasons"]
    assert [w["tool"] for w in gate["echo_warnings"]["unreviewed"]] == ["python"]
    assert [w["tool"] for w in gate["echo_warnings"]["accepted"]] == ["read_note"]


def test_two_warnings_both_confirmed_exits_0(tmp_path, capsys):
    code, _out, _err = _run(capsys, _two_warnings_log(tmp_path),
                            ACCEPT, "read_note", ACCEPT, "python")
    assert code == 0


def test_confirming_a_different_tool_confirms_nothing(tmp_path, capsys):
    code, _out, _err = _run(capsys, _notes_log(tmp_path), ACCEPT, "save_note")
    assert code == 1


def test_a_tool_name_built_to_look_like_a_confirmed_one_is_not_confirmed(tmp_path, capsys):
    # The warning text reads "tool[4] (read_note): x): ...". Parsing the name
    # back out of that text would find "read_note" and pass it.
    code, gate = _gate(capsys, tmp_path, _notes_log(tmp_path, reader="read_note): x"),
                       ACCEPT, "read_note")
    assert code == 1
    assert [w["tool"] for w in gate["echo_warnings"]["unreviewed"]] == ["read_note): x"]


def test_the_blanket_confirmation_flag_is_gone(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        main(["check-trace", _notes_log(tmp_path), "--accept-echo-warnings"])
    assert stopped.value.code == 2


# --- a log without warnings: nothing changes ---------------------------------

@pytest.mark.parametrize("name, expected_exit", [
    ("clean_run", 0), ("laundered_search", 0), ("ambiguous_tools", 1), ("otel_spans", 0)])
def test_a_log_without_echo_warnings_is_unchanged(name, expected_exit, capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    log = f"examples/logs/{name}.json"

    code, out, err = _run(capsys, log)
    assert code == expected_exit
    assert REASON not in out and REASON not in err

    code_flag, out_flag, _ = _run(capsys, log, ACCEPT, "read_note")
    assert (code_flag, out_flag) == (code, out)   # a confirmation is inert here

    code_quiet, out_quiet, err_quiet = _run(capsys, log, "--quiet")
    assert (code_quiet, out_quiet, err_quiet) == (expected_exit, "", "")
