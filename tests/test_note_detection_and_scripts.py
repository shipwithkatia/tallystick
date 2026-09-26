"""The echo detection is a note, not an accusation.

Without it, the demotion alone caught none of the six real echoes in a
hand-read sample of 20. With it as a gate - exit 1 and a confirmation by name -
it accused on a signal that was right 6 times in 20. So the detection
(adapters/openai_chat.py as of f84f278) is listed under the report and in
`--json`, and it never moves an exit code. No `--accept-echo-warning`, no
`UnreviewedEchoWarnings`, no `unreviewed_echo_warnings`.

And the scripts written without spaces: Tibetan, Thai, Lao, Khmer and Myanmar
are cut only at marks from outside their block. Cut at their vowel signs, a
long honest chat in them filled the report with false `unchecked` notes.
"""
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

import tallystick
from tallystick.adapters import openai_chat as oc
from tallystick.cli import main

ROOT = Path(__file__).resolve().parent.parent

NOTE = ("Sydney is the capital of Australia and it was chosen in 1901 because the "
        "federation needed a neutral site between the two.")


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _note_log(note=NOTE, readback=NOTE, final=None):
    return [{"role": "user", "content": "What is the capital of Australia?"},
            {"role": "assistant", "content": "I will note it.",
             "tool_calls": [_call("save_note", {"text": note}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": "Reading it back.",
             "tool_calls": [_call("read_note", {}, "c2")]},
            {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": readback},
            {"role": "assistant", "content": final if final is not None else note}]


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _run(argv, capsys):
    capsys.readouterr()
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _flat(text):
    return " ".join(text.split())


# --- the note is shown -------------------------------------------------------

def test_a_note_read_back_later_is_listed_under_the_report_and_exits_0(tmp_path, capsys):
    path = _write(tmp_path, "log.json", _note_log())
    code, out, _err = _run(["check-trace", path], capsys)
    assert code == 0
    said = _flat(out)
    assert "NOTE - 1 tool result(s) may be the model's own text" in said
    assert "the exit code does not change" in said
    assert "tool[4] (read_note) call c2" in said
    # the price, as a number, where the person reads the note
    assert "sample of 20" in said and "6 were the model's own text" in said
    assert "--tool-returns-model-text NAME" in said


def test_quiet_keeps_stdout_empty_and_says_the_note_on_stderr(tmp_path, capsys):
    path = _write(tmp_path, "log.json", _note_log())
    code, out, err = _run(["check-trace", path, "--quiet"], capsys)
    assert (code, out) == (0, "")
    said = _flat(err)
    assert "note, exit code unchanged: 1 tool result(s) may be the model's own text" in said
    assert "6 were the model's own text" in said and "tool[4] (read_note)" in said


def test_json_lists_the_note_beside_the_gate_not_in_it(tmp_path, capsys):
    path = _write(tmp_path, "log.json", _note_log())
    out = tmp_path / "out.json"
    code, _o, _e = _run(["check-trace", path, "--quiet", "--json", str(out)], capsys)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["gate"] == {"exit_code": 0, "reasons": []}
    block = payload["may_be_model_text"]
    assert block["count"] == 1 and block["exit_code_effect"] == "none"
    assert "6 were the model's own text" in block["worth"]
    (note,) = block["results"]
    assert (note["result"], note["tool"], note["kind"], note["call_id"], note["message"]) == \
        ("tool[4]", "read_note", "earlier_turn", "c2", 4)


def test_a_log_with_no_note_has_an_empty_block_and_no_note_line(tmp_path, capsys):
    path = _write(tmp_path, "log.json", _note_log(readback="Canberra, since 1913.",
                                                  final="Canberra."))
    out = tmp_path / "out.json"
    code, said, _e = _run(["check-trace", path, "--json", str(out)], capsys)
    assert "NOTE -" not in said
    block = json.loads(out.read_text(encoding="utf-8"))["may_be_model_text"]
    assert (block["count"], block["results"]) == (0, [])


def test_more_than_ten_notes_print_ten_and_keep_every_one_in_json(tmp_path, capsys):
    log = [{"role": "user", "content": "q"}]
    for i in range(12):
        note = f"{NOTE} Entry number {i} of the notebook."
        log += [{"role": "assistant", "content": None,
                 "tool_calls": [_call("save_note", {"text": note}, f"s{i}")]},
                {"role": "tool", "tool_call_id": f"s{i}", "name": "save_note", "content": "ok"},
                {"role": "assistant", "content": None,
                 "tool_calls": [_call("read_note", {"n": i}, f"r{i}")]},
                {"role": "tool", "tool_call_id": f"r{i}", "name": "read_note", "content": note}]
    log.append({"role": "assistant", "content": "done"})
    path = _write(tmp_path, "log.json", log)
    out = tmp_path / "out.json"
    code, said, _e = _run(["check-trace", path, "--json", str(out)], capsys)
    listed = [ln for ln in said.splitlines() if ln.startswith("    tool[")]
    assert len(listed) == 10 and "(+2 more, all of them in --json)" in said
    assert json.loads(out.read_text(encoding="utf-8"))["may_be_model_text"]["count"] == 12


# --- the note decides nothing --------------------------------------------------

def _with_records(trace):
    trace = dict(trace)
    trace["_meta"] = {"echo_warning_details": [{"result": "tool[4]", "tool": "read_note",
                                                "line": "a line", "kind": "earlier_turn"}],
                      "echoes_from_earlier_turns": ["tool[4] (read_note), a line: a line"]}
    return trace


@pytest.mark.parametrize("example, expected", [("balanced_run", 0), ("laundered_summary", 1)])
def test_audit_prints_the_note_and_its_exit_and_verdict_do_not_move(tmp_path, capsys, example,
                                                                    expected):
    trace = json.loads((ROOT / "examples" / f"{example}.json").read_text(encoding="utf-8"))
    plain = _write(tmp_path, "plain.json", trace)
    noted = _write(tmp_path, "noted.json", _with_records(trace))
    code_plain, out_plain, _ = _run(["audit", plain], capsys)
    code_noted, out_noted, _ = _run(["audit", noted], capsys)
    assert code_plain == code_noted == expected
    assert "NOTE - 1 tool result(s)" in out_noted and "NOTE -" not in out_plain
    assert out_noted.startswith(out_plain.rstrip("\n"))   # the report above is the same
    j_plain, j_noted = tmp_path / "p.json", tmp_path / "n.json"
    _run(["audit", plain, "--quiet", "--json", str(j_plain)], capsys)
    _run(["audit", noted, "--quiet", "--json", str(j_noted)], capsys)
    a = json.loads(j_plain.read_text(encoding="utf-8"))
    b = json.loads(j_noted.read_text(encoding="utf-8"))
    assert b.pop("may_be_model_text")["count"] == 1
    assert a.pop("may_be_model_text")["count"] == 0
    assert a == b


def test_the_python_audit_does_not_raise_on_a_noted_trace(tmp_path):
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    balance = tallystick.audit(_with_records(trace))
    assert balance.books_balance is True
    assert not hasattr(tallystick, "UnreviewedEchoWarnings")


@pytest.mark.parametrize("command", ["check-trace", "audit"])
def test_there_is_still_no_flag_to_confirm_a_note(tmp_path, command):
    path = _write(tmp_path, "log.json", _note_log())
    with pytest.raises(SystemExit) as exc:
        main([command, path, "--accept-echo-warning", "read_note"])
    assert exc.value.code == 2


def test_a_half_emoji_in_a_noted_line_exits_the_same_loud_and_quiet(tmp_path):
    """A note's line goes to the terminal, and a lone surrogate there made
    print() raise and the run exit 1 on books that balance. The same case in
    test_core_and_coverage_edges.py goes through the chain view."""
    note = "The bridge was opened to traffic in 1932 by the governor \ud83d"
    path = tmp_path / "log.json"
    path.write_text(json.dumps(_note_log(note, note)), encoding="utf-8")
    runs = [subprocess.run([sys.executable, "-m", "tallystick.cli", "check-trace", *flags,
                            str(path)], capture_output=True, text=True, cwd=ROOT, timeout=120)
            for flags in ([], ["--quiet"])]
    assert [r.returncode for r in runs] == [0, 0], runs[0].stderr[-400:]
    assert "NOTE - 1 tool result(s)" in runs[0].stdout
    assert "Traceback" not in runs[0].stderr + runs[1].stderr


def test_readme_names_the_price_of_a_note_and_that_it_moves_no_exit_code():
    text = " ".join((ROOT / "docs" / "known-limitations.md").read_text(encoding="utf-8").split())
    assert "**6 of 20 were the model's own text**" in text
    assert "**A note never changes the exit code**" in text
    assert "`python bench/echo_notes.py <AgentHallu>`" in text


def test_readme_names_the_owners_decision_on_the_half_line():
    text = " ".join((ROOT / "docs" / "known-limitations.md").read_text(encoding="utf-8").split())
    assert "owner's decision of 16 September 2026" in text
    assert "it moved 130 from exit 0 to exit 1" in text
    assert "Moving the line to two thirds after seeing the number was considered and refused" in text


def test_the_notes_script_without_the_corpus_says_so(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "bench" / "echo_notes.py"), str(tmp_path)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert "git clone https://github.com/liuxuannan/AgentHallu" in result.stderr


# --- Thai, Lao, Khmer, Myanmar: cut like Tibetan ------------------------------

SCRIPTS = {
    "th": ([chr(c) for c in range(0x0E01, 0x0E2F)], ["", "ั", "ิ", "ี", "ุ"]),
    "lo": ([chr(c) for c in (0x0E81, 0x0E82, 0x0E84, 0x0E87, 0x0E88, 0x0E8A, 0x0E8D, 0x0E94,
                             0x0E95, 0x0E96, 0x0E97, 0x0E99, 0x0E9A, 0x0E9B, 0x0E9C, 0x0E9D)],
           ["", "ິ", "ີ", "ຸ", "ົ"]),
    "km": ([chr(c) for c in range(0x1780, 0x17A3)], ["", "ិ", "ី", "ុ", "ំ"]),
    "my": ([chr(c) for c in range(0x1000, 0x1021)], ["", "ိ", "ီ", "ု", "ံ"]),
}


def _syllables(script, rng, n, limit=None):
    cons, vowels = SCRIPTS[script]
    text = "".join(rng.choice(cons) + rng.choice(vowels)
                   + (rng.choice(cons) if rng.random() < .4 else "") for _ in range(n))
    return text[:limit] if limit else text


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_a_long_chat_with_no_echo_gets_no_note(script):
    """200 turns: the model writes 1,000 characters into each call, the tool
    answers with 2,000 others, no reply is a note read back. Cut at the vowel
    signs, the exact pass ran out of work: 63 (Thai), 79 (Khmer), 82 (Myanmar)
    false `unchecked` notes on this chat, and more on Lao."""
    model, tool = random.Random(17), random.Random(1017)
    chat = [{"role": "user", "content": _syllables(script, random.Random(1), 8)}]
    for i in range(200):
        chat.append({"role": "assistant", "content": None, "tool_calls": [
            _call("web_search", {"query": _syllables(script, model, 400, 1000)}, f"c{i}")]})
        chat.append({"role": "tool", "tool_call_id": f"c{i}", "name": "web_search",
                     "content": _syllables(script, tool, 800, 2000)})
    chat.append({"role": "assistant", "content": _syllables(script, random.Random(2), 4)})
    assert [w["kind"] for w in oc.to_trace(chat)["_meta"]["echo_warning_details"]] == []


@pytest.mark.parametrize("script", list(SCRIPTS))
@pytest.mark.parametrize("shape", ["bare", "label_quotes", "pretty_json", "json_ascii", "clause"])
def test_a_note_in_these_scripts_is_still_noted(script, shape):
    note = _syllables(script, random.Random(5), 30)
    other = _syllables(script, random.Random(6), 30)
    reply = {"bare": note, "label_quotes": f'Note: "{note}"',
             "pretty_json": json.dumps({"id": 1, "text": note}, ensure_ascii=False, indent=2),
             "json_ascii": json.dumps({"id": 1, "text": note}),
             "clause": f"{note} {other}"}[shape]
    details = oc.to_trace(_note_log(note, reply, final="done"))["_meta"]["echo_warning_details"]
    assert [(w["result"], w["kind"]) for w in details] == [("tool[4]", "earlier_turn")]


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_the_price_a_note_glued_to_a_word_of_its_script_is_not_seen(script):
    """What the fix costs, pinned: a same-script word written straight onto the
    note, with no mark between, makes one word that matches nothing - as it
    already did in Tibetan and Chinese. Cut at vowel signs, this was noted."""
    note = _syllables(script, random.Random(5), 30)
    glued = _syllables(script, random.Random(7), 3) + note
    details = oc.to_trace(_note_log(note, glued, final="done"))["_meta"]["echo_warning_details"]
    assert details == []


def test_a_record_with_no_text_line_is_still_a_note(tmp_path, capsys):
    """A trace carrying only the structured records, without the text list the
    reader writes beside them, is still said to carry them."""
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"echo_warning_details": [{"result": "t1", "tool": "read_note", "line": "x"}]}
    path = _write(tmp_path, "t.json", trace)
    code, out, _err = _run(["audit", path], capsys)
    assert code == 0 and "NOTE - 1 tool result(s)" in out and "t1 (read_note): x" in out
