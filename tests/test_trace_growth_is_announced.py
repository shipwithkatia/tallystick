"""The trace may grow with the square of the turns, but not in silence.

The format keeps `inputs` as an explicit list, and a chat sends its whole
history every turn, so every step of a converted chat lists every artifact
recorded before it. A 2000-turn log of 610 KB becomes a trace of 130 MB. The
format stays - a changed format would be read by older versions of the core
without an error and give a wrong verdict in silence, which is worse - so the
reading says out loud when the trace it produced is TRACE_SIZE_NOTE_RATIO
times the log or more: how many steps, how big, and why.
"""

from __future__ import annotations

import json

from tallystick.cli import main
from tallystick import convert


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _chat(turns):
    messages = [{"role": "user", "content": "Find the population of Canberra."}]
    for k in range(turns):
        messages += [{"role": "assistant", "content": f"Step {k}: looking it up.",
                      "tool_calls": [_call("search", {"q": f"canberra {k}"}, f"c{k}")]},
                     {"role": "tool", "tool_call_id": f"c{k}", "name": "search",
                      "content": f"Result {k}: a page about topic {k * 7}."}]
    messages.append({"role": "assistant", "content": "About 460,000."})
    return messages


def _log(tmp_path, turns):
    path = tmp_path / "log.json"
    path.write_text(json.dumps(_chat(turns)), encoding="utf-8")
    return str(path)


LONG, SHORT = 300, 3


def test_the_ratio_is_one_named_constant():
    assert convert.TRACE_SIZE_NOTE_RATIO == 10, (
        "named before it was measured: an order of magnitude more than the log")


def test_convert_says_when_the_trace_outgrows_the_log(tmp_path, capsys):
    out = tmp_path / "trace.json"
    assert main(["convert", "--from", "openai", _log(tmp_path, LONG), "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "trace size:" in text, text
    assert "square" in text and "steps" in text, text


def test_check_trace_says_it_in_the_report(tmp_path, capsys):
    main(["check-trace", "--from", "openai", _log(tmp_path, LONG)])
    assert "trace size:" in capsys.readouterr().out


def test_check_trace_says_it_under_quiet_on_stderr(tmp_path, capsys):
    main(["check-trace", "--from", "openai", _log(tmp_path, LONG), "--quiet"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "trace size:" in captured.err, captured.err


def test_the_size_reaches_the_json_a_ci_job_keeps(tmp_path):
    out = tmp_path / "report.json"
    main(["check-trace", "--from", "openai", _log(tmp_path, LONG), "--quiet",
          "--json", str(out)])
    size = json.loads(out.read_text(encoding="utf-8"))["reading"]["trace_size"]
    assert size["ratio"] >= convert.TRACE_SIZE_NOTE_RATIO
    # The ratio is the two sizes it says, not a number of its own.
    assert abs(size["ratio"] - size["trace_bytes"] / size["log_bytes"]) < 0.01


def test_the_note_does_not_change_the_exit_code(tmp_path):
    # A long chat is not a defect of the run or of the recording.
    short = main(["convert", "--from", "openai", _log(tmp_path, LONG),
                  "-o", str(tmp_path / "t.json"), "--quiet"])
    assert short == 0


def test_control_a_short_log_says_nothing(tmp_path, capsys):
    out = tmp_path / "trace.json"
    main(["convert", "--from", "openai", _log(tmp_path, SHORT), "-o", str(out)])
    main(["check-trace", "--from", "openai", _log(tmp_path, SHORT)])
    captured = capsys.readouterr()
    assert "trace size:" not in captured.out + captured.err


def test_control_a_trace_given_as_a_trace_is_not_measured(tmp_path, capsys):
    # Nothing was converted, so there is no log to compare with.
    out = tmp_path / "trace.json"
    main(["convert", "--from", "openai", _log(tmp_path, LONG), "-o", str(out), "--quiet"])
    capsys.readouterr()
    main(["check-trace", str(out)])
    assert "trace size:" not in capsys.readouterr().out
