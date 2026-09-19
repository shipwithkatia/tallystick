"""proverka20: one failing test per finding on 64ff136 - the note, the reader,
the exit codes and the coverage line. The external review that found them gave
the command and the numbers for each."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main
from tallystick.ledger import MAX_DEPTH

ROOT = Path(__file__).resolve().parents[1]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _cli(argv, cwd):
    """A real process, as a terminal or a CI job runs the command: an exception
    that escapes is exit 1 there, not a test error."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "tallystick.cli", *argv], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


RU = ("Население Канберры в 2021 году составляло 431 тысячу человек, а город "
      "основан в 1913 году как компромисс между Сиднеем и Мельбурном.")


def _cross_turn(note, reply):
    """save_note in one turn, read_note in the next hands the note back."""
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


# --------------------------------------------------------------------------- #
# 2.2 The note
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(strict=True, reason=(
    "not done: `convert` does not list notes. The trace it writes keeps them in "
    "`_meta`, and `check-trace` on that trace prints them - README.md, Known "
    "limitations, What the commands say, and where. Documented, not fixed, by the "
    "owner's decision of 16 September 2026 after review 20; round 22 marked it"))
def test_convert_still_says_a_result_may_be_the_models_own_text(tmp_path):
    """Round 17 (f84f278) `convert` listed such a result under the reading:
    "left out - result(s) kept as evidence that may hand back the model's own
    text: tool[4] (read_note) call c2, ...". Round 18 took that line out of
    `_reading_notes` with the warnings, and round 19 brought the note back to
    `check-trace` and `audit` only. `convert` - the README's "keep the reading"
    command - now says nothing about it, on the same log where round 17 did.
    The detection is the same; the command that writes the trace went quiet."""
    log = tmp_path / "log.json"
    log.write_text(json.dumps(_cross_turn(RU, RU), ensure_ascii=False), encoding="utf-8")
    code, out, _err = _run(["convert", str(log), "-o", str(tmp_path / "t.json")])
    assert code == 0
    trace = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert trace["_meta"]["echo_warning_details"], "control: the reading recorded the note"
    assert "tool[4]" in out and "model's own text" in out, out


def test_a_note_holding_half_an_emoji_does_not_move_the_exit_code_under_json(tmp_path):
    """"A note never changes the exit code" (README). A noted tool result that
    holds a lone surrogate (half an emoji cut in a UTF-16 log) is copied into
    `may_be_model_text` - and into `reading` - so `--json` cannot be written:
    exit 2. The README's CI line is exactly `check-trace ... --json report.json
    --quiet`. The same log with the half-emoji in a result that is not noted
    exits 0 with `--json`; round 18 (0fa3383), which had no note, exits 0 on
    this one too. The terminal path was fixed in round 19; the file path was not."""
    note = "Sydney is the capital of Australia \ud83d and it was chosen in 1901 as a compromise."
    log = [{"role": "user", "content": "What is the capital of Australia?"},
           {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
               "function": {"name": "save_note", "arguments": json.dumps({"text": note})}}]},
           {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
           {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function",
               "function": {"name": "read_note", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": "Note: " + note},
           {"role": "assistant", "content": "Canberra."}]
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    quiet, _out, err = _run(["check-trace", str(path), "--quiet"])
    assert quiet == 0 and "may be the model's own text" in err, "control: noted, exit 0"
    with_json, _out, err = _run(["check-trace", str(path), "--quiet",
                                 "--json", str(tmp_path / "report.json")])
    assert with_json == quiet, f"the note moved the exit code {quiet} -> {with_json}: {err}"

    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"echo_warning_details": [
        {"result": "t1", "tool": "read_note", "line": "half \ud83d emoji"}]}
    posted = tmp_path / "posted.json"
    posted.write_text(json.dumps(trace), encoding="utf-8")
    plain, _o, _e = _run(["audit", str(posted), "--quiet"])
    as_json, _o, err = _run(["audit", str(posted), "--quiet", "--json", str(tmp_path / "a.json")])
    assert plain == 0 and as_json == plain, f"audit: {plain} -> {as_json}: {err}"


# --------------------------------------------------------------------------- #
# 2.1 The model's own text as a root, through the reader
# --------------------------------------------------------------------------- #


ANSWER = "The capital of Australia is Sydney, chosen in 1901."


def _reused_id_log(first_id, second_id):
    """Two turns; the second calls final_answer with the answer, and the tool
    hands exactly that back. The ids are per turn - `call_0` both times - as
    agents that number their calls turn by turn write them."""
    return [
        {"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": first_id, "type": "function",
            "function": {"name": "web_search",
                         "arguments": json.dumps({"query": "capital of Australia"})}}]},
        {"role": "tool", "tool_call_id": first_id,
         "content": "Canberra is the capital city of Australia."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": second_id, "type": "function",
            "function": {"name": "final_answer", "arguments": json.dumps({"answer": ANSWER})}}]},
        {"role": "tool", "tool_call_id": second_id, "content": ANSWER},
        {"role": "assistant", "content": ANSWER},
    ]


def test_a_value_handed_back_by_its_own_call_is_demoted_when_ids_repeat_across_turns(tmp_path):
    """README: the reader "never undoes a demotion: where the reply IS a value
    the answering call carried - the whole reply ... - it stays the model's own
    text". With ids unique per log, `final_answer(ANSWER)` answering ANSWER is
    demoted and the posted answer does not balance (exit 1). With the same
    call id in both turns - declared once per turn, open and unambiguous in its
    own turn - the reader marks the id "reused", files the result as "matched
    nothing open", keeps it a `tool_result` root, and lists only an `unmatched`
    note. Posted on it, `audit` prints BOOKS BALANCE and exits 0. Round 17
    exited 1 on the same trace (the warning gate); rounds 18 and 19 exit 0."""
    unique = to_trace(_reused_id_log("call_a", "call_b"))
    assert next(a["kind"] for a in unique["artifacts"] if a["artifact_id"] == "t4") == "intermediate"
    raw = to_trace(_reused_id_log("call_0", "call_0"))
    t4 = next(a for a in raw["artifacts"] if a["artifact_id"] == "t4")
    final = next(a for a in raw["artifacts"] if a["kind"] == "final_answer")
    raw["claims"] = [{"claim_id": "c1", "artifact_id": final["artifact_id"],
                      "start": 0, "end": len(ANSWER)}]
    raw["entries"] = [{"entry_id": "e1", "claim_id": "c1",
                       "account": f"EVIDENCE:t4#0-{len(ANSWER)}", "quoted_span": ANSWER}]
    path = tmp_path / "posted.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    code, _out, _err = _run(["audit", str(path), "--quiet"])
    assert t4["kind"] == "intermediate" and code != 0, (
        f"the answer's own value, handed back by its own call, is a {t4['kind']} root; "
        f"audit exit {code}")


def test_a_tool_handing_back_the_models_earlier_message_is_not_silent_evidence(tmp_path):
    """The note weighs a reply against the text the model wrote INTO CALLS -
    arguments only (`batch_text.add_call`, `earlier_text`). The model's own
    messages are never indexed. A memory or history tool that returns what the
    assistant said a turn ago, word for word, is a `tool_result` root with no
    demotion and no note; the answer quoting it balances: `audit` prints BOOKS
    BALANCE, exit 0, and not one line about it. `check-trace` names the three
    artifacts only as generic `duplicate_content` ("does not decide the
    verdict"). README's "What still passes without a word" lists a store
    record, padding, glued scripts and case, and a `user` message - not this.
    Same on f84f278."""
    draft = ("Sydney is the capital of Australia; it was chosen in 1901 as a compromise "
             "between the two largest cities.")
    chat = [
        {"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": draft, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "store_memory", "arguments": json.dumps({"key": "draft"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "stored"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2", "type": "function",
            "function": {"name": "recall", "arguments": json.dumps({"key": "draft"})}}]},
        {"role": "tool", "tool_call_id": "c2", "content": draft},
        {"role": "assistant", "content": draft},
    ]
    raw = to_trace(chat)
    t4 = next(a for a in raw["artifacts"] if a["artifact_id"] == "t4")
    final = next(a for a in raw["artifacts"] if a["kind"] == "final_answer")
    raw["claims"] = [{"claim_id": "c1", "artifact_id": final["artifact_id"],
                      "start": 0, "end": len(draft)}]
    raw["entries"] = [{"entry_id": "e1", "claim_id": "c1",
                       "account": f"EVIDENCE:t4#0-{len(draft)}", "quoted_span": draft}]
    path = tmp_path / "posted.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    code, out, err = _run(["audit", str(path)])
    assert code == 0 and "BOOKS BALANCE" in out, "control: it balances on the tool result"
    assert t4["kind"] != "tool_result" or "NOTE - " in out, (
        "the model's own earlier message, handed back by a tool, balances the answer "
        "with no demotion and no note")


# --------------------------------------------------------------------------- #
# 2.3 / 2.5 Exit codes
# --------------------------------------------------------------------------- #


def _chain(n, tail=""):
    """doc -> i1 -> ... -> i_n -> ans, every hop verbatim; `tail` is appended to
    the answer and stands under no claim."""
    text = "The filing reports revenue of 14 million."
    arts = [{"artifact_id": "doc", "kind": "document", "content": text}]
    steps, claims, entries = [], [], []
    prev = "doc"
    for k in range(1, n + 2):
        aid = f"i{k}" if k <= n else "ans"
        arts.append({"artifact_id": aid, "kind": "intermediate" if k <= n else "final_answer",
                     "content": text if k <= n else text + tail})
        steps.append({"step_id": f"s{k}", "kind": "summarize", "inputs": [prev], "outputs": [aid]})
        claims.append({"claim_id": f"c_{aid}", "artifact_id": aid, "start": 0, "end": len(text)})
        entries.append({"entry_id": f"e_{aid}", "claim_id": f"c_{aid}",
                        "account": f"EVIDENCE:{prev}#0-{len(text)}", "quoted_span": text})
        prev = aid
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


def test_an_answer_mostly_under_no_claim_is_exit_1_even_beside_a_chain_too_deep(tmp_path):
    """cli.py: exit 2 when the audit "could not finish (a claim's chain deeper
    than the ledger walks, and nothing else wrong)"; README: "A claim that
    really does not close in the same answer still makes it exit 1". Here
    something else is wrong and found: 61.4% of the answer stands under no
    claim (`answer_mostly_unclaimed`, a verdict - exit 1 on its own). Beside a
    chain of 257 hops the command exits 2 and prints "exit 2 -
    answer_mostly_unclaimed": a CI job that reads 2 as "could not run" passes
    an answer the tool itself found mostly unchecked."""
    tail = " And the moon is made of green cheese, as every astronomer confirms."
    shallow = tmp_path / "shallow.json"
    shallow.write_text(json.dumps(_chain(10, tail)), encoding="utf-8")
    assert _run(["audit", str(shallow), "--quiet"])[0] == 1, "control: exit 1 on its own"
    deep = tmp_path / "deep.json"
    deep.write_text(json.dumps(_chain(MAX_DEPTH + 1, tail)), encoding="utf-8")
    code, _out, err = _run(["audit", str(deep), "--quiet", "--json", str(tmp_path / "o.json")])
    reasons = json.loads((tmp_path / "o.json").read_text())["gate"]["reasons"]
    assert "answer_mostly_unclaimed" in reasons
    assert code == 1, f"exit {code}, reasons {reasons}: {err.strip()[-200:]}"


def test_arguments_nested_deeper_than_python_recurses_are_exit_2_not_a_traceback(tmp_path):
    """The seventh place where input the tool cannot read leaves as exit 1. A
    tool call whose `arguments` string is JSON nested 1,500 deep: `json.loads`
    reads it, then `_values` (openai_chat.py) walks it recursively outside any
    `except RecursionError`, from `_call_texts` via `_Words.add_call`.
    `check-trace` and `convert` both end in a RecursionError traceback, exit 1 -
    "the trace is not auditable" for check-trace, and a code `convert` is
    documented never to return. At 900 deep both exit 0. Same on f84f278."""
    args = "[" * 1500 + '"x"' + "]" * 1500
    log = [{"role": "user", "content": "q"},
           {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
               "function": {"name": "run", "arguments": args}}]},
           {"role": "tool", "tool_call_id": "c1", "content": "ok done here"},
           {"role": "assistant", "content": "answer text"}]
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    checked = _cli(["check-trace", str(path), "--quiet"], tmp_path)
    converted = _cli(["convert", str(path), "-o", str(tmp_path / "t.json")], tmp_path)
    assert (checked.returncode, converted.returncode) == (2, 2), (
        checked.returncode, converted.returncode, checked.stderr[-300:])


def test_the_verdict_line_does_not_read_half_and_half_where_more_than_half_is_unclaimed(tmp_path):
    """The rule is "more than half"; exactly half passes. 125 of 251 letters under
    a claim: exit 1, and the reason line says 50.2%. The verdict line - "the
    line people read", report.py - rounds both shares to whole percents and
    prints "BOOKS BALANCE ON 50% OF THE ANSWER - 50% IS UNDER NO CLAIM": the
    one reading of the rule under which this answer should have passed."""
    answer = "a" * 125 + " " + "b" * 126
    trace = {"artifacts": [{"artifact_id": "doc", "kind": "document", "content": answer},
                           {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
             "steps": [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
                       {"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
             "claims": [{"claim_id": "c0", "artifact_id": "ans", "start": 0, "end": 125}],
             "entries": [{"entry_id": "e0", "claim_id": "c0", "account": "EVIDENCE:doc#0-125",
                          "quoted_span": answer[:125]}]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    code, out, _err = _run(["audit", str(path)])
    verdict = next(line for line in out.splitlines() if line.startswith("BOOKS"))
    assert code == 1
    assert "ON 50% OF THE ANSWER - 50% IS" not in verdict, verdict
