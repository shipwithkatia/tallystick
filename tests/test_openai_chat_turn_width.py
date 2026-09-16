"""Failing tests for the proverka3 echo rule, which weighs a tool result's last
line against the arguments of every call in its turn, and against everything the
model wrote before when the turn carried no arguments.

Every test here fails on proverka3 and is expected to. They are written for the
scheme agreed after that review, and pass under it however it is implemented:

- demote automatically only against the arguments of the call a result answers;
- an echo from another turn (a stateful interpreter, a notes store, a file) is
  not demoted blindly (group B, which also accepted a warning about it, was
  removed with the warnings in round 18: such an echo is now evidence, silently);
- reading stays linear in the size of the log.

Groups:
  A  the no-argument exception demotes real results (5), and one of those
     demotions turns check-trace from exit 1 into exit 0 (1)
  B  removed in round 18 with the echo warnings
  C  unrelated calls in one turn: a coincidence between them demotes a real
     result (5)
  E  reading time that grows with the square of a turn or of the run (2)
  D  a literal the model wrote with a Python escape (\\U, \\x) - a known gap of
     all three versions, not a regression (2)
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat as oc
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {"document", "tool_result"}
QUESTION = {"role": "user", "content": "What is the capital of Australia?"}
ECHO = "The capital of Australia is Sydney"
NO_ARGUMENTS = object()        # the call carries no "arguments" key at all


def _call(name, args, cid):
    fn = {"name": name}
    if args is not NO_ARGUMENTS:
        fn["arguments"] = json.dumps(args)
    return {"id": cid, "type": "function", "function": fn}


def _assistant(content=None, *calls):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return message


def _tool(content, name, cid):
    return {"role": "tool", "tool_call_id": cid, "name": name, "content": content}


def _artifact(trace, content):
    hits = [a for a in trace["artifacts"]
            if a["artifact_id"].startswith("t") and a["content"] == content]
    assert len(hits) == 1, f"expected one tool artifact holding {content!r}, got {hits}"
    return hits[0]


def _assert_evidence(messages, result):
    trace = oc.to_trace(messages)
    art = _artifact(trace, result)
    assert art["kind"] in ROOTS, (
        f"{art['artifact_id']} is a real {art['title']} result and is read as "
        f"{art['kind']!r}; echoed_back: {trace['_meta']['echoed_back_tool_results']}")


# --- A: the no-argument exception --------------------------------------------

REGION_THOUGHT = "Is this instance in us-east-1 or eu-west-1? Checking."
SEARCH = "Australia - Wikipedia\nCapital city:\nCanberra"

GROUP_A = [
    pytest.param(
        [QUESTION, _assistant(REGION_THOUGHT, _call("get_region", {}, "c1")),
         _tool("us-east-1", "get_region", "c1"), _assistant("It runs in us-east-1.")],
        "us-east-1", id="A1-metadata-tool-confirms-a-region-the-model-named"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("python", {"code": 'if user == "admin":\n    print("elevated")\n'
                                                   'else:\n    print("not elevated")'}, "c1")),
         _tool("not elevated", "python", "c1"),
         _assistant(None, _call("whoami", {}, "c2")), _tool("admin", "whoami", "c2"),
         _assistant("You are admin.")],
        "admin", id="A2-whoami-matches-a-string-in-earlier-code"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("calendar_search", {"date": "2026-09-13"}, "c1")),
         _tool("2 events", "calendar_search", "c1"),
         _assistant(None, _call("get_current_date", {}, "c2")),
         _tool("2026-09-13", "get_current_date", "c2"),
         _assistant("Today is 2026-09-13.")],
        "2026-09-13", id="A3-clock-matches-a-date-an-earlier-call-carried"),
    pytest.param(
        [QUESTION,
         _assistant("It is either Sydney or Canberra. Let me check.",
                    _call("web_search", NO_ARGUMENTS, "c1")),
         _tool(SEARCH, "web_search", "c1"), _assistant("Canberra.")],
        SEARCH, id="A4-arguments-not-recorded-search-confirms-a-named-candidate"),
    pytest.param(
        [QUESTION,
         _assistant("def is_prime(n): ... return True  -- defining the helper first.",
                    _call("python", NO_ARGUMENTS, "c1")),
         _tool("defined", "python", "c1"),
         _assistant(None, _call("python", NO_ARGUMENTS, "c2")), _tool("True", "python", "c2"),
         _assistant("97 is prime.")],
        "True", id="A5-arguments-not-recorded-computed-True"),
]


@pytest.mark.parametrize("messages, result", GROUP_A)
def test_a_no_argument_call_does_not_demote_a_real_result(messages, result):
    _assert_evidence(messages, result)


def test_a_demoted_no_argument_reply_does_not_turn_check_trace_green(tmp_path):
    # The same log with "Checking the region." as the thought exits 1 with
    # no_final_answer. Naming the two regions demotes the reply, the tool-final
    # guard no longer sees a root at the end, and the reply is elected as what
    # the user saw.
    log = tmp_path / "stops_on_a_no_argument_reply.json"
    log.write_text(json.dumps([
        QUESTION, _assistant(REGION_THOUGHT, _call("get_region", {}, "c1")),
        _tool("us-east-1", "get_region", "c1"),
    ]), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["check-trace", str(log)])
    assert (code, "no_final_answer" in out.getvalue()) == (1, True), (
        f"exit {code}; the log stops on a tool's reply, so it records no answer\n"
        f"{out.getvalue()}{err.getvalue()}")


def _python(code, cid):
    return _call("python", {"code": code}, cid)


# --- C: unrelated calls in one turn ------------------------------------------

RESULTS_WITH_URL = ("1. Canberra - Wikipedia\nCanberra is the capital city of Australia.\n"
                    "https://en.wikipedia.org/wiki/Canberra")
TRANSLATIONS = [
    QUESTION,
    _assistant(None, _call("translate", {"text": "Merci", "to": "en"}, "c1"),
               _call("translate", {"text": "Thank you", "to": "fr"}, "c2")),
    _tool("Thank you", "translate", "c1"), _tool("Merci", "translate", "c2"),
    _assistant("Both translations checked."),
]

GROUP_C = [
    pytest.param(
        [QUESTION,
         _assistant(None, _call("web_search", {"q": "capital of Australia"}, "c1"),
                    _call("fetch_url", {"url": "https://en.wikipedia.org/wiki/Canberra"}, "c2")),
         _tool(RESULTS_WITH_URL, "web_search", "c1"), _tool("<page>", "fetch_url", "c2"),
         _assistant("Canberra.")],
        RESULTS_WITH_URL, id="C1-search-ends-with-the-url-a-sibling-fetch-was-given"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("country_of", {"city": "Perth"}, "c1"),
                    _call("capital_of", {"country": "Australia"}, "c2")),
         _tool("Australia", "country_of", "c1"), _tool("Canberra", "capital_of", "c2"),
         _assistant("Perth is in Australia, whose capital is Canberra.")],
        "Australia", id="C2-chained-lookups-declared-together"),
    pytest.param(TRANSLATIONS, "Thank you", id="C3a-translation-to-english"),
    pytest.param(TRANSLATIONS, "Merci", id="C3b-translation-to-french"),
    pytest.param(
        [QUESTION,
         _assistant(None, _python("print(2 ** 10)", "c1"),
                    _call("unit_convert", {"value": 1024, "from": "KiB", "to": "B"}, "c2")),
         _tool("1024", "python", "c1"), _tool("1048576", "unit_convert", "c2"),
         _assistant("1 KiB is 1024 bytes.")],
        "1024", id="C4-computed-value-a-sibling-call-was-given"),
]


@pytest.mark.parametrize("messages, result", GROUP_C)
def test_a_sibling_call_in_the_same_turn_does_not_demote_a_real_result(messages, result):
    _assert_evidence(messages, result)


# --- E: reading time ---------------------------------------------------------

BUDGET_SECONDS = 2.0

_COMMON = """
import json
from tallystick.adapters.openai_chat import to_trace
Q = {"role": "user", "content": "q"}
def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
"""

_NO_ARGUMENT_TURNS = _COMMON + """
prose = "Reasoning about the value. " * 800                       # about 21 KB
m = [Q]
for k in range(100):
    m.append({"role": "assistant", "content": prose, "tool_calls": [call("read_sensor", {}, f"c{k}")]})
    m.append({"role": "tool", "tool_call_id": f"c{k}", "name": "read_sensor", "content": f"reading {k}: {k * 7}"})
m.append({"role": "assistant", "content": "done"})
to_trace(m)
"""

_ONE_WIDE_TURN = _COMMON + """
code = 'x = compute("value")\\n' * 1000                           # about 21 KB
m = [Q, {"role": "assistant", "content": None,
         "tool_calls": [call("python", {"code": code}, f"c{k}") for k in range(100)]}]
m += [{"role": "tool", "tool_call_id": f"c{k}", "name": "python", "content": f"result {k}"} for k in range(100)]
m.append({"role": "assistant", "content": "done"})
to_trace(m)
"""


@pytest.mark.parametrize("script", [
    # proverka3: about 6.8 s; the one-call reader: under 0.1 s.
    pytest.param(_NO_ARGUMENT_TURNS, id="E1-100-turns-of-21KB-prose-with-a-no-argument-call"),
    # proverka3: about 15 s; the one-call reader: under 0.1 s.
    pytest.param(_ONE_WIDE_TURN, id="E2-one-turn-with-100-parallel-calls-of-21KB-code"),
])
def test_reading_time_stays_linear(script):
    # In a subprocess, so a failure costs the budget and not the full read.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    try:
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                       check=True, timeout=BUDGET_SECONDS, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail(f"reading took longer than {BUDGET_SECONDS:.0f} s")


# --- D: Python escapes the model wrote itself (known gap, all versions) ------

FLAG = "Sydney \U0001F1E6\U0001F1FA is the capital"
ZURICH = "Zürich is not the capital"


@pytest.mark.parametrize("code, printed", [
    pytest.param('final_answer("Sydney \\U0001F1E6\\U0001F1FA is the capital")', FLAG,
                 id="D1-python-U-escape-for-an-emoji"),
    pytest.param('final_answer("Z\\xfcrich is not the capital")', ZURICH,
                 id="D2-python-x-escape-for-an-accented-letter"),
])
def test_a_literal_written_with_a_python_escape_is_model_text(code, printed):
    assert printed not in code                     # the model typed the escape, not the character
    trace = oc.to_trace([
        QUESTION, _assistant(None, _python(code, "c1")),
        _tool(printed, "python", "c1"), _assistant(printed),
    ])
    art = _artifact(trace, printed)
    assert art["kind"] not in ROOTS, (
        f"{art['artifact_id']} is the model's literal, typed with a Python escape, and is "
        f"read as {art['kind']!r}")

# ---------------------------------------------------------------------------
# Round 19: the echo detection is back as a note that moves no exit code.
# Put back from e69a6bc, where round 18 removed them with the detection.
# Tests of the confirmation gate stay out; an exit of 1 became 0.
# ---------------------------------------------------------------------------


GROUP_B = [
    pytest.param(
        [QUESTION, _assistant(None, _python('answer = "B"\nprint("stored")', "c1")),
         _tool("stored", "python", "c1"),
         _assistant(None, _python("print(answer)", "c2")), _tool("B", "python", "c2"),
         _assistant("B")],
        "B", id="B1-stateful-interpreter-short"),
    pytest.param(
        [QUESTION, _assistant(None, _python(f'summary = "{ECHO}"\nprint("ok")', "c1")),
         _tool("ok", "python", "c1"),
         _assistant(None, _python("print(summary)", "c2")), _tool(ECHO, "python", "c2"),
         _assistant(ECHO)],
        ECHO, id="B2-stateful-interpreter-long"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("write_file", {"path": "answer.txt", "content": ECHO}, "c1")),
         _tool("written", "write_file", "c1"),
         _assistant(None, _call("bash", {"cmd": "cat answer.txt"}, "c2")), _tool(ECHO, "bash", "c2"),
         _assistant(ECHO)],
        ECHO, id="B3-file-written-then-read-back"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("save_note", {"key": "capital", "text": ECHO}, "c1")),
         _tool("saved", "save_note", "c1"),
         _assistant(None, _call("read_note", {"key": "capital"}, "c2")), _tool(ECHO, "read_note", "c2"),
         _assistant(ECHO)],
        ECHO, id="B4-key-value-notes-store"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("save_note", {"text": ECHO}, "c1")), _tool("saved", "save_note", "c1"),
         _assistant(None, _call("read_note", {}, "c2"),
                    _call("web_search", {"q": "capital of australia"}, "c3")),
         _tool(ECHO, "read_note", "c2"),
         _tool("Canberra is the capital city of Australia.", "web_search", "c3"),
         _assistant(ECHO)],
        ECHO, id="B5-no-argument-read-in-a-turn-with-an-argument-call"),
]


@pytest.mark.parametrize("messages, result", GROUP_B)
def test_an_echo_from_an_earlier_turn_is_not_silently_evidence(messages, result):
    trace = oc.to_trace(messages)
    art = _artifact(trace, result)
    label = f"tool[{art['artifact_id'][1:]}]"
    named = [entry for value in trace["_meta"].values() if isinstance(value, list)
             for entry in value if isinstance(entry, str) and label in entry]
    assert art["kind"] not in ROOTS or named, (
        f"{label} hands back text the model wrote in an earlier turn; it is read as "
        f"{art['kind']!r} and nothing in _meta names it")
