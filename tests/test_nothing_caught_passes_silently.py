"""Nothing the reading caught may leave as a silent "it balances".

A result the reader has already caught - or could not finish weighing - must
not go out with exit 0 without a word. Here: the demotion keeps case, a
non-breaking space does not stop it, the timing bench clears every cache the
reader has, the trace grows with the square of the turns (a strict xfail), and
the note on a reply that may be the model's own text is listed - including
where the reader ran out of work (MAX_STARTS), a value carried across turns,
and what an external declaration cleared - and moves no exit code.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat as oc

ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"role": "user", "content": "go"}
PAYLOAD = ("The plant recorded four point six million tonnes of output last year "
           "and the board approved the expansion without further review")


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _meta(messages, **kw):
    return oc.to_trace(messages, **kw)["_meta"]


# --- case and invisible characters, on the demotion ------------------------

def test_the_demotion_keeps_case():
    # Folding case in the demotion demoted 61 more results on AgentHallu, and
    # every one read was real work - this is the commonest: a ticker lookup.
    # `ZETA` is not the model's `Zeta Corp` handed back; it is what the tool
    # looked up.
    meta = _meta(_answered({"name": "Zeta Corp"}, json.dumps({"symbol": "ZETA"})))
    assert not meta["echoed_back_tool_results"], meta["echoed_back_tool_results"]


def _answered(args, reply):
    return [QUESTION,
            {"role": "assistant", "content": "save",
             "tool_calls": [_call("notes", args, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "notes", "content": reply},
            {"role": "assistant", "content": "done"}]


@pytest.mark.parametrize("reply", [
    pytest.param(json.dumps({"text": PAYLOAD}, ensure_ascii=False).replace(" ", " "),
                 id="json-reply-non-breaking-spaces"),
    pytest.param(f"Saved note: {PAYLOAD}".replace(" ", " "),
                 id="label-reply-non-breaking-spaces"),
    pytest.param(json.dumps({"text": PAYLOAD}).replace(" ", " "),
                 id="json-reply-narrow-no-break-spaces"),
])
def test_a_non_breaking_space_does_not_stop_the_demotion(reply):
    meta = _meta(_answered({"text": PAYLOAD}, reply))
    assert meta["echoed_back_tool_results"], "the reply IS the value it was given"


# --- the timing bench clears every cache the reader has ---------------------

def _cached_functions():
    """Every function in the package decorated with a functools cache, found
    by reading the source - not by asking the module, which is how the bench
    finds them."""
    found = set()
    for path in sorted((ROOT / "tallystick").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if name in ("lru_cache", "cache"):
                    found.add(node.name)
    return found


def test_the_timing_bench_clears_every_cache():
    spec = importlib.util.spec_from_file_location("reading_time", ROOT / "bench" / "reading_time.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    cleared = {getattr(c, "__wrapped__", c).__name__ for c in bench.CACHES}
    in_code = _cached_functions()
    assert in_code, "no cached function found: the scan itself is broken"
    missing = in_code - cleared
    assert not missing, (
        f"the reader caches {sorted(missing)} and bench/reading_time.py does not "
        f"clear it: every time it prints is then a warm-cache time")


# --- the trace grows with the square of the turns ---------------------------

def _long(turns):
    messages = [QUESTION]
    for k in range(turns):
        messages += [{"role": "assistant", "content": f"step {k}",
                      "tool_calls": [_call("search", {"q": f"item {k}"}, f"c{k}")]},
                     {"role": "tool", "tool_call_id": f"c{k}", "name": "search",
                      "content": f"result {k}"}]
    messages.append({"role": "assistant", "content": "done"})
    return messages


@pytest.mark.xfail(strict=True, reason=(
    "not fixed: every step lists every artifact before it, because "
    "the format's `inputs` is an explicit list and the verifier checks a citation "
    "against it. Linear output needs a format change; see docs/auditable-traces.md"))
def test_the_trace_grows_linearly_with_the_number_of_turns():
    small = len(json.dumps(oc.to_trace(_long(200))))
    large = len(json.dumps(oc.to_trace(_long(400))))
    # A budget on the SIZE of the output, not only on time: doubling the turns
    # may at most double the file, with room for ids growing a digit.
    assert large < 2.3 * small, f"200 turns: {small} B, 400 turns: {large} B"

# ---------------------------------------------------------------------------
# The echo detection is a note that moves no exit code. These tests come from
# f84f278, where the detection was a gate; its tests of the confirmation gate
# are not here, and where it expected exit 1 they expect 0.
# ---------------------------------------------------------------------------

from tallystick.cli import main  # noqa: E402


HALF = len(PAYLOAD) // 2


#: A shape only coverage can see: no line of the reply is the value.
BROKEN = PAYLOAD[:HALF] + "\n" + PAYLOAD[HALF:] + "\n0"


def _noticed(meta):
    return bool(meta["echo_warning_details"] or meta["echoed_back_tool_results"])


def _exit_code(tmp_path, messages, *flags):
    path = tmp_path / "log.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    return main(["check-trace", "--from", "openai", str(path), "--quiet", *flags])


def _repeated_in_args(reps):
    filler = " ".join(PAYLOAD.split()) + " "
    code = f'print("{PAYLOAD}")\n# ' + filler * reps
    return [QUESTION,
            {"role": "assistant", "content": "run it",
             "tool_calls": [_call("python", {"code": code}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "python", "content": BROKEN},
            {"role": "assistant", "content": PAYLOAD}]


@pytest.mark.parametrize("reps", [21, 22, 64, 200])
def test_a_repeated_word_limit_never_passes_an_echo_in_silence(tmp_path, reps):
    messages = _repeated_in_args(reps)
    assert _noticed(_meta(messages)), (
        f"each word of the echo repeated {reps}x in the arguments: the reply is "
        f"90% the model's sentence and the reading said nothing")
    assert _exit_code(tmp_path, messages) == 0   # a note, not a gate


def test_a_reply_it_could_not_finish_weighing_says_so():
    # The shape the speed limit exists for: one word, tens of thousands of
    # places. Weighing it exactly costs the square of its length. The reading
    # may stop - but then it must say "could not check", not "nothing here".
    import time
    args = json.dumps({"code": " ".join(["value"] * 20000)})
    reply = " ".join(["value"] * 4000)
    messages = [QUESTION,
                {"role": "assistant", "content": "run",
                 "tool_calls": [_call("python", args, "c1")]},
                {"role": "tool", "tool_call_id": "c1", "name": "python", "content": reply},
                {"role": "assistant", "content": "done"}]
    start = time.perf_counter()
    meta = _meta(messages)
    seconds = time.perf_counter() - start
    assert seconds < 5.0, f"took {seconds:.1f}s"
    kinds = [w["kind"] for w in meta["echo_warning_details"]]
    assert kinds == ["unchecked"], kinds


def _refed(times):
    """A note saved, handed to a checking tool `times` times, then read back."""
    messages = [QUESTION,
                {"role": "assistant", "content": "save",
                 "tool_calls": [_call("save_note", {"note": PAYLOAD}, "c0")]},
                {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"}]
    for k in range(times):
        messages += [{"role": "assistant", "content": f"check {k}",
                      "tool_calls": [_call("verify", {"claim": PAYLOAD, "round": k}, f"f{k}")]},
                     {"role": "tool", "tool_call_id": f"f{k}", "name": "verify",
                      "content": f"Checked against source {k}: consistent."}]
    messages += [{"role": "assistant", "content": "read it back",
                  "tool_calls": [_call("read_note", {"key": "n1"}, "c1")]},
                 {"role": "tool", "tool_call_id": "c1", "name": "read_note", "content": BROKEN},
                 {"role": "assistant", "content": PAYLOAD}]
    return messages


@pytest.mark.parametrize("times", [31, 32, 100])
def test_working_with_its_own_note_does_not_blind_the_cross_path(tmp_path, times):
    messages = _refed(times)
    meta = _meta(messages)
    read = [w for w in meta["echo_warning_details"] if w["tool"] == "read_note"]
    assert read, f"the note re-fed {times} times came back and nothing was said"
    assert _exit_code(tmp_path, messages) == 0   # a note, not a gate


def _kept_in_code(code, reply):
    return [QUESTION,
            {"role": "assistant", "content": "run",
             "tool_calls": [_call("python", {"code": code}, "c0")]},
            {"role": "tool", "tool_call_id": "c0", "name": "python", "content": "ok"},
            {"role": "assistant", "content": "print it",
             "tool_calls": [_call("python", {"code": "print(answer)"}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "python", "content": reply},
            {"role": "assistant", "content": "done"}]


@pytest.mark.parametrize("code, reply", [
    ("answer = B", "B"), ("answer = yes", "yes"), ("answer = 0", "0"),
    ("answer = True", "True"), ("answer=B", "B"), ("answer = 4.6  # tonnes", "4.6"),
])
def test_a_bare_value_assigned_in_code_is_a_value(code, reply):
    meta = _meta(_kept_in_code(code, reply))
    assert meta["echo_warning_details"], f"{code!r} then a reply of {reply!r}: nothing said"


def test_control_the_quoted_form_still_warns():
    assert _meta(_kept_in_code('answer = "B"', "B"))["echo_warning_details"]


def test_control_a_call_on_the_right_is_not_a_value():
    # `results = compute(dataset)` hands `dataset` to a function; it does not
    # state that anything IS `dataset`.
    meta = _meta(_kept_in_code("results = compute(dataset)", "dataset"))
    assert not meta["echo_warning_details"], meta["echo_warning_details"]


def _saved_then_read(stored, readback):
    return [QUESTION,
            {"role": "assistant", "content": "save",
             "tool_calls": [_call("save_note", {"note": stored}, "c0")]},
            {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"},
            {"role": "assistant", "content": "read",
             "tool_calls": [_call("read_note", {"key": "n1"}, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "read_note", "content": readback},
            {"role": "assistant", "content": "done"}]


@pytest.mark.parametrize("readback", [
    pytest.param("Canberra\u200b", id="trailing-zero-width-space"),
    pytest.param("Can\u200bberra", id="zero-width-space-inside"),
    pytest.param("﻿Canberra", id="byte-order-mark"),
    pytest.param('"Canberra"', id="double-quotes"),
    pytest.param("'Canberra'", id="single-quotes"),
    pytest.param("“Canberra”", id="typographic-quotes"),
])
def test_the_threshold_free_rule_is_not_knocked_out_by_invisible_changes(readback):
    meta = _meta(_saved_then_read("Canberra", readback))
    assert meta["echo_warning_details"], f"{readback!r} read back after saving 'Canberra'"


@pytest.mark.parametrize("readback", ["canberra", "CANBERRA"])
def test_a_value_read_back_in_another_case_is_not_warned(readback):
    meta = _meta(_saved_then_read("Canberra", readback))
    assert not meta["echo_warning_details"], meta["echo_warning_details"]


def test_coverage_does_not_fold_case():
    meta = _meta(_saved_then_read(PAYLOAD, BROKEN.upper()))
    assert not meta["echo_warning_details"], meta["echo_warning_details"]


def test_an_echo_in_another_case_is_not_warned_on_the_answering_call():
    meta = _meta(_answered({"note": PAYLOAD}, BROKEN.upper()))
    assert not meta["echo_warning_details"], meta["echo_warning_details"]


def test_control_the_same_echo_in_its_own_case_is_warned():
    # What the case tests above must not be allowed to hide: the rule itself.
    meta = _meta(_answered({"note": PAYLOAD}, BROKEN))
    assert [w["kind"] for w in meta["echo_warning_details"]] == ["answering_call"]


def _one_turn(readback, stored=PAYLOAD):
    return [QUESTION,
            {"role": "assistant", "content": "save and read",
             "tool_calls": [_call("save_note", {"note": stored}, "c0"),
                            _call("read_note", {"key": "n1"}, "c1")]},
            {"role": "tool", "tool_call_id": "c0", "name": "save_note", "content": "ok"},
            {"role": "tool", "tool_call_id": "c1", "name": "read_note", "content": readback},
            {"role": "assistant", "content": "done"}]


def test_a_note_saved_and_read_back_in_one_turn_is_warned(tmp_path):
    messages = _one_turn(BROKEN)
    kinds = [w["kind"] for w in _meta(messages)["echo_warning_details"]]
    assert kinds == ["same_turn"], kinds
    assert _exit_code(tmp_path, messages) == 0   # a note, not a gate


def test_a_short_value_saved_and_read_back_in_one_turn_is_warned():
    kinds = [w["kind"] for w in _meta(_one_turn("B", stored="B"))["echo_warning_details"]]
    assert kinds == ["same_turn"], kinds


def test_warnings_cleared_by_an_external_declaration_are_listed(tmp_path, capsys):
    messages = _saved_then_read(PAYLOAD, BROKEN)
    meta = _meta(messages, external_tools={"read_note"})
    assert not meta["echo_warning_details"]
    cleared = meta["echo_warnings_cleared_by_declaration"]
    assert [(w["tool"], w["kind"]) for w in cleared] == [("read_note", "earlier_turn")]
    assert cleared[0]["cleared_by"] == "--tool-returns-external read_note"

    assert _exit_code(tmp_path, messages, "--tool-returns-external", "read_note") == 0
    err = capsys.readouterr().err
    assert "cleared" in err and "--tool-returns-external read_note" in err, err


def test_cleared_warnings_reach_the_json_a_ci_job_keeps(tmp_path):
    messages = _saved_then_read(PAYLOAD, BROKEN)
    log, out = tmp_path / "log.json", tmp_path / "out.json"
    log.write_text(json.dumps(messages), encoding="utf-8")
    code = main(["check-trace", "--from", "openai", str(log), "--quiet",
                 "--tool-returns-external", "read_note", "--json", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "echo_warnings" not in payload["gate"]      # beside the gate, not in it
    cleared = payload["may_be_model_text"]["cleared_by_declaration"]
    assert [w["tool"] for w in cleared] == ["read_note"]


def test_control_nothing_is_listed_when_nothing_was_cleared():
    meta = _meta(_saved_then_read(PAYLOAD, "Checked: consistent."),
                 external_tools={"read_note"})
    assert meta["echo_warnings_cleared_by_declaration"] == []
