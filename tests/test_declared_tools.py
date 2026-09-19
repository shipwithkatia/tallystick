"""Tools nobody declared: counted in the report always, blocking only on request.

A tool result is a root by design - the audit stops there on purpose - so a
tool the operator said nothing about is not a suspicious place, and blocking on
it would block every real trace (measured on AgentHallu: 95.5% of trajectories
even with the corpus's four echo tools declared). So:

- the report says, on one line, how many results came from tools nobody
  declared and from how many names. It changes no exit code;
- `--require-declared-tools` is the strict mode for an operator whose tool set
  is fixed: exit 1 with `undeclared_tools` until every tool in the log is
  declared as returning the model's text, text verbatim, or external evidence;
- `--tool-returns-external NAME` is that third declaration - a search API or
  any tool whose result is external evidence in its own words. It clears the
  echo WARNINGS about that tool, which are guesses the operator now vouches
  for. It does not clear a DEMOTION: a result found in the arguments of the very
  call it answers is something the reader established, and one flag must not
  switch off the only part of the protection that works for certain.
"""

from __future__ import annotations

import json
from pathlib import Path

from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
RAW_RUN = str(ROOT / "examples" / "raw_research_run.json")   # a native trace, no reading
REASON = "undeclared_tools"
QUESTION = {"role": "user", "content": "What was Northwind's revenue in 2024?"}
PAGE = "Northwind reported revenue of 1,840 million in 2024."
DIGEST = "Revenue grew 9% year over year."
ANSWER = {"role": "assistant", "content": PAGE}


def _turn(name, args, cid, result):
    return [{"role": "assistant", "content": None, "tool_calls": [
                {"id": cid, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": cid, "name": name, "content": result}]


# Three tool results from two tools: read_file twice, web_search once.
LOG = [QUESTION,
       *_turn("read_file", {"path": "report_2024.txt"}, "c1", PAGE),
       *_turn("web_search", {"q": "northwind growth 2024"}, "c2", DIGEST),
       *_turn("read_file", {"path": "notes.txt"}, "c3", "Q4 figures are provisional."),
       ANSWER]
ALL_DECLARED = ["--tool-returns-verbatim", "read_file", "--tool-returns-external", "web_search"]


def _write(tmp_path, data, name="log.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _run(capsys, *argv):
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _gate(tmp_path, capsys, command, log, *extra):
    report = tmp_path / "report.json"
    code = _run(capsys, command, log, "--quiet", "--json", report, *extra)[0]
    return code, json.loads(report.read_text(encoding="utf-8"))


# --- the report line, which blocks nothing ------------------------------------

def test_the_report_counts_results_and_names_from_undeclared_tools(tmp_path, capsys):
    code, out, _err = _run(capsys, "check-trace", _write(tmp_path, LOG))
    lines = [ln for ln in out.splitlines() if "undeclared" in ln]
    assert len(lines) == 1, out
    assert "3 result(s) from 2 tool name(s)" in lines[0]
    assert "read_file" in lines[0] and "web_search" in lines[0]
    assert code == 0                         # counted, not blocked


def test_the_json_report_carries_the_count(tmp_path, capsys):
    _code, payload = _gate(tmp_path, capsys, "check-trace", _write(tmp_path, LOG))
    assert payload["reading"]["undeclared_tool_results"] == {
        "results": 3, "tools": ["read_file", "web_search"]}
    assert REASON not in payload["gate"]["reasons"]


def test_declared_tools_are_not_counted(tmp_path, capsys):
    log = _write(tmp_path, LOG)
    _code, out, _err = _run(capsys, "check-trace", log, "--tool-returns-verbatim", "read_file")
    lines = [ln for ln in out.splitlines() if "undeclared" in ln]
    assert len(lines) == 1 and "1 result(s) from 1 tool name(s)" in lines[0], out
    _code, out, _err = _run(capsys, "check-trace", log, *ALL_DECLARED)
    assert not [ln for ln in out.splitlines() if "undeclared" in ln], out


def test_declaring_a_tool_external_changes_nothing_about_its_reading(tmp_path, capsys):
    log = _write(tmp_path, LOG)
    _c, plain = _gate(tmp_path, capsys, "check-trace", log)
    _c, declared = _gate(tmp_path, capsys, "check-trace", log, "--tool-returns-external", "web_search")
    for key in ("verdict", "defects"):
        assert plain.get(key) == declared.get(key)
    assert declared["reading"]["external_tools"] == ["web_search"]


ECHO = "The capital of Australia is Sydney"


def test_declaring_a_tool_external_does_not_undo_a_demotion_the_reader_established(tmp_path, capsys):
    # The result is the model's sentence, found inside the arguments of the very
    # call it answers. Declared external or not, it is the model's own text.
    handed_back = json.dumps({"saved": True, "text": ECHO}, indent=2)
    log = [QUESTION,
           *_turn("save_note", {"text": ECHO}, "c1", handed_back),
           {"role": "assistant", "content": "Saved."}]
    path = _write(tmp_path, log)
    for flags in ((), ("--tool-returns-external", "save_note")):
        out = tmp_path / "trace.json"
        assert _run(capsys, "convert", path, "-o", out, *flags)[0] == 0
        trace = json.loads(out.read_text(encoding="utf-8"))
        kinds = {a["artifact_id"]: a["kind"] for a in trace["artifacts"]}
        assert kinds["t2"] == "intermediate", (flags, kinds)
        assert trace["_meta"]["echoed_back_tool_results"], flags


# --- --require-declared-tools: the strict mode ---------------------------------

def test_strict_mode_exits_1_until_every_tool_is_declared(tmp_path, capsys):
    log = _write(tmp_path, LOG)
    code, payload = _gate(tmp_path, capsys, "check-trace", log, "--require-declared-tools")
    assert code == 1
    assert payload["gate"]["reasons"] == [REASON]
    code, payload = _gate(tmp_path, capsys, "check-trace", log, "--require-declared-tools",
                          "--tool-returns-verbatim", "read_file")
    assert code == 1                         # web_search is still undeclared
    code, payload = _gate(tmp_path, capsys, "check-trace", log, "--require-declared-tools",
                          *ALL_DECLARED)
    assert (code, payload["gate"]["reasons"]) == (0, [])


def test_strict_mode_says_why_on_the_terminal_and_under_quiet(tmp_path, capsys):
    log = _write(tmp_path, LOG)
    code, out, _err = _run(capsys, "check-trace", log, "--require-declared-tools")
    assert code == 1 and REASON in out and "web_search" in out
    code, out, err = _run(capsys, "check-trace", log, "--require-declared-tools", "--quiet")
    assert (code, out) == (1, "")
    assert REASON in err and "read_file" in err


def test_a_model_text_declaration_counts_as_declared(tmp_path, capsys):
    log = _write(tmp_path, LOG)
    code, _payload = _gate(tmp_path, capsys, "check-trace", log, "--require-declared-tools",
                           "--tool-returns-model-text", "read_file",
                           "--tool-returns-external", "web_search")
    assert code == 0


def test_a_tool_cannot_be_declared_external_and_something_else(tmp_path, capsys):
    code, _out, err = _run(capsys, "check-trace", _write(tmp_path, LOG),
                           "--tool-returns-external", "read_file",
                           "--tool-returns-verbatim", "read_file")
    assert code == 2
    assert "read_file" in err


def test_strict_mode_on_a_trace_with_no_reading_blocks_nothing_and_says_so(capsys):
    code, _out, err = _run(capsys, "check-trace", RAW_RUN,
                           "--require-declared-tools")
    plain_code = _run(capsys, "check-trace", RAW_RUN)[0]
    assert code == plain_code
    assert "--require-declared-tools" in err


# --- the same gate on the posted trace -----------------------------------------

def _posted(tmp_path, capsys, *declarations):
    answers = [{"claims": [PAGE]}, {"credits": [{"artifact_id": "t2", "quote": PAGE}]}]
    script = _write(tmp_path, answers, "answers.json")
    posted = tmp_path / "posted.json"
    code = _run(capsys, "propose", _write(tmp_path, LOG), "-o", posted, "--proposer", "fake",
                "--script", script, "--no-audit", *declarations)[0]
    assert code == 0
    return str(posted)


def test_audit_counts_nothing_as_a_failure_without_the_flag(tmp_path, capsys):
    posted = _posted(tmp_path, capsys)
    assert _run(capsys, "audit", posted)[0] == 0


def test_audit_in_strict_mode_uses_the_declarations_made_when_reading(tmp_path, capsys):
    undeclared = _posted(tmp_path, capsys)
    code, payload = _gate(tmp_path, capsys, "audit", undeclared, "--require-declared-tools")
    assert (code, payload["gate"]["reasons"]) == (1, [REASON])
    declared = _posted(tmp_path, capsys, *ALL_DECLARED)
    code, payload = _gate(tmp_path, capsys, "audit", declared, "--require-declared-tools")
    assert (code, payload["gate"]["reasons"]) == (0, [])


def test_propose_passes_strict_mode_to_its_own_audit(tmp_path, capsys):
    answers = [{"claims": [PAGE]}, {"credits": [{"artifact_id": "t2", "quote": PAGE}]}]
    script = _write(tmp_path, answers, "answers.json")
    code = _run(capsys, "propose", _write(tmp_path, LOG), "-o", tmp_path / "p.json",
                "--proposer", "fake", "--script", script, "--require-declared-tools")[0]
    assert code == 1

# ---------------------------------------------------------------------------
# Round 19: the echo detection is back as a note that moves no exit code.
# Put back from f84f278, where round 18 removed them with the detection.
# Tests of the confirmation gate stay out; an exit of 1 became 0.
# ---------------------------------------------------------------------------


def test_declaring_a_tool_external_clears_its_echo_warning(tmp_path, capsys):
    notes = [QUESTION,
             *_turn("save_note", {"key": "capital", "text": ECHO}, "c1", "saved"),
             *_turn("read_note", {"key": "capital"}, "c2", ECHO),
             {"role": "assistant", "content": ECHO}]
    log = _write(tmp_path, notes)
    code, payload = _gate(tmp_path, capsys, "check-trace", log)
    # Round 19: noted, not gated.
    assert (code, payload["gate"]["reasons"]) == (0, [])
    assert payload["may_be_model_text"]["count"] == 1
    code, payload = _gate(tmp_path, capsys, "check-trace", log,
                          "--tool-returns-external", "read_note")
    assert (code, payload["gate"]["reasons"]) == (0, [])
    assert payload["reading"]["echo_warning_details"] == []
