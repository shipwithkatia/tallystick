"""A note about a reply that may be the model's own text is never silent: it
appears in the report, on stderr under --quiet, and in --json, and it never
moves the exit code.

Each test takes the input of an older test that asked for exit 1 and asserts
what that test was written for - the model's own note does not pass without a
word - in every place a person or a CI job reads. Each also asserts the exit
code is unchanged, so a return of the gate is caught too. The older tests were
deleted in 3d8df63:

    a file of 12 echo tests, deleted whole       -> the first four tests below
    test_a_structured_echo_record_without_its_text_still_blocks,
    then in test_mutation_coverage.py            -> test_a_structured_record_alone_is_noted_everywhere

Measured: all pass on 64ff136 and fail on 0fa3383 (a version with no note)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]

# The inputs of the 12 echo tests deleted in 3d8df63, verbatim.
RU = ("Население Канберры в 2021 году составляло 431 тысячу человек, а город "
      "основан в 1913 году как компромисс между Сиднеем и Мельбурном.")
PL = ("Ludność Canberry w 2021 roku wynosiła 431 tysięcy, a miasto założono w "
      "1913 roku jako kompromis między Sydney a Melbourne.")
VI = ("Dân số Canberra năm 2021 là 431 nghìn người, và thành phố được thành lập "
      "năm 1913 như một sự thỏa hiệp giữa Sydney và Melbourne.")
ZH = "堪培拉2021年人口为43.1万，该城市于1913年作为悉尼和墨尔本之间的折衷方案而建立。"
JA = "キャンベラの人口は2021年に43万1千人で、1913年にシドニーとメルボルンの妥協案として建設された"


def _cross_turn(note, reply):
    return [
        {"role": "user", "content": "Tell me about Canberra."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "save_note",
                "arguments": json.dumps({"text": note}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "saved"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c2", "type": "function", "function": {
                "name": "read_note", "arguments": json.dumps({"id": 1})}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": reply},
        {"role": "assistant", "content": note},
    ]


def _same_turn(note, reply):
    return [
        {"role": "user", "content": "Tell me about Canberra."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "save_note",
                "arguments": json.dumps({"text": note}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": reply},
        {"role": "assistant", "content": note},
    ]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _assert_noted_everywhere(command, path, result, tmp_path):
    """The case is visible in the report, on stderr under --quiet and in --json,
    and the exit code is the same in all three."""
    loud, out, _ = _run([command, str(path)])
    quiet, qout, qerr = _run([command, str(path), "--quiet"])
    report = tmp_path / "report.json"
    as_json, _, _ = _run([command, str(path), "--quiet", "--json", str(report)])
    block = json.loads(report.read_text(encoding="utf-8"))
    assert "NOTE - " in out and f"{result} (" in out.split("NOTE - ", 1)[1], out[-800:]
    assert qout == "" and "may be the model's own text" in qerr and f"{result} (" in qerr, qerr
    noted = block.get("may_be_model_text") or {}
    assert result in [r["result"] for r in noted.get("results", [])], noted
    assert noted.get("exit_code_effect") == "none"
    assert "echo" not in json.dumps(block["gate"])
    assert loud == quiet == as_json == block["gate"]["exit_code"]
    return loud


def _check(chat, result, tmp_path):
    trace = to_trace(chat)
    echo = next(a for a in trace["artifacts"] if a["artifact_id"] == "t" + result[5:-1])
    assert echo["kind"] == "tool_result", "the note stays evidence - a note, not a demotion"
    path = tmp_path / "log.json"
    path.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    _assert_noted_everywhere("check-trace", path, result, tmp_path)


# replaces test_note_read_back_as_python_json_in_a_later_turn[ru, pl, vi, zh]
@pytest.mark.parametrize("note", [RU, PL, VI, ZH], ids=["ru", "pl", "vi", "zh"])
def test_note_read_back_as_python_json_in_a_later_turn_is_noted(note, tmp_path):
    _check(_cross_turn(note, json.dumps({"id": 1, "text": note})), "tool[4]", tmp_path)


# replaces test_note_saved_and_returned_as_python_json_in_the_same_call[ru, zh]
@pytest.mark.parametrize("note", [RU, ZH], ids=["ru", "zh"])
def test_note_saved_and_returned_as_python_json_in_the_same_call_is_noted(note, tmp_path):
    _check(_same_turn(note, json.dumps({"id": 1, "text": note})), "tool[2]", tmp_path)


# replaces test_chinese_note_read_back_in_a_later_turn[pretty_json_utf8, fullwidth_label],
# test_japanese_note_read_back_in_a_later_turn[fullwidth_label, corner_quotes]
# and test_chinese_note_same_call_with_label
@pytest.mark.parametrize("chat, result", [
    (_cross_turn(ZH, json.dumps({"id": 1, "text": ZH}, ensure_ascii=False, indent=2)), "tool[4]"),
    (_cross_turn(ZH, "笔记：" + ZH), "tool[4]"),
    (_cross_turn(JA, "メモ：" + JA), "tool[4]"),
    (_cross_turn(JA, "「" + JA + "」"), "tool[4]"),
    (_same_turn(ZH, "已保存：" + ZH), "tool[2]"),
], ids=["zh_pretty_json_utf8", "zh_fullwidth_label", "ja_fullwidth_label", "ja_corner_quotes",
        "zh_same_call_with_label"])
def test_a_cjk_note_read_back_is_noted(chat, result, tmp_path):
    _check(chat, result, tmp_path)


# replaces test_the_laundered_chinese_note_audits_clean_end_to_end
def test_the_laundered_chinese_note_is_noted_by_audit_end_to_end(tmp_path):
    """The posted trace balances on the note (exit 0: a note moves no exit
    code), and `audit` says, in all three places, that its root may be the
    model's text."""
    chat = _cross_turn(ZH, json.dumps({"id": 1, "text": ZH}, ensure_ascii=False, indent=2))
    trace = to_trace(chat)
    arts = {a["artifact_id"]: a for a in trace["artifacts"]}
    final = next(a for a in trace["artifacts"] if a["kind"] == "final_answer")
    s = arts["t4"]["content"].index(ZH)
    trace["claims"] = [{"claim_id": "c1", "artifact_id": final["artifact_id"],
                        "start": 0, "end": len(ZH)}]
    trace["entries"] = [{"entry_id": "e1", "claim_id": "c1",
                         "account": f"EVIDENCE:t4#{s}-{s + len(ZH)}", "quoted_span": ZH}]
    path = tmp_path / "posted.json"
    path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    assert _assert_noted_everywhere("audit", path, "tool[4]", tmp_path) == 0


# replaces test_mutation_coverage.py::test_a_structured_echo_record_without_its_text_still_blocks
def test_a_structured_record_alone_is_noted_everywhere(tmp_path):
    """G03: a trace carrying only `echo_warning_details`, without the text list
    the reader writes beside it, must not lose the record."""
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"echo_warning_details": [{"result": "t1", "tool": "read_note", "line": "x"}]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    assert _assert_noted_everywhere("audit", path, "t1", tmp_path) == 0
