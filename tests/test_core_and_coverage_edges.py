"""Edges of the verdict core, the exit codes, the echo rule and the coverage
figure: each test failed on 3aa58a8.

The claim walk must not be exponential on a cycle of claims, an assumption must
not hide under "grounded", and the answer's status must not depend on claim
names. Half an emoji must not turn into exit 1, and an integer too long to read
must raise TraceError. The model's text must not pass unnoted, and a declared
name must not be dropped in silence. An answer checked on one letter must not
report full coverage, and what README and the bench scripts tell a stranger
must hold. Numbers in the docstrings were measured on 3aa58a8.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import tallystick.ledger as ledger
from tallystick import TraceError, audit, load_run
from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main
from tallystick.echo_gate import echo_warnings

ROOT = Path(__file__).resolve().parents[1]


def _quiet(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _cli(argv, tmp_path):
    """The command as a person runs it: a real process whose stdout is a UTF-8
    stream, as a terminal or a CI log is. In-process capture is a StringIO,
    which stores any str and hides an encoding failure."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "tallystick.cli", *argv], cwd=tmp_path,
                          env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


# --------------------------------------------------------------------------- #
# The verdict core
# --------------------------------------------------------------------------- #


def _claim_cycle(n):
    """n intermediates; each step sees all the others and a document; each claim
    cites every other intermediate. The answer cites m0. Nothing is grounded."""
    t = "alpha"
    mids = [f"m{i}" for i in range(n)]
    return {
        "artifacts": ([{"artifact_id": "doc", "kind": "document", "content": t}]
                      + [{"artifact_id": m, "kind": "intermediate", "content": t} for m in mids]
                      + [{"artifact_id": "ans", "kind": "final_answer", "content": t}]),
        "steps": ([{"step_id": f"s{i}", "kind": "k", "inputs": [x for x in mids if x != m] + ["doc"],
                    "outputs": [m]} for i, m in enumerate(mids)]
                  + [{"step_id": "sa", "kind": "answer", "inputs": ["m0"], "outputs": ["ans"]}]),
        "claims": ([{"claim_id": f"c{i}", "artifact_id": m, "start": 0, "end": 5}
                    for i, m in enumerate(mids)]
                   + [{"claim_id": "ca", "artifact_id": "ans", "start": 0, "end": 5}]),
        "entries": ([{"entry_id": f"e{i}_{j}", "claim_id": f"c{i}",
                      "account": f"EVIDENCE:m{j}#0-5", "quoted_span": t}
                     for i in range(n) for j in range(n) if i != j]
                    + [{"entry_id": "ea", "claim_id": "ca", "account": "EVIDENCE:m0#0-5",
                        "quoted_span": t}]),
    }


def test_closing_the_books_on_a_claim_cycle_is_not_exponential(monkeypatch):
    """Results computed inside a cycle are not memoised (ledger.py, `tainted`),
    so the walk enumerates every simple path through a cycle of claims. Calls to
    `_resolve` for n = 4..8: 78, 363, 2,083, 14,241, 112,514. Wall clock for
    `tallystick audit` on n = 10, an 11 KB file: 42 s, exit 1; n = 11 about ten
    times that. The depth guard does not help: the cycle is 10 claims deep.
    A gate a hand-made 11 KB trace keeps busy for minutes answers nothing."""
    calls = [0]
    real = ledger._resolve

    def counted(*a, **kw):
        calls[0] += 1
        return real(*a, **kw)

    monkeypatch.setattr(ledger, "_resolve", counted)
    n = 8
    balance = ledger.close_books(load_run(_claim_cycle(n)))
    assert not balance.books_balance
    assert calls[0] <= n ** 4, f"{calls[0]} walk calls for {n} claims in a cycle"


def test_a_quote_over_an_assumed_sentence_does_not_report_grounded():
    """The summary has two claims: one credited to the document, one to a
    declared assumption. The answer quotes the whole summary. ledger.py takes
    all-of over the claims a span covers for FAILING ones, but among claims that
    close it keeps the BEST (`best_ok`), so the answer is `grounded`, the report
    prints "grounded 1 100%", and `--chain` shows only the document half.
    types.py: an assumption "funds, but is marked, so a reader can see which
    conclusions rest on assumptions rather than evidence". Here it is not.
    Present since 2104b07; exit code 0 either way."""
    t = {
        "artifacts": [
            {"artifact_id": "doc", "kind": "document", "content": "Revenue rose 14%."},
            {"artifact_id": "sum", "kind": "intermediate",
             "content": "Revenue rose 14%. Next year will be a record."},
            {"artifact_id": "ans", "kind": "final_answer",
             "content": "Revenue rose 14%. Next year will be a record."}],
        "steps": [{"step_id": "s1", "kind": "summarize", "inputs": ["doc"], "outputs": ["sum"]},
                  {"step_id": "s2", "kind": "answer", "inputs": ["sum"], "outputs": ["ans"]}],
        "assumptions": {"guess": "management's optimism"},
        "claims": [{"claim_id": "s_a", "artifact_id": "sum", "start": 0, "end": 17},
                   {"claim_id": "s_b", "artifact_id": "sum", "start": 18, "end": 45},
                   {"claim_id": "ans_1", "artifact_id": "ans", "start": 0, "end": 45}],
        "entries": [
            {"entry_id": "e1", "claim_id": "s_a", "account": "EVIDENCE:doc#0-17",
             "quoted_span": "Revenue rose 14%."},
            {"entry_id": "e2", "claim_id": "s_b", "account": "ASSUMPTION:guess"},
            {"entry_id": "e3", "claim_id": "ans_1", "account": "EVIDENCE:sum#0-45",
             "quoted_span": "Revenue rose 14%. Next year will be a record."}],
    }
    balance = ledger.close_books(load_run(t))
    assert balance.audits["s_b"].status is ledger.ClaimStatus.ASSUMED
    assert balance.audits["ans_1"].status is ledger.ClaimStatus.ASSUMED


def _named(x6_claim, x7_claim):
    c = "alpha bravo charlie"
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": c},
                      {"artifact_id": "x6", "kind": "intermediate", "content": c},
                      {"artifact_id": "x7", "kind": "intermediate", "content": c},
                      {"artifact_id": "ans", "kind": "final_answer", "content": "bravo"}],
        "steps": [{"step_id": "s0", "kind": "k", "inputs": ["x7"], "outputs": ["x6"]},
                  {"step_id": "s6", "kind": "k", "inputs": ["doc", "x6"], "outputs": ["x7"]},
                  {"step_id": "sa", "kind": "answer", "inputs": ["x7"], "outputs": ["ans"]}],
        "assumptions": {"a1": "p"},
        "claims": [{"claim_id": x6_claim, "artifact_id": "x6", "start": 0, "end": 5},
                   {"claim_id": x7_claim, "artifact_id": "x7", "start": 6, "end": 11},
                   {"claim_id": "x7_rest", "artifact_id": "x7", "start": 12, "end": 19},
                   {"claim_id": "zz_ans", "artifact_id": "ans", "start": 0, "end": 5}],
        "entries": [
            {"entry_id": "e1", "claim_id": x6_claim, "account": "EVIDENCE:x7#6-19",
             "quoted_span": "bravo charlie"},
            {"entry_id": "e2", "claim_id": x7_claim, "account": "EVIDENCE:x6#0-5",
             "quoted_span": "alpha"},
            {"entry_id": "e3", "claim_id": x7_claim, "account": "ASSUMPTION:a1"},
            {"entry_id": "e4", "claim_id": "x7_rest", "account": "EVIDENCE:doc#12-19",
             "quoted_span": "charlie"},
            {"entry_id": "e5", "claim_id": "zz_ans", "account": "EVIDENCE:x7#6-11",
             "quoted_span": "bravo"}],
    }


def test_the_answers_status_does_not_depend_on_claim_names():
    """ledger.py: "Results do not depend on ... how the claims are named". Two
    claims cite each other; one also rests on a declared assumption. Swapping
    the two claim ids (`c_a` <-> `c_b`) turns the answer from `assumed` into
    `grounded`. Found by fuzzing: 16 of 40,000 random traces, every one a swap
    between grounded and assumed; none flipped books_balance. Cause, shown by
    intervention: the two places that close an all-of on its BEST closing
    member (`best_ok` over the claims a span covers, `min(members)` for a group).
    With both taking the worst closing member, the same 40,000 traces give 0.
    Walked from one claim the other is still open and counts as circular; walked
    from the other it closes, so which claim sorts first decides."""
    one = ledger.close_books(load_run(_named("c_a", "c_b")))
    two = ledger.close_books(load_run(_named("c_b", "c_a")))
    assert one.audits["zz_ans"].status == two.audits["zz_ans"].status, (
        one.audits["zz_ans"].status, two.audits["zz_ans"].status)


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #

_SURROGATE_TEXT = "Canberra \ud83d is the capital."


def _balanced_with_surrogate():
    c = _SURROGATE_TEXT
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": c},
                      {"artifact_id": "ans", "kind": "final_answer", "content": c}],
        "steps": [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
                  {"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": len(c)}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#0-{len(c)}", "quoted_span": c}],
    }


def test_chain_view_of_a_balancing_trace_with_half_an_emoji_does_not_exit_1(tmp_path):
    """A lone surrogate (\\ud83d, half of an emoji cut in a UTF-16 log) is valid
    JSON. `audit` on this trace exits 0; `audit --chain c1` prints the claim text,
    print() raises UnicodeEncodeError, and the interpreter exits 1 - "the books
    do not balance" - on books that balance. An earlier fix handled the same
    character on the way into a file, not on the way to the terminal."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_balanced_with_surrogate()), encoding="utf-8")
    plain = _cli(["audit", str(path)], tmp_path)
    assert plain.returncode == 0, plain.stderr
    chain = _cli(["audit", "--chain", "c1", str(path)], tmp_path)
    assert chain.returncode == 0, chain.stderr[-400:]


def test_a_readable_trace_with_half_an_emoji_in_a_meta_note_does_not_exit_1(tmp_path):
    """examples/balanced_run.json with `_meta: {"notes": ["a note \\ud83d"]}`:
    `audit` exits 0, `check-trace` prints the note and exits 1 with a traceback."""
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"notes": ["a note \ud83d"]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    result = _cli(["check-trace", str(path)], tmp_path)
    assert result.returncode != 1 or "Traceback" not in result.stderr, result.stderr[-400:]


def test_audit_function_raises_trace_error_on_an_integer_too_long_to_read(tmp_path):
    """README: "A file that cannot be read ... raises TraceError". A claim `start`
    of 5,000 digits: json.loads raises ValueError (Python's 4,300-digit limit),
    which read_json_file does not turn into TraceError, so `except TraceError`
    does not catch it. The terminal exits 2 (its handler catches ValueError)."""
    text = json.dumps(_balanced_with_surrogate()).replace('"start": 0', '"start": ' + "1" * 5000)
    path = tmp_path / "t.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TraceError):
        audit(str(path))


# --------------------------------------------------------------------------- #
# The echo rule
# --------------------------------------------------------------------------- #


def _note_log(note, readback, final="Done."):
    return {"messages": [
        {"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": "I will note what I believe.", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "save_note", "arguments": json.dumps({"text": note}, ensure_ascii=False)}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
        {"role": "assistant", "content": "Reading it back.", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "read_note", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": readback},
        {"role": "assistant", "content": final},
    ]}


NOTE_20_WORDS = ("Sydney is the capital of Australia and it was chosen in 1901 because the "
                 "federation needed a neutral site between the two.")


@pytest.mark.xfail(strict=True, reason=(
    "known hole, right in substance: a short note read back inside a memory "
    "store's record (mem0, LangGraph Store) passes with no demotion and no note - "
    "the record's fields are more than half of the reply. Described in README.md, "
    "Known limitations, first item (python bench/store_record_threshold.py). Kept "
    "red on purpose: documented, not fixed."))
def test_a_note_read_back_from_a_memory_store_record_is_reported():
    """A memory store answers with the note inside its record - an id, a hash, a
    score, timestamps - the shape of a mem0 search result. Nobody is evading
    anything. Plain, the read-back warns; inside the record, notes up to 163
    characters (28 words) warn on nothing and check-trace exits 0. The record's
    fields are ordinary metadata, and whitespace words keep the JSON quotes
    glued to the note's first and last word, which removes them from the share."""
    record = json.dumps({"results": [{
        "id": "892db2ae-06d9-49e5-8b3e-585ef9b85b8e", "memory": NOTE_20_WORDS,
        "hash": "3f2b1c9e", "metadata": None, "score": 0.38,
        "created_at": "2026-09-16T10:00:00-07:00", "updated_at": None, "user_id": "alice"}]})
    plain = to_trace(_note_log(NOTE_20_WORDS, NOTE_20_WORDS))
    assert echo_warnings(plain["_meta"]), "baseline: the plain read-back warns"
    stored = to_trace(_note_log(NOTE_20_WORDS, record))
    assert echo_warnings(stored["_meta"]), "the same note in a store record warns on nothing"


@pytest.mark.xfail(strict=True, reason=(
    "known hole, right in substance: the model's draft pasted back as a `user` "
    "message is a root and nothing compares it with the model's text. Described in "
    "README.md, Known limitations, the item on a draft pasted back as a `user` "
    "message. Kept red on purpose: documented, not fixed."))
def test_the_models_draft_pasted_back_as_a_user_message_is_not_silent_evidence(tmp_path):
    """A self-refine loop: the model writes a draft, the harness pastes it back
    in a `user` message ("here is your previous draft"), the model answers with
    it. The reader records every user message as a `document` root, and the
    echo rule looks only at tool results. Posted with the answer credited to
    the user message, `audit` balances, and `check-trace` exits 0 with no word
    that the evidence is the model's own earlier turn, which the log shows."""
    answer = "Sydney is the capital of Australia."
    log = {"messages": [
        {"role": "user", "content": "What is the capital of Australia?"},
        {"role": "assistant", "content": "Draft: " + answer},
        {"role": "user", "content": "Here is your previous draft, check it and give the final "
                                    "answer:\n" + answer},
        {"role": "assistant", "content": answer},
    ]}
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    raw = to_trace(log)
    assert [a["kind"] for a in raw["artifacts"]][2] == "document"
    code, _out, _err = _quiet(["check-trace", str(path), "--quiet"])
    assert code != 0 or echo_warnings(raw["_meta"]), (
        "the model's draft, pasted back as a user message, is a root with no warning")


def test_a_tibetan_note_in_quotes_is_reported_like_a_chinese_one():
    """The reader cuts words at marks in scripts written without spaces, but on
    3aa58a8 the list (_NO_SPACES) has no Tibetan (U+0F00-U+0FFF), which is written without
    spaces between words. `Note: "<note>"` warns for the English and the Chinese
    note and not for the Tibetan one; a bare read-back warns for all three."""
    zh = "悉尼是澳大利亚的首都。"
    bo = "སིཌ་ནི་ཨོ་སི་ཊྲེ་ལི་ཡའི་རྒྱལ་ས་ཡིན།"
    assert echo_warnings(to_trace(_note_log(zh, f'Note: "{zh}"'))["_meta"])
    assert echo_warnings(to_trace(_note_log(bo, bo))["_meta"])
    assert echo_warnings(to_trace(_note_log(bo, f'Note: "{bo}"'))["_meta"])


def test_declaring_a_tool_named_tool_is_not_dropped_in_silence(tmp_path):
    """README: "a name that matches no tool in the log is reported rather than
    left to do nothing in silence. The name `tool` cannot be declared". A log
    whose note store is literally named `tool`: `--tool-returns-model-text tool`
    changes nothing - the result stays a tool_result root - and nothing is said;
    `declarations_not_in_log` is empty. The output is byte-identical to the run
    without the flag."""
    log = {"messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "tool",
         "content": "Sydney is the capital of Australia."},
        {"role": "assistant", "content": "Sydney is the capital of Australia."},
    ]}
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    _c0, without, _e0 = _quiet(["check-trace", str(path)])
    _c1, with_flag, _e1 = _quiet(["check-trace", str(path), "--tool-returns-model-text", "tool"])
    assert with_flag != without, "the declaration was dropped and nothing said so"


# --------------------------------------------------------------------------- #
# Exit 0 where little was checked
# --------------------------------------------------------------------------- #


def test_an_answer_checked_by_one_letter_is_not_reported_as_full_coverage(tmp_path):
    """Claims decide what the audit checks. An answer of 54 characters with one
    claim on its first letter, credited against one letter of a document:
    `audit` exits 0 and prints "coverage 100.0%". On the posted files that exist
    locally, 31 of the 146 AgentHallu traces whose books balance have at least
    half of the answer's letters and digits under no claim (8 of 126 on RAGTruth).
    The share is computable from the trace and is not printed anywhere."""
    answer = "Sydney is the capital of Australia. Revenue grew 900%."
    trace = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": "Canberra is the capital."},
                      {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
        "steps": [{"step_id": "s", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 1}],
        "entries": [{"entry_id": "e1", "claim_id": "c1", "account": "EVIDENCE:doc#0-1",
                     "quoted_span": "C"}],
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    code, out, _err = _quiet(["audit", str(path)])
    assert not (code == 0 and "coverage         100.0%" in out), out


# --------------------------------------------------------------------------- #
# What a stranger is told
# --------------------------------------------------------------------------- #


def test_the_diff_the_examples_readme_gives_is_empty_on_a_correct_copy():
    """examples/logs/README.md: `tallystick check-trace examples/logs/clean_run.json
    > /tmp/got.txt; diff /tmp/got.txt examples/expected/clean_run.txt` - "No
    output from diff means your copy says exactly what this repository's does."
    Every expected file opens with a `$ tallystick ...` line and ends with
    `exit: N`, so the diff is never empty: on this commit it prints 2 added
    lines at the top and 2 at the bottom, for a copy whose output is correct."""
    cwd = Path.cwd()
    os.chdir(ROOT)
    try:
        _code, out, _err = _quiet(["check-trace", "examples/logs/clean_run.json"])
    finally:
        os.chdir(cwd)
    expected = (ROOT / "examples" / "expected" / "clean_run.txt").read_text(encoding="utf-8")
    assert out == expected


def test_echo_coverage_without_the_corpus_says_so_instead_of_crashing(tmp_path):
    """README names `python bench/echo_coverage.py <AgentHallu>`. Given a
    directory with no trajectories it prints zeros, then raises
    ZeroDivisionError and exits 1. strict_mode.py with no argument exits 2 with
    its usage; auditability_agenthallu.py on a missing path exits 2."""
    result = subprocess.run([sys.executable, str(ROOT / "bench" / "echo_coverage.py"),
                             str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert "Traceback" not in result.stderr, result.stderr[-300:]


def test_auditability_script_on_the_clone_root_does_not_report_zero_with_exit_0(tmp_path):
    """docs/auditable-traces.md: "reproducible with `python
    bench/auditability_agenthallu.py --data <AgentHallu>`". `git clone
    https://github.com/liuxuannan/AgentHallu` puts the trajectories one folder
    down, in AgentHallu/AgentHallu/<framework>/. The script globs `*/*.json`, so
    given the clone it prints "0 trajectories, 0 labelled" and every row 0/0,
    and exits 0. (trace_growth.py, strict_mode.py and echo_coverage.py search
    recursively and take the clone.) Laid out here with the committed sample."""
    clone = tmp_path / "AgentHallu"
    shutil.copytree(ROOT / "bench" / "sample-agenthallu", clone / "AgentHallu")
    result = subprocess.run([sys.executable, str(ROOT / "bench" / "auditability_agenthallu.py"),
                             "--data", str(clone), "--draws", "10"],
                            capture_output=True, text=True, timeout=300, cwd=ROOT)
    found_nothing = result.stdout.startswith("0 trajectories")
    assert not (found_nothing and result.returncode == 0), result.stdout[:300]
