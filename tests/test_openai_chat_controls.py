"""Controls for the failing tests in test_openai_chat_laundering.py.

Every test here passes on v0.7.5 and must keep passing after the fix. Each one
sits next to a failing case and pins what the reader already gets right there,
in both directions:

- echoes the rule catches today stay the model's text, so a fix does not lose
  the shapes that work while adding the ones that do not;
- real tool output stays evidence, so the failing tests cannot be made green by
  reading every tool result as the model's own words - which would pass all of
  them and leave the audit nothing to stop on.
"""

from __future__ import annotations

import json

from tallystick.adapters import openai_chat as oc

ROOTS = {"document", "tool_result"}
QUESTION = {"role": "user", "content": "What is the capital of Australia?"}
ECHO = "The capital of Australia is Sydney"
COMPUTED = "2432902008176640000"
LIMIT = getattr(oc, "ESCAPE_CHARS", 8192)


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


def _code(code: str, ensure_ascii: bool = True) -> str:
    return json.dumps({"code": code}, ensure_ascii=ensure_ascii)


def _kind(trace, content):
    hits = [a for a in trace["artifacts"]
            if a["artifact_id"].startswith("t") and a["content"] == content]
    assert len(hits) == 1, f"expected one tool artifact holding {content!r}, got {hits}"
    return hits[0]["kind"]


def _one_call_run(tool_name, args, output, answer="done"):
    return [QUESTION,
            {"role": "assistant", "content": None,
             "tool_calls": [_call(tool_name, args, "c1")]},
            _tool(output, tool_name, "c1"),
            {"role": "assistant", "content": answer}]


# --- echoes caught today: neighbours of the literal-shape failures ------------

def test_quoted_literal_in_final_answer_is_model_text_and_is_reported():
    trace = oc.to_trace(_one_call_run("python", _code('final_answer("B")'), "B", "B"))
    assert _kind(trace, "B") == "intermediate"
    assert trace["_meta"]["echoed_back_tool_results"] == ["tool[2] (python): B"]


def test_single_quoted_literal_is_model_text():
    trace = oc.to_trace(_one_call_run("python", _code("final_answer('Ottawa')"), "Ottawa"))
    assert _kind(trace, "Ottawa") == "intermediate"


def test_literal_with_escaped_quotes_inside_is_model_text():
    # Two levels of escaping: the model's code escapes the quotes, the JSON
    # string escapes them again.
    line = 'Episode 6, "A New Member of the Wolfpack"'
    code = 'final_answer("Episode 6, \\"A New Member of the Wolfpack\\"")'
    trace = oc.to_trace(_one_call_run("python", _code(code), line))
    assert _kind(trace, line) == "intermediate"


def test_short_non_ascii_literal_logged_with_ensure_ascii_is_model_text():
    trace = oc.to_trace(_one_call_run("python", _code('final_answer("Zürich")'), "Zürich"))
    assert _kind(trace, "Zürich") == "intermediate"


def test_literal_in_anthropic_object_arguments_is_model_text():
    # Anthropic sends `input` as an object; the reader dumps it to text.
    trace = oc.to_trace(_one_call_run("python", {"code": 'final_answer("Ottawa")'}, "Ottawa"))
    assert _kind(trace, "Ottawa") == "intermediate"


def test_long_unquoted_line_found_in_arguments_is_model_text():
    code = f'print("Result:")\nprint("{ECHO}")'
    trace = oc.to_trace(_one_call_run("python", _code(code), f"Result:\n{ECHO}"))
    assert _kind(trace, f"Result:\n{ECHO}") == "intermediate"


# --- real tool output stays evidence ------------------------------------------

def test_computed_value_is_evidence():
    code = "import math\nprint(math.factorial(20))"
    trace = oc.to_trace(_one_call_run("python", _code(code), COMPUTED))
    assert _kind(trace, COMPUTED) == "tool_result"


def test_search_result_that_echoes_the_query_in_its_header_is_evidence():
    # Why the rule looks at the LAST line: the header quotes the query back.
    result = "Search results for: capital of australia\nCanberra is the capital city of Australia."
    trace = oc.to_trace(_one_call_run(
        "web_search", json.dumps({"q": "capital of australia"}), result))
    assert _kind(trace, result) == "tool_result"


def test_short_result_not_in_the_arguments_is_evidence():
    trace = oc.to_trace(_one_call_run("weather", json.dumps({"city": "Paris"}), "Sunny"))
    assert _kind(trace, "Sunny") == "tool_result"


def test_declared_verbatim_tool_is_a_document():
    page = "Canberra is the capital city of Australia.\nPopulation: 467,000."
    trace = oc.to_trace(_one_call_run("read_file", json.dumps({"path": "au.txt"}), page),
                        verbatim_tools={"read_file"})
    assert _kind(trace, page) == "document"


# --- neighbours of the ESCAPE_CHARS failures -----------------------------------

def test_echoed_line_with_a_quote_at_the_escape_limit_is_model_text():
    head = 'Sydney is the "capital". '
    line = head + "x" * (LIMIT - len(head))
    trace = oc.to_trace(_one_call_run("bash", json.dumps({"cmd": f"echo '{line}'"}), line))
    assert _kind(trace, line) == "intermediate"


def test_long_echoed_line_needing_no_escaping_is_model_text():
    line = "Sydney is the capital " + "x" * (LIMIT * 2)
    trace = oc.to_trace(_one_call_run("bash", json.dumps({"cmd": f"echo '{line}'"}), line))
    assert _kind(trace, line) == "intermediate"


def test_non_ascii_line_logged_with_ensure_ascii_under_the_limit_is_model_text():
    line = "Столица Австралии - Сидней, по мнению модели"
    trace = oc.to_trace(_one_call_run("save_and_show", json.dumps({"text": line}), line))
    assert _kind(trace, line) == "intermediate"


def test_non_ascii_line_logged_without_escaping_is_model_text():
    line = "Столица Австралии - Сидней, по мнению модели"
    args = json.dumps({"text": line}, ensure_ascii=False)
    trace = oc.to_trace(_one_call_run("save_and_show", args, line))
    assert _kind(trace, line) == "intermediate"


# --- neighbours of the matching failures ---------------------------------------

def _parallel(results, with_ids):
    calls = [_call("python", _code("import math\nprint(math.factorial(20))"),
                   "c1" if with_ids else None),
             _call("python", _code(f'print("{ECHO}")'), "c2" if with_ids else None)]
    return oc.to_trace([QUESTION,
                        {"role": "assistant", "content": None, "tool_calls": calls},
                        *results,
                        {"role": "assistant", "content": ECHO}])


def test_same_tool_in_parallel_without_ids_in_call_order():
    trace = _parallel([_tool(COMPUTED, "python"), _tool(ECHO, "python")], with_ids=False)
    assert (_kind(trace, COMPUTED), _kind(trace, ECHO)) == ("tool_result", "intermediate")


def test_same_tool_in_parallel_with_ids_in_completion_order():
    trace = _parallel([_tool(ECHO, "python", "c2"), _tool(COMPUTED, "python", "c1")],
                      with_ids=True)
    assert (_kind(trace, COMPUTED), _kind(trace, ECHO)) == ("tool_result", "intermediate")


def test_note_store_declared_by_the_operator_is_model_text():
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": None,
         "tool_calls": [_call("save_note", json.dumps({"text": ECHO}), "c1")]},
        _tool("saved", "save_note", "c1"),
        {"role": "assistant", "content": None,
         "tool_calls": [_call("read_note", "{}", "c2")]},
        _tool(ECHO, "read_note", "c2"),
        {"role": "assistant", "content": ECHO},
    ], model_text_tools={"read_note"})
    assert _kind(trace, ECHO) == "intermediate"


# --- neighbours of the run-ending-in-a-tool failure ----------------------------

def _answers(trace):
    return [a["content"] for a in trace["artifacts"] if a["kind"] == "final_answer"]


def test_run_ending_in_a_tool_that_hands_its_argument_back_elects_that_text():
    trace = oc.to_trace([
        QUESTION,
        {"role": "assistant", "content": "I will submit the answer now.",
         "tool_calls": [_call("submit_answer", json.dumps({"answer": ECHO}), "c1")]},
        _tool(ECHO, "submit_answer", "c1"),
    ])
    assert _answers(trace) == [ECHO]


def test_assistant_message_after_the_last_tool_result_is_the_answer():
    trace = oc.to_trace(_one_call_run(
        "web_search", json.dumps({"q": "capital of australia"}),
        "Canberra is the capital city of Australia.", "Canberra."))
    assert _answers(trace) == ["Canberra."]
