"""The tool's main promise: the model's text must not pass as a source, and
what the report says must hold.

- Three ways the OpenAI reader recorded the model's text as a root: a tool
  handing back a whole message the model wrote; a call id repeated across
  turns; a reply in decomposed Unicode (NFD).
- A note must not move the exit code: half an emoji in it made `--json` exit 2.
- Arguments nested deeper than Python recurses: a sentence and exit 2, not a
  traceback and exit 1.
- "Could not finish" (exit 2) must not hide a verdict found beside it.
- The verdict line must not print 50% / 50% on an answer failed for being more
  than half unclaimed.
- The share of the answer under claims, at the edges six mutants of the report
  code slipped past (N_c2, C_t0, C_f1, C_j1, C_j2, C_l1).

Every case here is a small example written in this file, so the tests need no
data from outside the repository.
"""
import json
import unicodedata
from pathlib import Path

import pytest

from tallystick.adapters.openai_chat import to_trace
from tallystick.cli import main
from tallystick.echo_gate import echo_warnings
from tallystick.ledger import MAX_DEPTH

ROOT = Path(__file__).resolve().parent.parent


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _run(argv, capsys):
    capsys.readouterr()
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _kind(trace, aid):
    return next(a["kind"] for a in trace["artifacts"] if a["artifact_id"] == aid)


# --------------------------------------------------------------------------- #
# A tool hands back a whole message the model wrote
# --------------------------------------------------------------------------- #

DRAFT = ("Sydney is the capital of Australia; it was chosen in 1901 as a compromise "
         "between the two largest cities.")

READBACKS = {
    "bare": DRAFT,
    "label": "Memory [draft]: " + DRAFT,
    "history_json": json.dumps([{"role": "assistant", "content": DRAFT}]),
    "record_json": json.dumps({"id": 7, "text": DRAFT}),
    "trailing_period": DRAFT + ".",
    "decomposed": unicodedata.normalize("NFD", DRAFT.replace("Sydney", "Sýdney")),
}


def _recall_log(readback, draft=DRAFT, ask="What is the capital of Australia?"):
    return [{"role": "user", "content": ask},
            {"role": "assistant", "content": draft,
             "tool_calls": [_call("store_memory", {"key": "draft"}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "stored"},
            {"role": "assistant", "content": None,
             "tool_calls": [_call("recall", {"key": "draft"}, "c2")]},
            {"role": "tool", "tool_call_id": "c2", "content": readback},
            {"role": "assistant", "content": "Canberra."}]


@pytest.mark.parametrize("shape", list(READBACKS))
def test_a_tool_handing_back_a_message_the_model_wrote_is_noted(shape):
    draft = DRAFT.replace("Sydney", "Sýdney") if shape == "decomposed" else DRAFT
    meta = to_trace(_recall_log(READBACKS[shape], draft))["_meta"]
    notes = echo_warnings(meta)
    assert [n["kind"] for n in notes] == ["model_message"], notes
    assert notes[0]["result"] == "tool[4]"


def test_the_message_note_moves_no_exit_code_and_names_what_it_is(tmp_path, capsys):
    path = _write(tmp_path, "log.json", _recall_log(DRAFT))
    code, out, _err = _run(["check-trace", path], capsys)
    assert code == 0
    assert "NOTE - 1 tool result(s)" in out and "a whole message the model wrote earlier" in out
    report = tmp_path / "r.json"
    assert _run(["check-trace", path, "--quiet", "--json", str(report)], capsys)[0] == 0
    block = json.loads(report.read_text(encoding="utf-8"))["may_be_model_text"]
    assert block["count"] == 1 and block["results"][0]["kind"] == "model_message"


def test_a_search_repeating_the_question_the_model_restated_is_not_noted():
    """The price the note is held to: a reply that shares a phrase
    with the model's message - a search answering in the words of the query the
    model wrote in its plan - is not a message handed back."""
    plan = "I need to find the city where Marina Abramovic performed Rhythm 4 in 1974."
    reply = "Marina Abramovic performed Rhythm 4 in 1974 in the city of Belgrade, Serbia."
    meta = to_trace(_recall_log(reply, plan))["_meta"]
    assert echo_warnings(meta) == []


def test_a_short_message_is_not_a_value_to_match():
    """`Done.` said by the model and `Done.` returned by a tool: too short to say
    anything about where the reply came from (ECHO_MIN_CHARS)."""
    meta = to_trace(_recall_log("Done.", "Done."))["_meta"]
    assert echo_warnings(meta) == []


def test_a_long_page_holding_a_message_as_one_short_line_is_not_noted():
    """A piece must hold most of the reply: a page that merely contains the
    model's sentence as one of its lines is not the message handed back."""
    page = "\n".join(["Results for your search."] + [f"Line {i} of an unrelated page about "
                                                    f"something else entirely." for i in range(30)]
                     + [DRAFT])
    meta = to_trace(_recall_log(page))["_meta"]
    assert echo_warnings(meta) == []


def test_a_message_read_back_escaped_is_noted():
    """A history store that writes JSON with `\\uXXXX` escapes: undone on the
    reply's side before the comparison, however long the model's messages are."""
    draft = "Сидней - столица Австралии, выбранная в 1901 году как компромисс между городами."
    meta = to_trace(_recall_log(json.dumps({"text": draft}), draft))["_meta"]
    assert [n["kind"] for n in echo_warnings(meta)] == ["model_message"]


# --------------------------------------------------------------------------- #
# Call ids repeated across turns
# --------------------------------------------------------------------------- #

ANSWER = "The capital of Australia is Sydney, chosen in 1901."


def _two_turns(first_id, second_id, first_tool="web_search"):
    return [{"role": "user", "content": "What is the capital of Australia?"},
            {"role": "assistant", "content": None,
             "tool_calls": [_call(first_tool, {"query": "capital"}, first_id)]},
            {"role": "tool", "tool_call_id": first_id, "content": "Canberra is the capital."},
            {"role": "assistant", "content": None,
             "tool_calls": [_call("final_answer", {"answer": ANSWER}, second_id)]},
            {"role": "tool", "tool_call_id": second_id, "content": ANSWER},
            {"role": "assistant", "content": ANSWER}]


@pytest.mark.parametrize("ids", [("call_a", "call_b"), ("call_0", "call_0")],
                         ids=["unique", "per_turn"])
def test_a_value_handed_back_by_its_own_call_is_demoted_however_ids_are_numbered(ids):
    trace = to_trace(_two_turns(*ids))
    assert _kind(trace, "t4") == "intermediate"
    assert trace["_meta"]["unmatched_tool_results"] == []


def test_an_id_repeated_across_turns_still_places_every_result_by_id():
    """Three turns, `call_0` each time, three different tools: each result is
    tied to the call of its own turn, none is filed as matching nothing."""
    log = [{"role": "user", "content": "Plan a trip."}]
    for turn, tool in enumerate(("search_flights", "search_hotels", "book")):
        log.append({"role": "assistant", "content": None,
                    "tool_calls": [_call(tool, {"n": turn}, "call_0")]})
        log.append({"role": "tool", "tool_call_id": "call_0", "content": f"result {turn} of {tool}"})
    log.append({"role": "assistant", "content": "Booked."})
    trace = to_trace(log)
    assert trace["_meta"]["unmatched_tool_results"] == []
    assert [a["title"] for a in trace["artifacts"] if a["artifact_id"].startswith("t")] == [
        "search_flights", "search_hotels", "book"]


def test_an_id_declared_twice_in_one_turn_still_names_no_call():
    """The rule that stays: inside ONE turn, an id given to two calls does not
    say which of them a result answers, so the result is not placed by it."""
    log = [{"role": "user", "content": "q"},
           {"role": "assistant", "content": None,
            "tool_calls": [_call("final_answer", {"answer": ANSWER}, "dup"),
                           _call("web_search", {"query": "capital"}, "dup")]},
           {"role": "tool", "tool_call_id": "dup", "content": "Canberra is the capital city."},
           {"role": "tool", "tool_call_id": "dup", "content": ANSWER},
           {"role": "assistant", "content": ANSWER}]
    trace = to_trace(log)
    meta = trace["_meta"]
    # As docs/known-limitations.md, Known limitations, says: both filed as matching nothing, both
    # noted, neither demoted - not placed by name or order.
    assert meta["unmatched_tool_results"] == ["tool[2] (id dup)", "tool[3] (id dup)"]
    assert meta["guessed_tool_names"] == []
    assert [(w["result"], w["kind"]) for w in echo_warnings(meta)] == [
        ("tool[2]", "unmatched"), ("tool[3]", "unmatched")]
    assert _kind(trace, "t3") == "tool_result"


# --------------------------------------------------------------------------- #
# A reply in decomposed Unicode (NFD)
# --------------------------------------------------------------------------- #

VI = "Thủ đô của Úc là Sydney, được chọn năm 1901 như một sự thỏa hiệp giữa hai thành phố lớn"


def _same_call(note, reply):
    return [{"role": "user", "content": "Tell me."},
            {"role": "assistant", "content": None,
             "tool_calls": [_call("save_note", {"text": note}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": reply},
            {"role": "assistant", "content": "Done."}]


@pytest.mark.parametrize("sides", ["nfd_reply", "nfd_call", "nfd_both"])
def test_the_demotion_reads_both_sides_in_nfc(sides):
    nfd = unicodedata.normalize("NFD", VI)
    note, reply = {"nfd_reply": (VI, nfd), "nfd_call": (nfd, VI), "nfd_both": (nfd, nfd)}[sides]
    assert _kind(to_trace(_same_call(note, reply)), "t2") == "intermediate"


def test_a_decomposed_note_escaped_in_a_json_reply_is_found():
    """`{"text": "Thu\\u0309 ..."}`: the escape decodes to a decomposed letter
    inside the JSON value, after the reply itself was cleaned."""
    nfd = unicodedata.normalize("NFD", VI)
    reply = json.dumps({"saved": True, "text": nfd})       # ensure_ascii: \\uXXXX
    assert "\\u0309" in reply
    assert _kind(to_trace(_same_call(VI, reply)), "t2") == "intermediate"


def test_the_recorded_artifact_keeps_the_form_the_log_had():
    nfd = unicodedata.normalize("NFD", VI) + " - and a tool's own words after it, to stay a root"
    trace = to_trace(_same_call("something else entirely, written by the model", nfd))
    assert next(a["content"] for a in trace["artifacts"] if a["artifact_id"] == "t2") == nfd


# --------------------------------------------------------------------------- #
# A note with half an emoji does not move the exit code under --json
# --------------------------------------------------------------------------- #

HALF = "Sydney is the capital of Australia \ud83d and it was chosen in 1901 as a compromise."


def _half_emoji_log():
    return [{"role": "user", "content": "What is the capital of Australia?"},
            {"role": "assistant", "content": None,
             "tool_calls": [_call("save_note", {"text": HALF}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": None, "tool_calls": [_call("read_note", {}, "c2")]},
            {"role": "tool", "tool_call_id": "c2", "content": "Note: " + HALF},
            {"role": "assistant", "content": "Canberra."}]


def _raw_write(tmp_path, name, data):
    """json.dumps escapes the lone surrogate, so the file is valid UTF-8."""
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("declared", [False, True], ids=["listed", "cleared_by_declaration"])
def test_check_trace_json_with_a_half_emoji_note_exits_as_without_json(tmp_path, capsys, declared):
    path = _raw_write(tmp_path, "log.json", _half_emoji_log())
    flags = ["--tool-returns-external", "read_note"] if declared else []
    plain = _run(["check-trace", path, "--quiet", *flags], capsys)[0]
    report = tmp_path / "r.json"
    code, _out, err = _run(["check-trace", path, "--quiet", "--json", str(report), *flags], capsys)
    assert (plain, code) == (0, 0), err
    text = report.read_text(encoding="utf-8")
    assert "\\\\ud83d" in text                      # written as the terminal shows it
    data = json.loads(text)
    block = data["may_be_model_text"]
    listed = block["cleared_by_declaration"] if declared else block["results"]
    assert len(listed) == 1 and "\\ud83d" in listed[0]["line"]


def test_audit_json_with_a_half_emoji_note_exits_as_without_json(tmp_path, capsys):
    trace = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
    trace["_meta"] = {"echo_warning_details": [
        {"result": "t1", "tool": "read_note", "line": "half \ud83d emoji"}]}
    path = _raw_write(tmp_path, "posted.json", trace)
    assert _run(["audit", path, "--quiet"], capsys)[0] == 0
    assert _run(["audit", path, "--quiet", "--json", str(tmp_path / "a.json")], capsys)[0] == 0


def test_a_trace_file_with_half_an_emoji_is_still_refused(tmp_path, capsys):
    """Only the REPORT escapes: a trace written by `convert` is read by the next
    command, and an escape there would change the text it audits."""
    path = _raw_write(tmp_path, "log.json", _half_emoji_log())
    code, _out, err = _run(["convert", path, "-o", str(tmp_path / "t.json")], capsys)
    assert code == 2 and "cannot write" in err
    assert not (tmp_path / "t.json").exists()


# --------------------------------------------------------------------------- #
# Nested deeper than Python recurses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["check-trace", "convert"])
def test_arguments_nested_1500_deep_are_exit_2_with_a_sentence(tmp_path, capsys, command):
    args = "[" * 1500 + '"x"' + "]" * 1500
    log = [{"role": "user", "content": "q"},
           {"role": "assistant", "content": None, "tool_calls": [
               {"id": "c1", "type": "function", "function": {"name": "run", "arguments": args}}]},
           {"role": "tool", "tool_call_id": "c1", "content": "ok done here"},
           {"role": "assistant", "content": "answer text"}]
    path = _write(tmp_path, "log.json", log)
    argv = [command, path] + (["-o", str(tmp_path / "t.json")] if command == "convert" else ["--quiet"])
    code, _out, err = _run(argv, capsys)
    assert code == 2 and "nested more deeply than this reader can follow" in err


# --------------------------------------------------------------------------- #
# Exit 2 only when "could not finish" is the whole story
# --------------------------------------------------------------------------- #


def _chain(n, tail="", finals=1):
    text = "The filing reports revenue of 14 million."
    arts = [{"artifact_id": "doc", "kind": "document", "content": text}]
    steps, claims, entries, prev = [], [], [], "doc"
    for k in range(1, n + 2):
        aid = f"i{k}" if k <= n else "ans"
        arts.append({"artifact_id": aid, "kind": "intermediate" if k <= n else "final_answer",
                     "content": text if k <= n else text + tail})
        steps.append({"step_id": f"s{k}", "kind": "summarize", "inputs": [prev], "outputs": [aid]})
        claims.append({"claim_id": f"c_{aid}", "artifact_id": aid, "start": 0, "end": len(text)})
        entries.append({"entry_id": f"e_{aid}", "claim_id": f"c_{aid}",
                        "account": f"EVIDENCE:{prev}#0-{len(text)}", "quoted_span": text})
        prev = aid
    if finals == 2:
        arts.append({"artifact_id": "ans2", "kind": "final_answer", "content": text})
        steps.append({"step_id": "s_ans2", "kind": "answer", "inputs": ["doc"], "outputs": ["ans2"]})
        claims.append({"claim_id": "c_ans2", "artifact_id": "ans2", "start": 0, "end": len(text)})
        entries.append({"entry_id": "e_ans2", "claim_id": "c_ans2",
                        "account": f"EVIDENCE:doc#0-{len(text)}", "quoted_span": text})
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


TAIL = " And the moon is made of green cheese, as every astronomer confirms."


@pytest.mark.parametrize("case, expected", [
    ("deep_alone", (2, ["chain_too_deep"])),
    ("deep_and_unclaimed", (1, ["chain_too_deep", "answer_mostly_unclaimed"])),
    ("deep_and_two_answers", (1, ["chain_too_deep", "multiple_final_answers"])),
])
def test_exit_2_only_where_nothing_but_the_depth_is_wrong(tmp_path, capsys, case, expected):
    trace = {"deep_alone": _chain(MAX_DEPTH + 1),
             "deep_and_unclaimed": _chain(MAX_DEPTH + 1, TAIL),
             "deep_and_two_answers": _chain(MAX_DEPTH + 1, finals=2)}[case]
    path = _write(tmp_path, "t.json", trace)
    report = tmp_path / "r.json"
    code, _out, err = _run(["audit", path, "--quiet", "--json", str(report)], capsys)
    reasons = json.loads(report.read_text())["gate"]["reasons"]
    assert (code, reasons) == expected
    assert f"exit {code} - chain_too_deep" in err


# --------------------------------------------------------------------------- #
# The verdict line never reads half and half at exit 1
# --------------------------------------------------------------------------- #


def _half(claimed, total_letters=251):
    answer = "a" * claimed + " " + "b" * (total_letters - claimed)
    return {"artifacts": [{"artifact_id": "doc", "kind": "document", "content": answer},
                          {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
            "steps": [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
                      {"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
            "claims": [{"claim_id": "c0", "artifact_id": "ans", "start": 0, "end": claimed}],
            "entries": [{"entry_id": "e0", "claim_id": "c0", "account": f"EVIDENCE:doc#0-{claimed}",
                         "quoted_span": answer[:claimed]}]}


@pytest.mark.parametrize("claimed, total, line", [
    (125, 251, "BOOKS BALANCE ON 49% OF THE ANSWER - 51% IS UNDER NO CLAIM, NOT CHECKED"),
    (124, 251, "BOOKS BALANCE ON 49% OF THE ANSWER - 51% IS UNDER NO CLAIM, NOT CHECKED"),
    (70, 100, None),
    (29, 100, "BOOKS BALANCE ON 29% OF THE ANSWER - 71% IS UNDER NO CLAIM, NOT CHECKED"),
    (1, 300, "BOOKS BALANCE ON 0% OF THE ANSWER - 100% IS UNDER NO CLAIM, NOT CHECKED"),
])
def test_the_verdict_line_rounds_the_claimed_share_down(tmp_path, capsys, claimed, total, line):
    path = _write(tmp_path, "t.json", _half(claimed, total))
    code, out, _err = _run(["audit", path], capsys)
    verdict = next(v for v in out.splitlines() if v.startswith("BOOKS"))
    if line is None:
        assert (code, verdict) == (0, "BOOKS BALANCE")
    else:
        assert (code, verdict) == (1, line)


def test_exactly_half_under_a_claim_passes_and_says_plain_balance(tmp_path, capsys):
    path = _write(tmp_path, "t.json", _half(125, 250))
    code, out, _err = _run(["audit", path], capsys)
    assert code == 0 and "\nBOOKS BALANCE\n" in out


# --------------------------------------------------------------------------- #
# The share of the answer under claims: six mutants no other test noticed
# --------------------------------------------------------------------------- #


def test_c_l1_c_j1_c_j2_unclaimed_is_counted_under_any_claim_not_under_closed_ones(tmp_path, capsys):
    """laundered_summary: every letter of the answer is under a claim, and 54%
    under claims that close. `under no claim` is 0.0%, not 46.0% - the README
    demo shows exactly this, and nothing guarded it."""
    path = str(ROOT / "examples" / "laundered_summary.json")
    code, out, _err = _run(["audit", path], capsys)
    assert code == 1
    assert "  coverage         54.0% of the answer (67 of 124 letters and digits)" in out
    assert "  under no claim   0.0% of the answer - not checked" in out
    report = tmp_path / "r.json"
    _run(["audit", path, "--quiet", "--json", str(report)], capsys)
    answer = json.loads(report.read_text())["answer"]
    assert answer == {"letters_and_digits": 124, "under_a_claim": 124,
                      "under_claims_that_close": 67, "under_no_claim_share": 0.0}


def test_c_t0_an_answer_with_no_letters_or_digits_is_not_mostly_unclaimed(tmp_path, capsys):
    answer = "?!..."
    trace = {"artifacts": [{"artifact_id": "doc", "kind": "document", "content": answer},
                           {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
             "steps": [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
                       {"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
             "claims": [{"claim_id": "c0", "artifact_id": "ans", "start": 0, "end": 2}],
             "entries": [{"entry_id": "e0", "claim_id": "c0", "account": "EVIDENCE:doc#0-2",
                          "quoted_span": "?!"}]}
    path = _write(tmp_path, "t.json", trace)
    code, out, _err = _run(["audit", path], capsys)
    assert code == 0 and "the answer holds no letters or digits to cover" in out
    assert "answer_mostly_unclaimed" not in out


def test_c_f1_every_final_answer_counts_toward_the_share(tmp_path, capsys):
    """Two answers exit 1 anyway (multiple_final_answers), but the share is
    still of both: 41 + 41 letters here, the second one wholly unclaimed."""
    text = "The filing reports revenue of 14 million."
    trace = {"artifacts": [{"artifact_id": "doc", "kind": "document", "content": text},
                           {"artifact_id": "a1", "kind": "final_answer", "content": text},
                           {"artifact_id": "a2", "kind": "final_answer", "content": text}],
             "steps": [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
                       {"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["a1", "a2"]}],
             "claims": [{"claim_id": "c1", "artifact_id": "a1", "start": 0, "end": len(text)}],
             "entries": [{"entry_id": "e1", "claim_id": "c1",
                          "account": f"EVIDENCE:doc#0-{len(text)}", "quoted_span": text}]}
    path = _write(tmp_path, "t.json", trace)
    report = tmp_path / "r.json"
    code, _out, _err = _run(["audit", path, "--quiet", "--json", str(report)], capsys)
    data = json.loads(report.read_text())
    letters = sum(ch.isalnum() for ch in text)
    assert code == 1
    assert data["answer"]["letters_and_digits"] == 2 * letters
    assert data["answer"]["under_a_claim"] == letters
    assert data["answer"]["under_no_claim_share"] == 0.5


def test_n_c2_every_note_a_declaration_cleared_is_named(tmp_path, capsys):
    notes = [f"Note number {i}: Sydney is the capital of Australia, chosen in 1901 as a "
             f"compromise between the two largest cities of the federation." for i in (1, 2, 3)]
    log = [{"role": "user", "content": "Remember these."}]
    for i, note in enumerate(notes):
        log += [{"role": "assistant", "content": None,
                 "tool_calls": [_call("save_note", {"text": note}, f"s{i}")]},
                {"role": "tool", "tool_call_id": f"s{i}", "content": "ok"}]
    for i, note in enumerate(notes):
        log += [{"role": "assistant", "content": None,
                 "tool_calls": [_call("read_note", {"n": i}, f"r{i}")]},
                {"role": "tool", "tool_call_id": f"r{i}", "content": note}]
    log.append({"role": "assistant", "content": "Done."})
    path = _write(tmp_path, "log.json", log)
    report = tmp_path / "r.json"
    code, out, _err = _run(["check-trace", path, "--tool-returns-external", "read_note",
                            "--json", str(report)], capsys)
    block = json.loads(report.read_text())["may_be_model_text"]
    assert code == 0 and block["count"] == 0
    assert len(block["cleared_by_declaration"]) == 3
    assert "Notes cleared by a declaration, not by review: 3 tool result(s)." in out
    assert out.count("- cleared by --tool-returns-external read_note") == 3


# --------------------------------------------------------------------------- #
# Three mutants of the reader's id and escape handling that no other test
# noticed
# --------------------------------------------------------------------------- #


def test_an_id_repeated_inside_an_earlier_turn_does_not_blind_a_later_one():
    """A12_dup_never_reset: turn 1 gives `dup` to two calls, which places
    nothing by that id - in turn 1. Turn 2 gives `dup` to one call, and its
    result is that call's: `final_answer(X)` answering X is demoted."""
    log = [{"role": "user", "content": "q"},
           {"role": "assistant", "content": None,
            "tool_calls": [_call("web_search", {"query": "a"}, "dup"),
                           _call("web_search", {"query": "b"}, "dup")]},
           {"role": "tool", "tool_call_id": "dup", "content": "first page of results"},
           {"role": "tool", "tool_call_id": "dup", "content": "second page of results"},
           {"role": "assistant", "content": None,
            "tool_calls": [_call("final_answer", {"answer": ANSWER}, "dup")]},
           {"role": "tool", "tool_call_id": "dup", "content": ANSWER},
           {"role": "assistant", "content": ANSWER}]
    assert _kind(to_trace(log), "t5") == "intermediate"


def test_a_long_escaped_record_holding_a_decomposed_message_is_noted():
    """A13_candidates_uncleaned: past UNESCAPE_RESULT_CHARS the reply is not
    decoded as a whole, and its JSON value decodes to decomposed letters: the
    value is cleaned (NFC) before it is compared with the model's message."""
    from tallystick.adapters.openai_chat import UNESCAPE_RESULT_CHARS
    # Mostly plain letters, so the decoded value still holds most of the reply;
    # only the accents arrive as escapes (`e\u0301`).
    draft = " ".join(["The café in Sýdney near the harbour was chosen for the meeting in 1901"] * 30)
    reply = json.dumps({"id": 7, "text": unicodedata.normalize("NFD", draft)})
    assert len(reply) > UNESCAPE_RESULT_CHARS and "\\u0301" in reply
    meta = to_trace(_recall_log(reply, draft))["_meta"]
    assert [n["kind"] for n in echo_warnings(meta)] == ["model_message"]


def test_a_literal_with_an_escaped_quote_and_decomposed_escapes_is_found_among_calls():
    """A13_literal_uncleaned: two calls of one tool and results placed by name,
    so each result is weighed against both calls' pieces. The model's code holds
    `print("She said \\"Thu\\u0309 ...\\" today")`; the piece is the literal with
    its escapes undone, in NFC, and the printed line is found in it."""
    said = 'She said "Thủ đô của Úc là Sydney, được chọn năm 1901" today'
    escaped = unicodedata.normalize("NFD", said).encode("ascii", "backslashreplace").decode()
    code = 'print("' + escaped.replace('"', '\\"') + '")'
    log = [{"role": "user", "content": "q"},
           {"role": "assistant", "content": None,
            "tool_calls": [{"type": "function", "function": {"name": "python", "arguments": code}},
                           {"type": "function", "function": {"name": "python",
                                                             "arguments": "print(1 + 1)"}}]},
           {"role": "tool", "name": "python", "content": said},
           {"role": "tool", "name": "python", "content": "2"},
           {"role": "assistant", "content": "Done."}]
    assert _kind(to_trace(log), "t2") == "intermediate"
