"""Round 10: nothing the reading caught may leave as a silent "it balances".

The ninth review found four ways a result the reader had already caught - or
could not finish weighing - went out with exit 0:

- MAX_STARTS. A run of matching words is not started from a word that stands
  in more than 64 places. Every word of an echo repeated 22 times in the
  arguments emptied the coverage of a reply that was 90% the model's sentence;
  21 times still warned. On the cross path the index grows over the whole run,
  so an agent that keeps handing its own note to tools blinds the check itself
  (31 re-feeds warned, 32 did not).
- The threshold-free rule ("the whole reply IS a plain value of an earlier
  call") compared values exactly: a change of case, a zero-width space or a
  pair of quotes emptied it, and a value the model wrote into code unquoted
  (`answer = B`) was never a value at all.
- A non-breaking space in a JSON or `label: value` reply stopped the demotion.
- `--tool-returns-external` cleared warnings and said nothing about it.

Plus two guards the suite lacked: the same-turn warning branch could be
switched off with every test green, and the timing bench could miss a cache.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat as oc
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"role": "user", "content": "go"}
PAYLOAD = ("The plant recorded four point six million tonnes of output last year "
           "and the board approved the expansion without further review")
HALF = len(PAYLOAD) // 2
#: The sixth review's shape: only coverage can see it, no line is the value.
BROKEN = PAYLOAD[:HALF] + "\n" + PAYLOAD[HALF:] + "\n0"


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _meta(messages, **kw):
    return oc.to_trace(messages, **kw)["_meta"]


def _noticed(meta):
    return bool(meta["echo_warning_details"] or meta["echoed_back_tool_results"])


def _exit_code(tmp_path, messages, *flags):
    path = tmp_path / "log.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    return main(["check-trace", "--from", "openai", str(path), "--quiet", *flags])


# --- point 2: MAX_STARTS on the call a result answers ------------------------

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
    assert _exit_code(tmp_path, messages) == 1


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


# --- point 3: the cross path, whose index grows over the whole run -----------

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
    assert _exit_code(tmp_path, messages) == 1


# --- point 4.1: a value written into code without quotes ---------------------

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


# --- point 4.2: case, invisible characters, quotes ---------------------------

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
    pytest.param("canberra", id="lower-case"),
    pytest.param("CANBERRA", id="upper-case"),
    pytest.param("Canberra​", id="trailing-zero-width-space"),
    pytest.param("Can​berra", id="zero-width-space-inside"),
    pytest.param("﻿Canberra", id="byte-order-mark"),
    pytest.param('"Canberra"', id="double-quotes"),
    pytest.param("'Canberra'", id="single-quotes"),
    pytest.param("“Canberra”", id="typographic-quotes"),
])
def test_the_threshold_free_rule_is_not_knocked_out_by_invisible_changes(readback):
    meta = _meta(_saved_then_read("Canberra", readback))
    assert meta["echo_warning_details"], f"{readback!r} read back after saving 'Canberra'"


def test_coverage_ignores_case_too():
    meta = _meta(_saved_then_read(PAYLOAD, BROKEN.upper()))
    assert meta["echo_warning_details"]


def test_the_demotion_keeps_case():
    # Folding case in the demotion demoted 61 more results on AgentHallu, and
    # every one read was real work - this is the commonest: a ticker lookup.
    # `ZETA` is not the model's `Zeta Corp` handed back; it is what the tool
    # looked up.
    meta = _meta(_answered({"name": "Zeta Corp"}, json.dumps({"symbol": "ZETA"})))
    assert not meta["echoed_back_tool_results"], meta["echoed_back_tool_results"]


def test_an_echo_in_another_case_is_still_warned_on_the_answering_call():
    meta = _meta(_answered({"note": PAYLOAD}, BROKEN.upper()))
    assert [w["kind"] for w in meta["echo_warning_details"]] == ["answering_call"]


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


# --- point 4.3: the same-turn warning, which nothing guarded -----------------

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
    assert _exit_code(tmp_path, messages) == 1


def test_a_short_value_saved_and_read_back_in_one_turn_is_warned():
    kinds = [w["kind"] for w in _meta(_one_turn("B", stored="B"))["echo_warning_details"]]
    assert kinds == ["same_turn"], kinds


# --- point 4.4: what a declaration cleared is said ---------------------------

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
    gate = json.loads(out.read_text(encoding="utf-8"))["gate"]
    assert [w["tool"] for w in gate["echo_warnings"]["cleared_by_declaration"]] == ["read_note"]


def test_control_nothing_is_listed_when_nothing_was_cleared():
    meta = _meta(_saved_then_read(PAYLOAD, "Checked: consistent."),
                 external_tools={"read_note"})
    assert meta["echo_warnings_cleared_by_declaration"] == []


# --- point 6: the timing bench clears every cache the reader has -------------

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


# --- point 1: the trace grows with the square of the turns -------------------

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
    "not fixed in round 10: every step lists every artifact before it, because "
    "the format's `inputs` is an explicit list and the verifier checks a citation "
    "against it. Linear output needs a format change; see docs/auditable-traces.md"))
def test_the_trace_grows_linearly_with_the_number_of_turns():
    small = len(json.dumps(oc.to_trace(_long(200))))
    large = len(json.dumps(oc.to_trace(_long(400))))
    # A budget on the SIZE of the output, not only on time: doubling the turns
    # may at most double the file, with room for ids growing a digit.
    assert large < 2.3 * small, f"200 turns: {small} B, 400 turns: {large} B"
