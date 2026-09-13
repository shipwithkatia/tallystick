"""`check-trace` does not pass a run on an echo warning nobody has looked at.

The OpenAI reader keeps a tool result as evidence when its last line is text
the model wrote in an EARLIER call - a note read back, a variable an interpreter
kept - because the log cannot tell a value handed back from a value confirmed.
It says so in `_meta.echoes_from_earlier_turns`. Before this gate, a log that
laundered the model's words through a notes store exited 0, and with `--quiet`
the warning was never printed: a CI job certified a place nobody had checked.

Now: any such warning makes the exit 1 with the reason
`unreviewed_echo_warnings`, printed even under `--quiet`, until the operator
confirms with `--accept-echo-warnings`. The flag clears that reason and no
other. A log with no such warning behaves exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
REASON = "unreviewed_echo_warnings"
FLAG = "--accept-echo-warnings"
ECHO = "The capital of Australia is Sydney"


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _notes_log(tmp_path, *, ends_on_the_note=False):
    """A note saved in one turn and read back in the next: the reader keeps the
    read as evidence and reports it as an echo from an earlier turn."""
    messages = [
        {"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": None,
         "tool_calls": [_call("save_note", {"key": "capital", "text": ECHO}, "c1")]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "saved"},
        {"role": "assistant", "content": None,
         "tool_calls": [_call("read_note", {"key": "capital"}, "c2")]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": ECHO},
    ]
    if not ends_on_the_note:
        messages.append({"role": "assistant", "content": ECHO})
    path = tmp_path / "notes.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    return str(path)


def _run(capsys, *argv):
    code = main(["check-trace", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- a log with an echo warning ----------------------------------------------

def test_an_echo_warning_without_the_flag_exits_1_and_names_the_reason(tmp_path, capsys):
    code, out, _err = _run(capsys, _notes_log(tmp_path))
    assert code == 1
    assert REASON in out
    assert "tool[4] (read_note)" in out
    assert FLAG in out                       # says how to confirm


def test_the_same_log_with_the_flag_exits_0(tmp_path, capsys):
    code, out, _err = _run(capsys, _notes_log(tmp_path), FLAG)
    assert code == 0
    assert REASON not in out
    assert "tool[4] (read_note)" in out      # still shown, now as accepted


def test_quiet_still_prints_the_warning_and_exits_1(tmp_path, capsys):
    code, out, err = _run(capsys, _notes_log(tmp_path), "--quiet")
    assert code == 1
    assert out == ""                         # stdout stays empty for scripts
    assert REASON in err
    assert "tool[4] (read_note)" in err


def test_quiet_with_the_flag_exits_0_and_still_says_what_was_accepted(tmp_path, capsys):
    code, out, err = _run(capsys, _notes_log(tmp_path), "--quiet", FLAG)
    assert code == 0
    assert out == ""
    assert "tool[4] (read_note)" in err
    assert "accepted" in err
    assert REASON not in err


def test_json_carries_the_exit_and_its_reason(tmp_path, capsys):
    log = _notes_log(tmp_path)
    report = tmp_path / "report.json"

    assert _run(capsys, log, "--quiet", "--json", str(report))[0] == 1
    gate = json.loads(report.read_text(encoding="utf-8"))["gate"]
    assert gate == {"exit_code": 1, "reasons": [REASON], "echo_warnings_accepted": False}

    assert _run(capsys, log, "--quiet", "--json", str(report), FLAG)[0] == 0
    gate = json.loads(report.read_text(encoding="utf-8"))["gate"]
    assert gate == {"exit_code": 0, "reasons": [], "echo_warnings_accepted": True}


def test_the_flag_clears_only_its_own_reason(tmp_path, capsys):
    # The note is the last thing in the log: no answer is recorded, so the
    # verdict is CANNOT BE CHECKED. Accepting the echo warning must not make
    # that pass.
    log = _notes_log(tmp_path, ends_on_the_note=True)
    report = tmp_path / "report.json"
    code, _out, _err = _run(capsys, log, "--quiet", "--json", str(report), FLAG)
    assert code == 1
    gate = json.loads(report.read_text(encoding="utf-8"))["gate"]
    assert gate["reasons"] == ["verdict:unauditable"]


# --- a log without one: nothing changes --------------------------------------

@pytest.mark.parametrize("name, expected_exit", [
    ("clean_run", 0), ("laundered_search", 0), ("ambiguous_tools", 1), ("otel_spans", 0)])
def test_a_log_without_echo_warnings_is_unchanged(name, expected_exit, capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    log = f"examples/logs/{name}.json"

    code, out, err = _run(capsys, log)
    assert code == expected_exit
    assert REASON not in out and REASON not in err

    code_flag, out_flag, _ = _run(capsys, log, FLAG)
    assert (code_flag, out_flag) == (code, out)   # the flag is inert here

    code_quiet, out_quiet, err_quiet = _run(capsys, log, "--quiet")
    assert (code_quiet, out_quiet, err_quiet) == (expected_exit, "", "")
