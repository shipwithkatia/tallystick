"""Failing tests for the echo rule of the wide-context reader, before the fix
that narrowed it: the rule weighed a tool result's last line against everything
the model has written in the run.

Every test here fails on that reader and is expected to. A test passes when the
reader stops doing what it describes, however the fix is made.

- Eight real results - external or computed - read as the model's own words,
  because their last line happens to stand somewhere in what the model wrote:
  a guess it named, a source it quoted, a token in unrelated code. The old
  narrow rule (arguments of the answered call only) kept every one of them as
  evidence.
- A demotion that turns `check-trace` from exit 1 into exit 0. The module
  docstring licenses the rule on one condition - it can only make the audit
  stricter - and says any second effect voids that licence. This is one.
- Two echoes the old reader caught and this one records as evidence: a
  character outside the Basic Multilingual Plane, which a Python log written
  with ensure_ascii=True spells as a surrogate pair.
- Reading time that grows with the square of the run: every tool result
  re-joins and re-unescapes everything written so far.
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


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


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


# --- real evidence demoted --------------------------------------------------

SEARCH = "Australia - Wikipedia\nCapital city:\nCanberra"
INFOBOX = "Eiffel Tower\nLocation: Paris\nHeight:\n330 metres"
BRITANNICA = "Canberra | Britannica\nCanberra is the capital city of Australia."

EVIDENCE = [
    pytest.param(
        [QUESTION,
         _assistant(None, _call("python", {"code": "def is_prime(n):\n    if n < 2:\n"
                                                   "        return False\n    return True"}, "c1")),
         _tool("defined", "python", "c1"),
         _assistant(None, _call("python", {"code": "print(is_prime(97))"}, "c2")),
         _tool("True", "python", "c2"),
         _assistant("97 is prime.")],
        "True", id="computed-True-matches-return-True-in-earlier-code"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("python", {"code": "errors = 0\nprint('scanning')"}, "c1")),
         _tool("scanning", "python", "c1"),
         _assistant(None, _call("python", {"code": "print(sum(1 for l in open('app.log') "
                                                   "if 'ERROR' in l))"}, "c2")),
         _tool("0", "python", "c2"),
         _assistant("No errors in the log.")],
        "0", id="computed-zero-matches-errors-equals-0"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("python", {"code": "r = requests.get(url)\n"
                                                   "if r.status_code == 200:\n"
                                                   "    print(r.text[:80])"}, "c1")),
         _tool("<html>...", "python", "c1"),
         _assistant(None, _call("http_status", {"url": "https://example.com/report.pdf"}, "c2")),
         _tool("200", "http_status", "c2"),
         _assistant("The report is online.")],
        "200", id="server-status-matches-status-check-in-code"),
    pytest.param(
        [QUESTION,
         _assistant("It is either Sydney or Canberra. Let me check.",
                    _call("web_search", {"q": "capital of Australia"}, "c1")),
         _tool(SEARCH, "web_search", "c1"),
         _assistant("Canberra.")],
        SEARCH, id="search-confirms-a-candidate-the-model-named"),
    pytest.param(
        [QUESTION,
         _assistant("I believe the Eiffel Tower is 330 metres tall. Let me verify.",
                    _call("fetch_url", {"url": "https://en.wikipedia.org/wiki/Eiffel_Tower"}, "c1")),
         _tool(INFOBOX, "fetch_url", "c1"),
         _assistant("It is 330 metres tall.")],
        INFOBOX, id="page-confirms-the-models-hypothesis"),
    pytest.param(
        [QUESTION,
         _assistant("I need the country of this Australian-sounding city.",
                    _call("country_of", {"city": "Perth"}, "c1")),
         _tool("Australia", "country_of", "c1"),
         _assistant("Perth is in Australia.")],
        "Australia", id="lookup-is-a-substring-of-a-longer-word"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("web_search", {"q": "capital of Australia"}, "c1")),
         _tool("Results:\nCanberra is the capital city of Australia.", "web_search", "c1"),
         _assistant("Source 1 says: Canberra is the capital city of Australia. "
                    "Checking a second source.",
                    _call("fetch_url", {"url": "https://www.britannica.com/place/Canberra"}, "c2")),
         _tool(BRITANNICA, "fetch_url", "c2"),
         _assistant("Canberra.")],
        BRITANNICA, id="second-source-after-the-model-quoted-the-first"),
    pytest.param(
        [QUESTION,
         _assistant(None, _call("python", {"code": "yes_votes = 0\nprint('ready')"}, "c1")),
         _tool("ready", "python", "c1"),
         _assistant(None, _call("ask_approver", {"request": "Deploy v2 to production?"}, "c2")),
         _tool("yes", "ask_approver", "c2"),
         _assistant("Approved, deploying.")],
        "yes", id="human-approval-matches-an-identifier-prefix"),
]


@pytest.mark.parametrize("messages, result", EVIDENCE)
def test_real_result_stays_evidence(messages, result):
    trace = oc.to_trace(messages)
    art = _artifact(trace, result)
    assert art["kind"] in ROOTS, (
        f"{art['artifact_id']} is a real {art['title']} result and is read as "
        f"{art['kind']!r}; echoed_back: {trace['_meta']['echoed_back_tool_results']}")


# --- a demotion that turns the gate green -----------------------------------

def test_demoting_the_last_tool_result_does_not_turn_check_trace_green(tmp_path):
    # The same log without "either Sydney or Canberra" in the thought exits 1
    # with no_final_answer. Naming the candidate demotes the search result,
    # the tool-final guard no longer sees a root at the end, and the search
    # result is elected as what the user saw.
    log = tmp_path / "stops_on_a_demoted_tool_reply.json"
    log.write_text(json.dumps([
        QUESTION,
        _assistant("It is either Sydney or Canberra. Let me check.",
                   _call("web_search", {"q": "capital of Australia"}, "c1")),
        _tool(SEARCH, "web_search", "c1"),
    ]), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["check-trace", str(log)])
    assert (code, "no_final_answer" in out.getvalue()) == (1, True), (
        f"exit {code}; the log stops on a tool's reply, so it records no answer\n"
        f"{out.getvalue()}{err.getvalue()}")


# --- echoes recorded as evidence again ---------------------------------------

@pytest.mark.parametrize("line", [
    pytest.param("Sydney \U0001F1E6\U0001F1FA is the capital", id="flag-emoji"),
    pytest.param("\U0001D465 = 42", id="math-italic-x"),
])
def test_literal_outside_the_bmp_logged_with_ensure_ascii_is_model_text(line):
    code = f'final_answer("{line}")'
    assert "\\ud8" in json.dumps({"code": code}).lower()      # the surrogate-pair spelling
    trace = oc.to_trace([
        QUESTION,
        _assistant(None, _call("python", {"code": code}, "c1")),
        _tool(line, "python", "c1"),
        _assistant(line),
    ])
    art = _artifact(trace, line)
    assert art["kind"] not in ROOTS, f"{art['artifact_id']} is the model's literal, read as {art['kind']!r}"


# --- reading time ----------------------------------------------------------

TURNS = 200
BUDGET_SECONDS = 5.0

_READ_A_LONG_RUN = f"""
import json
from tallystick.adapters.openai_chat import to_trace
script = "# analysis step\\n" + "x = compute(\\"value\\")\\n" * 1000      # about 21 KB
messages = [{{"role": "user", "content": "q"}}]
for k in range({TURNS}):
    messages.append({{"role": "assistant", "content": None, "tool_calls": [
        {{"id": f"c{{k}}", "type": "function",
          "function": {{"name": "python", "arguments": json.dumps({{"code": script}})}}}}]}})
    messages.append({{"role": "tool", "tool_call_id": f"c{{k}}", "name": "python",
                      "content": f"result {{k}}: {{k * 7}}"}})
messages.append({{"role": "assistant", "content": "done"}})
to_trace(messages)
"""


def test_a_long_codeact_run_reads_in_seconds_not_minutes():
    # 200 turns, each call carrying a 21 KB script: about 4 MB of arguments.
    # What this test guards is the budget and nothing finer: the read finishes
    # inside BUDGET_SECONDS. It does not time the read, so no figure here is
    # held to. The wide-context reader this budget was written against is in no
    # commit reachable from HEAD, so the seconds it took cannot be measured
    # again from this history; the figure that used to stand here has been
    # dropped rather than left unrepeatable. Run in a subprocess so a failure
    # costs the budget, not the full read.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    try:
        subprocess.run([sys.executable, "-c", _READ_A_LONG_RUN], cwd=ROOT, env=env,
                       check=True, timeout=BUDGET_SECONDS, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail(f"reading {TURNS} turns of ~21 KB code took longer than "
                    f"{BUDGET_SECONDS:.0f} s")
