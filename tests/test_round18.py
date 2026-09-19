"""Round 18: the owner's three decisions and the Thai finding.

1. Echo warnings are gone; the demotion stays. Nothing about an echo waits for
   a confirmation, `--accept-echo-warning` no longer exists, and a trace an
   earlier reader wrote warnings into says so in a note without moving the exit.
2. Not shipped: both rules built to close the two silent holes were stopped at
   the hand-read sample (see tallystick/adapters/openai_chat.py). The tests
   here pin that both holes are still open, so nobody reads them as closed.
3. `coverage` is the share of the WHOLE answer under claims that close, the
   share under no claim is printed without a flag, and more than half of the
   answer under no claim is exit 1 (`answer_mostly_unclaimed`).
4. A long Thai chat with no echo in it no longer comes back `unchecked`.

Every test runs on data built here; none needs the corpus.
"""

from __future__ import annotations

import io
import json
import random
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick import load_run, close_books
from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
ANSWER = "Sydney is the capital of Australia."


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _kinds(trace):
    return {a["artifact_id"]: a["kind"] for a in trace["artifacts"]}


# --- decision 2: not shipped, and pinned so it cannot change unseen ------------

def test_a_draft_pasted_back_as_a_user_message_is_still_a_root():
    # Both rules built for decision 2 were stopped at the hand-read sample
    # (tallystick/adapters/openai_chat.py). The hole stays open; README says so.
    log = [{"role": "user", "content": "What is the capital of Australia?"},
           {"role": "assistant", "content": "Draft: " + ANSWER},
           {"role": "user", "content": "Here is your previous draft:\n" + ANSWER},
           {"role": "assistant", "content": ANSWER}]
    trace = to_trace(log)
    assert _kinds(trace)["m2"] == "document"
    assert "root_messages_repeating_the_model" not in trace["_meta"]


# --- decision 1: no warnings, no confirmation -------------------------------

def test_the_confirmation_flag_is_gone(tmp_path):
    log = _write(tmp_path, "log.json", [{"role": "user", "content": "hi"},
                                        {"role": "assistant", "content": "hello"}])
    with pytest.raises(SystemExit) as stopped:
        _run(["check-trace", log, "--accept-echo-warning", "read_note"])
    assert stopped.value.code == 2


def test_a_note_read_back_in_a_later_turn_exits_0_and_is_evidence(tmp_path):
    # The price of decision 1, pinned so it cannot change unseen: this used to
    # exit 1 with unreviewed_echo_warnings. README states that it passes. Round
    # 19 lists it again as a note (tests/test_round19.py); the exit stays 0.
    log = [{"role": "user", "content": "What is the capital of Australia?"},
           {"role": "assistant", "content": None, "tool_calls": [
               {"id": "c1", "type": "function", "function": {
                   "name": "save_note", "arguments": json.dumps({"text": ANSWER})}}]},
           {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "saved"},
           {"role": "assistant", "content": None, "tool_calls": [
               {"id": "c2", "type": "function", "function": {"name": "read_note", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": ANSWER},
           {"role": "assistant", "content": ANSWER}]
    trace = to_trace(log)
    assert _kinds(trace)["t4"] == "tool_result"
    assert trace["_meta"]["echo_warning_details"]
    assert _run(["check-trace", _write(tmp_path, "log.json", log), "--quiet"])[0] == 0


def _legacy_posted(tmp_path):
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"echo_warning_details": [{"result": "tool[4]", "tool": "read_note",
                                                "line": "x"}],
                      "echoes_from_earlier_turns": ["tool[4] (read_note): x"]}
    return _write(tmp_path, "legacy.json", trace)


def test_a_trace_with_old_warnings_says_so_and_keeps_its_exit(tmp_path):
    path = _legacy_posted(tmp_path)
    code, out, _err = _run(["audit", path])
    assert code == 0
    assert "NOTE - 1 tool result(s) may be the model's own text" in " ".join(out.split())
    code, out, err = _run(["audit", path, "--quiet"])
    assert (code, out) == (0, "") and "1 tool result(s) may be the model's own text" in err


# --- decision 3: coverage of the whole answer --------------------------------

def _posted(answer, spans, *, fund=True):
    """One document holding the whole answer; a claim per span, each credited
    to the same text in the document when `fund`."""
    claims, entries = [], []
    for i, (a, b) in enumerate(spans):
        claims.append({"claim_id": f"c{i}", "artifact_id": "ans", "start": a, "end": b})
        if fund:
            entries.append({"entry_id": f"e{i}", "claim_id": f"c{i}",
                            "account": f"EVIDENCE:doc#{a}-{b}", "quoted_span": answer[a:b]})
    return {"artifacts": [{"artifact_id": "doc", "kind": "document", "content": answer},
                          {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
            "steps": [{"step_id": "s", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
            "claims": claims, "entries": entries}


def test_an_answer_checked_by_one_letter_is_exit_1_and_says_how_little(tmp_path):
    answer = "Sydney is the capital of Australia. Revenue grew 900%."
    path = _write(tmp_path, "t.json", _posted(answer, [(0, 1)]))
    code, out, _err = _run(["audit", path])
    assert code == 1
    assert "coverage         2.3% of the answer (1 of 43 letters and digits" in out
    assert "under no claim   97.7% of the answer - not checked" in out
    assert "claims closed    1 of 1" in out
    assert "answer_mostly_unclaimed" in out
    assert "BOOKS BALANCE ON 2% OF THE ANSWER" in out
    code, out, err = _run(["audit", path, "--quiet", "--json", str(tmp_path / "r.json")])
    assert (code, out) == (1, "") and "answer_mostly_unclaimed" in err
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["coverage"] == 1 / 43 and report["claims_closed"] == 1.0
    assert report["gate"]["reasons"] == ["answer_mostly_unclaimed"]


def test_the_line_is_more_than_half_counted_in_letters_and_digits(tmp_path):
    # "abcd efgh": 8 letters. A claim on 4 leaves exactly half unclaimed: exit 0.
    # A claim on 3 leaves 5 of 8: exit 1. Spaces and punctuation count for nothing.
    answer = "abcd, efgh!"
    assert _run(["audit", _write(tmp_path, "half.json", _posted(answer, [(0, 5)])), "--quiet"])[0] == 0
    assert _run(["audit", _write(tmp_path, "less.json", _posted(answer, [(0, 3)])), "--quiet"])[0] == 1


def test_the_whole_answer_claimed_and_closed_is_full_coverage_and_exit_0(tmp_path):
    answer = "Canberra is the capital."
    path = _write(tmp_path, "t.json", _posted(answer, [(0, 12), (9, len(answer))]))
    code, out, _err = _run(["audit", path, "--json", str(tmp_path / "r.json")])
    assert code == 0 and "coverage         100.0% of the answer" in out
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["coverage"] == 1.0 and report["claims_closed"] == 1.0
    assert report["answer"] == {"letters_and_digits": 20, "under_a_claim": 20,
                                "under_claims_that_close": 20, "under_no_claim_share": 0.0}


def test_coverage_counts_only_claims_that_close(tmp_path):
    answer = "Canberra is the capital. Revenue grew."
    trace = _posted(answer, [(0, 24), (25, len(answer))])
    trace["entries"] = trace["entries"][:1]          # the second claim has no credit
    from tallystick.report import answer_cover        # here, so the file loads on f84f278
    run = load_run(trace)
    cover = answer_cover(run, close_books(run))
    assert (cover.total, cover.claimed, cover.closed) == (31, 31, 20)
    assert cover.coverage == 20 / 31 and cover.unclaimed == 0.0
    assert not cover.mostly_unclaimed                 # every letter is under a claim
    assert _run(["audit", _write(tmp_path, "t.json", trace), "--quiet"])[0] == 1


# --- 4: a long chat in a script without spaces -------------------------------

@pytest.mark.parametrize("script", ["th", "bo", "zh"])
def test_a_long_chat_with_no_echo_is_read_to_the_end_with_nothing_demoted(tmp_path, script):
    """150 turns; the model writes 1,000 characters into each call, the tool
    answers 2,000 others. On f84f278 the Thai chat came back with 13 replies
    `unchecked`, exit 1."""
    blocks = {"th": (0x0E01, 0x0E2F, ["", "ั", "ิ", "ี", "ุ"], ""),
              "bo": (0x0F40, 0x0F6A, ["", "ི", "ུ", "ེ", "ོ"], "་"),
              "zh": (0x4E00, 0x4F00, [""], "")}
    lo, hi, vowels, sep = blocks[script]
    cons = [chr(c) for c in range(lo, hi)]

    def text(rng, n, limit):
        return sep.join(rng.choice(cons) + rng.choice(vowels) + (rng.choice(cons) if rng.random() < .4 else "")
                        for _ in range(n))[:limit]

    model, tool = random.Random(17), random.Random(1017)
    chat = [{"role": "user", "content": text(random.Random(1), 8, 100)}]
    for i in range(150):
        chat.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {
                "name": "web_search",
                "arguments": json.dumps({"query": text(model, 400, 1000)}, ensure_ascii=False)}}]})
        chat.append({"role": "tool", "tool_call_id": f"c{i}", "name": "web_search",
                     "content": text(tool, 800, 2000)})
    chat.append({"role": "assistant", "content": text(random.Random(2), 4, 100)})
    start = time.perf_counter()
    trace = to_trace(chat)
    assert time.perf_counter() - start < 10
    assert all(a["kind"] == "tool_result" for a in trace["artifacts"] if a["artifact_id"].startswith("t"))
    assert _run(["check-trace", _write(tmp_path, "chat.json", chat), "--quiet"])[0] == 0
