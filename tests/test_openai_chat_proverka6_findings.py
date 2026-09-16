"""The sixth review's findings, each with a test that fails without its fix.

The headline one: the answering-call echo rule was defeated by three
characters - a newline in the middle of the echo and a one-character line after
it. The newline broke the "more than half" threshold, because each half is then
at most half; the one-character last line emptied the last-line test. On the
corpus that took the rule from 216 fires to 0.

The scheme this file pins:

  * a result is DEMOTED only when the whole reply, in some spelling and with
    its trailing punctuation trimmed one mark at a time, IS a value the
    answering call carried. No threshold, no last line, no requirement that a
    match hold a space - nothing to shift;
  * any other match with the answering call was a WARNING until round 18,
    which removed the warnings. The three-character trick (P1-P3) and the
    tests that pinned warnings went with them: that trick is now a silent pass,
    and README says so.

Everything here is built in the file. Nothing needs the AgentHallu corpus.
That is the point of it: in the sixth review eight mutations of the echo path
went unnoticed by all 477 tests, and three more were caught only by tests that
skip for anyone who clones this repository. Every mutation in
bench/mutations/mutate.py is now noticed with the corpus absent - checked by
running the suite with TALLYSTICK_AGENTHALLU pointed at an empty directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat as oc

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {"document", "tool_result"}
QUESTION = {"role": "user", "content": "go"}

#: A sentence long enough that nobody writes it twice by accident.
CLAIM = ("The Australian Bureau of Statistics recorded that the resident population "
         "of the Canberra region reached four hundred and sixty two thousand people "
         "in the June quarter, an increase of one point nine per cent on the year.")


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _log(args, result, name="notes", cid="c1", answer="done"):
    return [QUESTION,
            {"role": "assistant", "content": "working", "tool_calls": [_call(name, args, cid)]},
            {"role": "tool", "tool_call_id": cid, "name": name, "content": result},
            {"role": "assistant", "content": answer}]


def _read(args, result, **kw):
    trace = oc.to_trace(_log(args, result), **kw)
    art = next(a for a in trace["artifacts"] if a["artifact_id"].startswith("t"))
    meta = trace["_meta"]
    return {"kind": art["kind"],
            "demoted": bool(meta["echoed_back_tool_results"]),
            "warnings": meta["echo_warning_details"],
            "meta": meta}


def _noticed(read) -> bool:
    """Did the reading refuse to pass this result off as evidence? Since round 18
    only a demotion does: the warnings this used to accept are gone."""
    return read["demoted"]


# --- the three-character bypass, and the two tests it defeated --------------

HALF = len(CLAIM) // 2


@pytest.mark.parametrize("result, why", [
    pytest.param("=" * 200 + "\n" + CLAIM,
                 "the echo diluted below half by a banner", id="P4-diluted-below-half"),
])
def test_an_echo_broken_up_or_diluted_is_not_silent(result, why):
    read = _read({"text": CLAIM}, result)
    assert _noticed(read), (
        f"{why}: the reply is the model's own sentence and the reading passed it "
        f"as evidence with no warning (kind={read['kind']})")


def test_an_echo_at_exactly_half_is_not_silent():
    # The old rule wanted MORE than half, so an echo padded to exactly half
    # walked through. There is no such boundary left to stand on.
    pad = "x" * sum(c.isalnum() for c in CLAIM)
    read = _read({"text": CLAIM}, CLAIM + "\n" + pad)
    assert _noticed(read), "an echo at exactly half of the reply passed silently"


def test_an_echo_that_holds_no_space_is_not_excused():
    # The old rule required a matched phrase to hold a space, so that a path or
    # an identifier a tool legitimately repeats would not demote it. A value
    # with no space in it is still the model's value.
    token = "Canberra1913ACTAustralia"
    read = _read({"answer": token}, token)
    assert _noticed(read), "a one-word value handed straight back passed silently"


# --- hole 1: trailing punctuation ------------------------------------------

@pytest.mark.parametrize("sent, back, why", [
    pytest.param("Canberra has been the capital since 1913.",
                 "Canberra has been the capital since 1913..",
                 "a sentence that already ended in a period, and the tool added one",
                 id="T1-period-on-a-period"),
    pytest.param("Canberra has been the capital since 1913.",
                 "Canberra has been the capital since 1913.!",
                 "a period then an exclamation mark", id="T2-two-different-marks"),
    pytest.param("Is Canberra the capital?",
                 "Is Canberra the capital?.",
                 "a question mark then a period", id="T3-question-then-period"),
])
def test_a_reply_that_gained_one_more_punctuation_mark_is_demoted(sent, back, why):
    # The trailing run was stripped whole, so the candidates held the form with
    # no marks and the form with two - never the one with one, which is what
    # the arguments carry. A warning is not enough here: the whole reply IS the
    # value, and trimming one mark at a time is what finds it.
    read = _read({"text": sent}, back)
    assert read["demoted"], f"{why}: not demoted (kind={read['kind']})"


# --- the shapes the demotion must still catch outright ---------------------

@pytest.mark.parametrize("args, result, id_", [
    pytest.param({"answer": "0"}, "0", "W1-a-bare-zero"),
    pytest.param({"answer": "1.3"}, "1.3", "W2-a-number"),
    pytest.param({"text": CLAIM}, CLAIM, "W3-the-sentence-itself"),
    pytest.param({"text": CLAIM}, CLAIM + ".", "W4-with-a-period-added"),
    pytest.param({"text": CLAIM}, "  " + CLAIM + "  ", "W5-with-surrounding-space"),
    pytest.param({"text": "line one\nline two"}, "line one line two",
                 "W6-reflowed-onto-one-line"),
])
def test_a_reply_that_is_exactly_the_value_it_was_given_is_demoted(args, result, id_):
    read = _read(args, result)
    assert read["demoted"], f"{id_}: the whole reply IS the value the call carried"
    assert read["kind"] not in ROOTS


@pytest.mark.parametrize("args, result, id_", [
    pytest.param({"text": CLAIM}, json.dumps({"text": CLAIM}), "V1-json-value"),
    pytest.param({"text": CLAIM}, json.dumps({"text": CLAIM}, indent=1), "V2-json-indented"),
    pytest.param({"text": CLAIM}, "Saved note: " + CLAIM, "V3-after-a-label"),
    pytest.param({"text": CLAIM}, CLAIM + "\n[Execution time: 0.01s]", "V4-status-line-after"),
    pytest.param({"text": CLAIM}, "'" + CLAIM + "'", "V5-quoted-repr"),
])
def test_a_value_handed_back_inside_a_reply_is_demoted(args, result, id_):
    read = _read(args, result)
    assert _noticed(read), f"{id_}: the call's own value stands inside the reply"


def test_a_tool_reporting_a_fact_about_its_argument_is_not_demoted():
    # The reason the old rule carried a threshold and a space requirement at
    # all. This must stay evidence the audit may stop on - a warning is fine,
    # a demotion is not: the tool reported something it knows and the model did
    # not write.
    read = _read({"source": "final_report.pdf", "destination": "temp"},
                 json.dumps({"result": "'final_report.pdf' moved to 'temp/final_report.pdf'"}))
    assert not read["demoted"], "a status line about a filename is not an echo"


# --- escaping: the arguments are compared in their unescaped spellings ------

def test_the_arguments_are_compared_with_their_escapes_undone():
    # The model's own code escapes its quotes and the JSON layer escapes them
    # again. Both layers have to come off or the value is never recognised.
    inner = 'The plant produced 4.6 million tonnes'
    code = 'final_answer("' + inner + '")'
    read = _read(code, inner)
    assert _noticed(read), "a literal the model quoted in its own code went unnoticed"


def test_a_reply_written_with_its_escapes_still_in_is_recognised():
    sentence = 'The company said it would "expand" its plant capacity next year'
    read = _read({"text": sentence}, sentence.replace('"', '\\"'))
    assert _noticed(read), "the reply was the same sentence, escaped once"


# --- hole 2: the reader's placeholder name ---------------------------------

NOTE = "The plant recorded four point six million tonnes of output last year"


def _unnamed_log():
    """Two calls the log gives no name for: the reader's placeholder stands in."""
    return [QUESTION,
            {"role": "assistant", "content": "save", "tool_calls": [
                {"id": "c0", "type": "function",
                 "function": {"arguments": json.dumps({"note": NOTE})}}]},
            {"role": "tool", "tool_call_id": "c0", "content": "ok"},
            {"role": "assistant", "content": "read", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"arguments": json.dumps({"key": "n1"})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": NOTE},
            {"role": "assistant", "content": "done"}]


def test_the_placeholder_name_cannot_be_declared_verbatim():
    trace = oc.to_trace(_unnamed_log(), verbatim_tools={"tool"})
    kinds = [a["kind"] for a in trace["artifacts"] if a["artifact_id"].startswith("t")]
    assert "document" not in kinds, (
        "`tool` is the name the reader puts in where the log gives none; declaring "
        "it must not turn every unnamed result into external text kept verbatim")


# --- hole 3: declarations compared byte for byte ---------------------------

PAGE = "Page 12 of the annual report states the figure was 4.6 million tonnes."


@pytest.mark.parametrize("declared", ["read_file", "Read_File", "READ_FILE",
                                      " read_file", "read_file "])
def test_a_declaration_finds_its_tool_whatever_the_case_or_spacing(declared):
    trace = oc.to_trace(_log({"path": "r.txt"}, PAGE, name="read_file"),
                        verbatim_tools={declared})
    art = next(a for a in trace["artifacts"] if a["artifact_id"].startswith("t"))
    assert art["kind"] == "document", (
        f"declared {declared!r}, the log says 'read_file', and the declaration "
        f"did nothing - silently")


def test_a_declared_name_that_matches_no_tool_is_said_out_loud():
    trace = oc.to_trace(_log({"path": "r.txt"}, PAGE, name="read_file"),
                        verbatim_tools={"reed_file"})
    assert trace["_meta"].get("declarations_not_in_log") == ["reed_file"], (
        "a declaration that matches no tool in the log must be reported, not "
        "silently ignored")


def test_a_contradiction_is_refused_whatever_the_case():
    with pytest.raises(ValueError):
        oc.to_trace(_log({"path": "r.txt"}, PAGE, name="read_file"),
                    model_text_tools={"read_file"}, verbatim_tools={"Read_File"})


# --- hole 4: reading time with many distinct tool names --------------------

BUDGET_SECONDS = 20.0
WIDE_NAMES = """
import json, sys, time
sys.path.insert(0, {root!r})
from tallystick.adapters.openai_chat import to_trace

def log(n):
    calls = [{{"type": "function", "function": {{
                 "name": "tool_%d" % k,
                 "arguments": json.dumps({{"code": "y=%d" % k}})}}}} for k in range(n)]
    msgs = [{{"role": "user", "content": "q"}},
            {{"role": "assistant", "content": None, "tool_calls": calls}}]
    msgs += [{{"role": "tool", "content": "a sentence the tool returned for call %d" % k}}
             for k in range(n)]
    msgs.append({{"role": "assistant", "content": "done"}})
    return msgs

small = time.perf_counter(); to_trace(log(250));  small = time.perf_counter() - small
big   = time.perf_counter(); to_trace(log(2000)); big   = time.perf_counter() - big
# Eight times the calls. Linear would be about eight times the work; quadratic
# is sixty-four. Three times the linear estimate leaves room for a slow machine
# and still fails a quadratic reading.
assert big <= max(small * 8 * 3, 0.5), "N=250 %.3fs, N=2000 %.3fs" % (small, big)
"""


def test_a_wide_turn_of_distinct_tool_names_reads_in_linear_time():
    # bench/reading_time.py used one tool name for every call, so the per-result
    # work over the set of distinct names never showed.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", WIDE_NAMES.format(root=str(ROOT))],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=BUDGET_SECONDS * 3)
    assert proc.returncode == 0, proc.stderr.strip()[-400:]


def test_the_report_says_out_loud_that_a_declaration_matched_nothing(tmp_path, capsys):
    from tallystick.cli import main
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"messages": _log({"path": "r.txt"}, PAGE, name="read_file")}),
                    encoding="utf-8")
    main(["check-trace", "--from", "openai", str(path),
          "--tool-returns-verbatim", "reed_file"])
    out = capsys.readouterr().out
    assert "declared but not in this log: reed_file" in out, out


def test_the_whole_reply_is_a_candidate_even_when_it_spans_several_lines():
    # A reply whose lines each match nothing, and whose whole - once the line
    # breaks are collapsed - is exactly the value the call carried. Only the
    # whole-reply candidate finds this one.
    sent = "line one of the note\nline two of the note"
    read = _read({"text": sent}, "line one of the note\nline two of the note")
    assert read["demoted"], f"kind={read['kind']}"


def test_a_one_character_line_is_not_a_value_of_the_call():
    # The guard that keeps a stray trailing character out: a reply that merely
    # ENDS in a character the arguments also hold is not an echo of it. Only a
    # reply that IS that one character is.
    read = _read({"code": "print(x)"}, "Result of the run:\nx")
    assert not read["demoted"], (
        "a one-character line demoted a reply that is not that character")


def test_a_reply_broken_across_lines_is_found_by_the_whole_reply_alone():
    # The arguments carry one line; the reply carries the same words across
    # two. No line of the reply is a value, and no value of the call is a line
    # of the reply - only the whole reply, with its line breaks collapsed, is.
    sent = "alpha beta gamma delta epsilon"
    read = _read({"text": sent}, "alpha beta\ngamma delta epsilon")
    assert read["demoted"], f"kind={read['kind']}"


@pytest.mark.parametrize("sent, back", [
    pytest.param("Canberra has been the capital since 1913.",
                 "Canberra has been the capital since 1913..", id="one-more-period"),
    pytest.param("Canberra has been the capital since 1913",
                 "Canberra has been the capital since 1913.", id="a-period-added"),
])
def test_trailing_punctuation_is_trimmed_on_both_sides(sent, back):
    # What fixes the sixth review's second hole is that the SAME trimming runs
    # over the arguments and over the reply. The old code trimmed only the
    # reply, so the form the arguments carried - with exactly one mark - was in
    # neither set.
    read = _read({"text": sent}, back)
    assert read["demoted"], f"{sent!r} -> {back!r}: kind={read['kind']}"


def test_a_reply_stored_escaped_is_decoded_before_its_lines_are_read():
    # A note store hands the note back as one escaped blob: the line breaks are
    # `\n` two characters, not line breaks. Undoing that is what turns the blob
    # into lines, and one of those lines is the value the call carried. Without
    # it the reply is a single line that matches nothing.
    value = "the plant produced four point six million tonnes"
    blob = "header line\\n" + value            # a literal backslash-n
    assert "\n" not in blob
    read = _read({"text": value}, blob)
    assert read["demoted"], (
        f"the escaped reply was not decoded before its lines were read "
        f"(kind={read['kind']})")

# ---------------------------------------------------------------------------
# Round 19: the echo detection is back as a note that moves no exit code.
# Put back from e69a6bc, where round 18 removed them with the detection.
# Tests of the confirmation gate stay out; an exit of 1 became 0.
# ---------------------------------------------------------------------------


def test_the_placeholder_name_cannot_be_declared_model_text():
    trace = oc.to_trace(_unnamed_log(), model_text_tools={"tool"})
    assert trace["_meta"]["echo_warning_details"], (
        "declaring the placeholder silenced the warning about an unnamed tool")


def test_the_placeholder_name_cannot_vouch_as_external():
    # The protection commit 7760068 states in words: an external declaration
    # clears a warning "only for a tool known by its own name".
    trace = oc.to_trace(_unnamed_log(), external_tools={"tool"})
    assert trace["_meta"]["echo_warning_details"], (
        "an external declaration vouched for a tool the log never named")


def test_the_warning_quotes_what_it_matched_not_the_last_line():
    # The bypass ends in a one-character line. Quoting the last line would show
    # the reader's own answer as `0` and tell the person reading it nothing.
    result = CLAIM[:HALF] + "\n" + CLAIM[HALF:] + "\n0"
    read = _read({"query": CLAIM}, result)
    assert read["warnings"], "the bypass produced no warning"
    line = read["warnings"][0]["line"]
    assert line.strip() != "0", "the warning quoted the character the tool added"
    assert CLAIM.split()[0] in line, line


def test_the_bypass_is_noted_end_to_end_and_moves_no_exit_code(tmp_path, capsys):
    from tallystick.cli import main
    result = CLAIM[:HALF] + "\n" + CLAIM[HALF:] + "\n0"
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"messages": _log({"query": CLAIM}, result, name="stats_api")}),
                    encoding="utf-8")
    # Round 19: this held exit 1 until the tool was confirmed by name. It is
    # a note now: said on stderr, exit 0, and no flag to pass.
    assert main(["check-trace", "--from", "openai", str(path), "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "1 tool result(s) may be the model's own text" in err and "stats_api" in err, err


def test_an_external_declaration_does_not_vouch_where_the_placement_is_a_guess():
    # `--tool-returns-external NAME` clears warnings about that tool. Where the
    # reader had to place the result by position among several calls, it does
    # not know that this result is that tool's - so the declaration must not
    # reach it. The name is in the log; what is missing is which call answered.
    note = "The plant recorded four point six million tonnes of output last year"
    log = [QUESTION,
           {"role": "assistant", "content": "save", "tool_calls": [
               _call("save_note", {"note": note}, "c0")]},
           {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"},
           {"role": "assistant", "content": "two calls, no ids on the results",
            "tool_calls": [
                {"type": "function",
                 "function": {"name": "read_note", "arguments": json.dumps({"key": "n1"})}},
                {"type": "function",
                 "function": {"name": "web_search", "arguments": json.dumps({"q": "output"})}}]},
           {"role": "tool", "content": note},
           {"role": "tool", "content": "An unrelated page about shipping."},
           {"role": "assistant", "content": "done"}]
    plain = oc.to_trace(json.loads(json.dumps(log)))
    assert plain["_meta"]["echo_warning_details"], "no warning to clear in the first place"
    vouched = oc.to_trace(json.loads(json.dumps(log)), external_tools={"read_note"})
    assert vouched["_meta"]["echo_warning_details"], (
        "an external declaration vouched for a result the reader only guessed "
        "belonged to that tool")


def _noticed_or_noted(read) -> bool:
    """Demoted, or listed as a note (e69a6bc's `_noticed`). `_noticed` above stays
    demotion only, as round 18 made it."""
    return read["demoted"] or bool(read["warnings"])


@pytest.mark.parametrize("result, why", [
    pytest.param(CLAIM[:HALF] + "\n" + CLAIM[HALF:] + "\n0",
                 "a newline in the middle and a one-character line after it",
                 id="P1-the-three-character-bypass"),
    pytest.param(CLAIM[:HALF] + "\n" + CLAIM[HALF:] + "\n[ok]",
                 "a newline in the middle and a short status line after it",
                 id="P2-newline-and-a-status-line"),
    pytest.param(CLAIM[:40] + "\n" + CLAIM[40:90] + "\n" + CLAIM[90:] + "\n0",
                 "the echo broken across three lines", id="P3-three-way-split"),
])
def test_an_echo_broken_up_is_not_silent(result, why):
    # e69a6bc's P1-P3 of `test_an_echo_broken_up_or_diluted_is_not_silent`, which
    # round 18 removed (P4 stayed there, on the demotion). Only the note sees them.
    read = _read({"text": CLAIM}, result)
    assert _noticed_or_noted(read), (
        f"{why}: the reply is the model's own sentence and the reading passed it "
        f"as evidence with no note (kind={read['kind']})")
