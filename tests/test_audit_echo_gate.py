"""`tallystick audit` applies the same echo gate as `check-trace`.

Before this, one notes-store log gave two answers. `check-trace` exited 1 with
unreviewed_echo_warnings. `propose` then wrote the posted trace without the
reader's `_meta`, audited it, printed BOOKS BALANCE and exited 0 - crediting the
model's claim to the note that handed the model's own words back. A gate the
standard second command walks around is not a gate.

Now `propose` carries the reading's `_meta` into the posted trace, and `audit`
exits 1 with the reason `unreviewed_echo_warnings` until each tool is confirmed
by name with `--accept-echo-warning NAME` - `propose`'s own audit included. A
confirmation clears that reason and no other, and a posted trace with no echo
warning behaves exactly as before.
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


NOTES_LOG = [{"role": "user", "content": "What is the capital of Australia?"},
             *_turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved"),
             *_turn("read_note", {"key": "capital"}, "c2", ECHO),
             {"role": "assistant", "content": ECHO}]
# One claim on the answer, credited to the note read back (t4): books balance.
NOTES_ANSWERS = [{"claims": [ECHO]},
                 {"credits": [{"artifact_id": "t4", "quote": ECHO}]}]
# Books that do not balance. Not the note's claim without a credit: `propose`
# posts a self-evident credit for a claim that stands verbatim in a root, so
# that one balances on its own - which is exactly the laundering this gate
# stops. A second sentence no source holds is what leaves the books open.
FOUNDED = "It was founded in 1788."
UNFUNDED_LOG = [*NOTES_LOG[:-1], {"role": "assistant", "content": f"{ECHO}. {FOUNDED}"}]
UNFUNDED_ANSWERS = [{"claims": [f"{ECHO}.", FOUNDED]},
                    {"credits": [{"artifact_id": "t4", "quote": ECHO}]},
                    {"credits": []}]

TWO_WARNINGS_LOG = [{"role": "user", "content": "Tell me about Canberra."},
                    *_turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved"),
                    *_turn("read_note", {"key": "capital"}, "c2", ECHO),
                    *_turn("python", {"code": f'population = "{POPULATION}"\nprint("ok")'},
                           "c3", "ok"),
                    *_turn("python", {"code": "print(population)"}, "c4", POPULATION),
                    {"role": "assistant", "content": f"{ECHO}. {POPULATION}."}]
# t6 ("ok") quoted its own call and is model text, so it is segmented first.
TWO_WARNINGS_ANSWERS = [{"claims": []},
                        {"claims": [f"{ECHO}.", f"{POPULATION}."]},
                        {"credits": [{"artifact_id": "t4", "quote": ECHO}]},
                        {"credits": [{"artifact_id": "t8", "quote": POPULATION}]}]


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _propose(tmp_path, capsys, log, answers, *extra, name="run"):
    """Run `propose` through the CLI, the way a person would. Returns
    (exit code, path of the posted trace)."""
    out = tmp_path / f"{name}-posted.json"
    code = main(["propose", _write(tmp_path, f"{name}.json", log), "-o", str(out),
                 "--proposer", "fake",
                 "--script", _write(tmp_path, f"{name}-answers.json", answers), *extra])
    capsys.readouterr()
    return code, str(out)


def _audit(capsys, *argv):
    code = main(["audit", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _gate(tmp_path, capsys, posted, *extra):
    report = tmp_path / "audit.json"
    code = _audit(capsys, posted, "--quiet", "--json", str(report), *extra)[0]
    return code, json.loads(report.read_text(encoding="utf-8"))["gate"]


# --- the warning reaches the posted trace, and propose's own audit ------------

def test_propose_carries_the_reading_into_the_posted_trace(tmp_path, capsys):
    _code, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    meta = json.loads(Path(posted).read_text(encoding="utf-8"))["_meta"]
    assert [d["tool"] for d in meta["echo_warning_details"]] == ["read_note"]
    assert meta["echoes_from_earlier_turns"]


def test_propose_does_not_pass_its_own_audit_on_an_unreviewed_echo(tmp_path, capsys):
    assert _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)[0] == 1
    assert _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS,
                    ACCEPT, "read_note", name="confirmed")[0] == 0


# --- audit on the posted trace -------------------------------------------------

def test_audit_without_confirmation_exits_1_and_names_the_reason(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    code, out, _err = _audit(capsys, posted)
    assert code == 1
    assert REASON in out
    assert "tool[4] (read_note)" in out
    assert f"{ACCEPT} read_note" in out


def test_audit_confirmed_by_name_exits_0(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    code, out, _err = _audit(capsys, posted, ACCEPT, "read_note")
    assert code == 0
    assert REASON not in out


def test_audit_quiet_still_prints_the_warning_and_exits_1(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    code, out, err = _audit(capsys, posted, "--quiet")
    assert code == 1
    assert out == ""
    assert REASON in err
    assert "tool[4] (read_note)" in err


def test_audit_json_carries_the_exit_its_reason_and_each_warning(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    warning = {"result": "tool[4]", "tool": "read_note", "line": ECHO}
    assert _gate(tmp_path, capsys, posted) == (1, {
        "exit_code": 1, "reasons": [REASON],
        "echo_warnings": {"accepted": [], "unreviewed": [warning]}})
    assert _gate(tmp_path, capsys, posted, ACCEPT, "read_note") == (0, {
        "exit_code": 0, "reasons": [],
        "echo_warnings": {"accepted": [warning], "unreviewed": []}})


def test_audit_confirmation_clears_only_its_own_reason(tmp_path, capsys):
    # The books do not balance ("It was founded in 1788." rests on nothing), and
    # the note is confirmed. The confirmation must not make that pass.
    _c, posted = _propose(tmp_path, capsys, UNFUNDED_LOG, UNFUNDED_ANSWERS)
    code, gate = _gate(tmp_path, capsys, posted, ACCEPT, "read_note")
    assert code == 1
    assert gate["reasons"] == ["books_do_not_balance"]


def test_audit_two_warnings_with_one_confirmed_exits_1_on_the_other(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, TWO_WARNINGS_LOG, TWO_WARNINGS_ANSWERS)
    code, out, _err = _audit(capsys, posted, ACCEPT, "read_note")
    assert code == 1
    assert "tool[8] (python)" in out
    assert _audit(capsys, posted, ACCEPT, "read_note", ACCEPT, "python")[0] == 0


def test_audit_confirming_a_different_tool_confirms_nothing(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    assert _audit(capsys, posted, ACCEPT, "save_note")[0] == 1


def test_a_warning_whose_tool_name_was_removed_cannot_be_confirmed(tmp_path, capsys):
    # A posted trace edited by hand, or written before tool names were
    # recorded: the warning text is there, the structured record is not.
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    data = json.loads(Path(posted).read_text(encoding="utf-8"))
    del data["_meta"]["echo_warning_details"]
    Path(posted).write_text(json.dumps(data), encoding="utf-8")
    assert _audit(capsys, posted, ACCEPT, "read_note")[0] == 1


def test_audit_has_no_blanket_confirmation(tmp_path, capsys):
    _c, posted = _propose(tmp_path, capsys, NOTES_LOG, NOTES_ANSWERS)
    with pytest.raises(SystemExit) as stopped:
        main(["audit", posted, "--accept-echo-warnings"])
    assert stopped.value.code == 2


# --- no echo warning: nothing changes -------------------------------------------

@pytest.mark.parametrize("name, expected_exit", [("balanced_run", 0), ("laundered_summary", 1)])
def test_a_posted_trace_without_echo_warnings_is_unchanged(name, expected_exit, capsys):
    posted = str(ROOT / "examples" / f"{name}.json")
    code, out, err = _audit(capsys, posted)
    assert code == expected_exit
    assert REASON not in out and REASON not in err
    assert _audit(capsys, posted, ACCEPT, "read_note")[:2] == (code, out)
    assert _audit(capsys, posted, "--quiet") == (expected_exit, "", "")


def test_propose_without_echo_warnings_is_unchanged(tmp_path, capsys):
    raw = ROOT / "examples" / "raw_research_run.json"
    answers = json.loads((ROOT / "examples" / "fake_answers.json").read_text(encoding="utf-8"))
    for extra in ((), (ACCEPT, "read_note")):
        code, posted = _propose(tmp_path, capsys, json.loads(raw.read_text(encoding="utf-8")),
                                answers, *extra, name="research")
        assert code == 1                   # laundering found, as before
        meta = json.loads(Path(posted).read_text(encoding="utf-8")).get("_meta") or {}
        assert not meta.get("echoes_from_earlier_turns")
