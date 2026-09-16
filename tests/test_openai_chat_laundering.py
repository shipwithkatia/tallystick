"""Inputs on which the OpenAI reader records the model's own text as evidence.

Every test here fails on v0.7.5 and is expected to: each one is a log where a
tool hands back text the model wrote, and the reader records that text as a
root (`document` / `tool_result`) - the failure this library exists to catch.
A test passes when the reader stops doing that, however the fix is made.

Found by adversarial review of `_hands_back_what_it_was_given`, its quoting
check `_is_whole_literal`, the `ESCAPE_CHARS` bound, result-to-call matching
without ids, and runs whose answer arrives as a tool result.
"""

from __future__ import annotations

import json

import pytest

from tallystick.adapters import openai_chat as oc

ROOTS = {"document", "tool_result"}
QUESTION = {"role": "user", "content": "What is the capital of Australia?"}
ECHO = "The capital of Australia is Sydney"


def _call(name, args, cid=None):
    call = {"type": "function", "function": {"name": name, "arguments": args}}
    if cid:
        call["id"] = cid
    return call


def _tool(content, name=None, cid=None):
    message = {"role": "tool", "content": content}
    if name:
        message["name"] = name
    if cid:
        message["tool_call_id"] = cid
    return message


def _code(code: str) -> str:
    """Arguments as OpenAI sends them: a JSON string, so the model's quotes and
    newlines arrive escaped."""
    return json.dumps({"code": code})


def _tool_artifact(trace, content):
    """The tool-result artifact holding `content`."""
    hits = [a for a in trace["artifacts"]
            if a["artifact_id"].startswith("t") and a["content"] == content]
    assert len(hits) == 1, f"expected one tool artifact holding {content!r}, got {hits}"
    return hits[0]


def _assert_model_text(trace, content):
    art = _tool_artifact(trace, content)
    assert art["kind"] not in ROOTS, (
        f"{art['artifact_id']} holds text the model wrote and is recorded as "
        f"{art['kind']!r} - evidence. _meta: "
        f"{ {k: v for k, v in trace['_meta'].items() if isinstance(v, list) and v} }")


def _one_call_run(tool_name, args, output, answer):
    return [QUESTION,
            {"role": "assistant", "content": None,
             "tool_calls": [_call(tool_name, args, "c1")]},
            _tool(output, tool_name, "c1"),
            {"role": "assistant", "content": answer}]


# --- _is_whole_literal: literal shapes the neighbour-character sets reject ---

@pytest.mark.parametrize("code, output", [
    # In the JSON string the newline is the two characters `\n`, so the char
    # after `\"B\"` is a backslash - not in _AFTER_LITERAL. The most common
    # CodeAct shape there is.
    pytest.param('answer = "B"\nfinal_answer(answer)', "B", id="literal-in-variable"),
    pytest.param("final_answer(156391)", "156391", id="unquoted-number"),
    pytest.param('final_answer("Sydney".strip())', "Sydney", id="method-on-literal"),
    pytest.param('final_answer(r"Sydney")', "Sydney", id="raw-string-prefix"),
    pytest.param('final_answer(f"Sydney")', "Sydney", id="f-string-prefix"),
    pytest.param('final_answer("""Sydney""")', "Sydney", id="triple-quoted"),
    pytest.param('final_answer(\n    "Sydney"\n)', "Sydney", id="literal-on-own-line"),
    # Longer than ECHO_MIN_CHARS, so the length floor does not save it: the
    # Python escape `\'` is not one of the spellings json.dumps produces.
    pytest.param("final_answer('Hawai\\'i Volcanoes National Park')",
                 "Hawai'i Volcanoes National Park", id="python-escaped-apostrophe"),
])
def test_literal_the_model_typed_is_not_evidence(code, output):
    trace = oc.to_trace(_one_call_run("python", _code(code), output, output))
    _assert_model_text(trace, output)


# --- spellings other JSON encoders use ----------------------------------------

def test_go_encoder_html_escapes_are_still_the_models_text():
    # Go's encoding/json writes & < > as & < > by default.
    line = "R&D spending <per the model> rose 12%"
    args = (json.dumps({"text": line})
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))
    trace = oc.to_trace(_one_call_run("echo", args, line, "done"))
    _assert_model_text(trace, line)


def test_escaped_slash_is_still_the_models_text():
    # PHP's json_encode and several Java libraries write / as \/.
    line = "See https://example.com/reports/2025"
    args = json.dumps({"text": line}).replace("/", "\\/")
    trace = oc.to_trace(_one_call_run("echo", args, line, "done"))
    _assert_model_text(trace, line)


# --- ESCAPE_CHARS: above it only the raw spelling is compared -----------------

LIMIT = getattr(oc, "ESCAPE_CHARS", 8192)


def test_long_echoed_line_with_one_quote_is_not_evidence():
    head = 'Sydney is the "capital". '
    line = head + "x" * (LIMIT + 1 - len(head))
    args = json.dumps({"cmd": f"echo '{line}'"})
    trace = oc.to_trace(_one_call_run("bash", args, line, "done"))
    _assert_model_text(trace, line)


def test_long_non_ascii_line_logged_with_ensure_ascii_is_not_evidence():
    line = ("Столица Австралии - Сидней. " * (LIMIT // 20))[:LIMIT + 1]
    assert len(line) > LIMIT and line == line.strip()
    args = json.dumps({"text": line})          # ensure_ascii=True, the default
    trace = oc.to_trace(_one_call_run("save_and_show", args, line, "done"))
    _assert_model_text(trace, line)


# --- matching a result to its call without ids --------------------------------

def test_same_tool_in_parallel_results_in_completion_order():
    calls = [_call("python", _code("import math\nprint(math.factorial(20))")),
             _call("python", _code(f'print("{ECHO}")'))]
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": None, "tool_calls": calls},
        _tool(ECHO, "python"),                  # the second call finished first
        _tool("2432902008176640000", "python"),
        {"role": "assistant", "content": ECHO},
    ])
    _assert_model_text(trace, ECHO)


def test_positional_guess_swapped_without_flags():
    calls = [_call("web_search", json.dumps({"q": "capital of australia"})),
             _call("python", _code(f'print("{ECHO}")'))]
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": None, "tool_calls": calls},
        _tool(ECHO),
        _tool("Canberra is the capital city of Australia. (wikipedia)"),
        {"role": "assistant", "content": ECHO},
    ])
    _assert_model_text(trace, ECHO)


def test_run_ending_in_a_tool_result_does_not_elect_an_earlier_thought():
    thought = "I will submit the answer now."
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": thought,
         "tool_calls": [_call("python", _code(
             'city = "Sydney"\nprint(f"The capital of Australia is {city}")'), "c1")]},
        _tool(ECHO, "python", "c1"),
    ])
    answers = [a["content"] for a in trace["artifacts"] if a["kind"] == "final_answer"]
    assert answers != [thought], (
        "the log ends with a tool result, and the reader elected the thought before "
        "it as what the user saw; the text the user actually saw is recorded as "
        f"{_tool_artifact(trace, ECHO)['kind']!r}")

# ---------------------------------------------------------------------------
# Round 19: the echo detection is back as a note that moves no exit code.
# Put back from e69a6bc, where round 18 removed them with the detection.
# Tests of the confirmation gate stay out; an exit of 1 became 0.
# ---------------------------------------------------------------------------


def test_note_read_back_in_a_later_turn():
    # The module docstring names a notes store as a tool that hands the model's
    # words back; read_note's own arguments are empty, so the rule never looks.
    #
    # Relaxed after the proverka3 review, by the scheme agreed there: an echo
    # from an earlier turn is not demoted blindly, because the same match is
    # also a tool confirming what the model guessed. Either outcome passes -
    # read as model text, or named in _meta - exactly as in
    # test_openai_chat_turn_width.py group B. What fails is silence.
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": None,
         "tool_calls": [_call("save_note", json.dumps({"text": ECHO}), "c1")]},
        _tool("saved", "save_note", "c1"),
        {"role": "assistant", "content": None,
         "tool_calls": [_call("read_note", "{}", "c2")]},
        _tool(ECHO, "read_note", "c2"),
        {"role": "assistant", "content": ECHO},
    ])
    art = _tool_artifact(trace, ECHO)
    label = f"tool[{art['artifact_id'][1:]}]"
    named = [entry for value in trace["_meta"].values() if isinstance(value, list)
             for entry in value if isinstance(entry, str) and label in entry]
    assert art["kind"] not in ROOTS or named, (
        f"{label} hands back a note the model saved in an earlier turn; it is read "
        f"as {art['kind']!r} and nothing in _meta names it")
