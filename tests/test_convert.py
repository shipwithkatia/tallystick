"""Reading a log this project did not write.

Two readers - an OpenAI chat message list and an OpenTelemetry GenAI span
export - and one rule they share: what the reader leaves out is written down,
and nothing is converted on a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tallystick import load_run
from tallystick.auditability import check_trace
from tallystick.adapters import openai_chat, otel_genai
from tallystick.cli import main
from tallystick.convert import detect, hint_for, read_any
from tallystick.types import TraceError

CHAT = [
    {"role": "system", "content": "Cite everything."},
    {"role": "user", "content": "What was revenue?"},
    {"role": "assistant", "content": "Looking it up.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "search", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "Revenue was $4.2B."},
    {"role": "assistant", "content": "Revenue was $4.2B."},
]


def kinds(trace):
    return [(a["artifact_id"], a["kind"]) for a in trace["artifacts"]]


# ----------------------------------------------------------- the OpenAI reader

def test_the_shape_a_person_has_on_disk_loads():
    trace = openai_chat.to_trace(CHAT)
    assert load_run(trace)                      # it is a trace, not a shape
    assert kinds(trace) == [
        ("m0", "document"), ("m1", "document"),
        ("a2", "intermediate"), ("t3", "tool_result"), ("a4", "final_answer")]


def test_a_bare_list_a_messages_key_and_a_response_object_all_load():
    a = openai_chat.to_trace(CHAT)
    b = openai_chat.to_trace({"messages": CHAT})
    assert kinds(a) == kinds(b)
    resp = {"choices": [{"message": {"role": "assistant", "content": "Hello."}}]}
    assert kinds(openai_chat.to_trace(resp)) == [("a0", "final_answer")]


def test_a_step_sees_everything_recorded_before_it():
    """Each API call carries the whole conversation, so that is the input. A
    narrower claim would let a citation to something the step never had pass."""
    trace = openai_chat.to_trace(CHAT)
    by_id = {s["step_id"]: s for s in trace["steps"]}
    assert by_id["a2"]["inputs"] == ["m0", "m1"]
    assert by_id["a2.tools"]["inputs"] == ["m0", "m1", "a2"]
    assert by_id["a4"]["inputs"] == ["m0", "m1", "a2", "t3"]


def test_the_last_model_text_is_the_answer_and_only_that_one():
    trace = openai_chat.to_trace(CHAT)
    finals = [a for a in trace["artifacts"] if a["kind"] == "final_answer"]
    assert [a["artifact_id"] for a in finals] == ["a4"]
    assert [s["kind"] for s in trace["steps"] if s["outputs"] == ["a4"]] == ["answer"]


def test_a_tool_that_hands_the_models_words_back_is_not_a_root():
    """The whole point: a `final_answer` tool did not bring anything in from
    outside, so recording it as a root would launder the model's text."""
    chat = CHAT[:3] + [
        {"role": "tool", "tool_call_id": "c1", "name": "final_answer",
         "content": "Revenue was $4.2B."}]
    plain = openai_chat.to_trace(chat)
    assert plain["artifacts"][-1]["kind"] == "tool_result"

    told = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert told["artifacts"][-1]["kind"] == "final_answer"   # model text, and last
    assert any(s["outputs"] == ["t3"] and s["kind"] == "answer"
               for s in told["steps"])


def test_a_tool_that_returns_a_page_verbatim_is_a_document():
    chat = CHAT[:3] + [{"role": "tool", "tool_call_id": "c1", "name": "read_file",
                        "content": "page text"}]
    told = openai_chat.to_trace(chat, verbatim_tools={"read_file"})
    assert told["artifacts"][-1]["kind"] == "document"


def test_a_tool_cannot_be_both_kinds_at_once():
    with pytest.raises(ValueError, match="cannot be both"):
        openai_chat.to_trace(CHAT, model_text_tools={"x"}, verbatim_tools={"x"})


def test_a_cut_tool_result_is_recorded_as_cut():
    chat = CHAT[:3] + [{"role": "tool", "tool_call_id": "c1", "content": "x" * 50}]
    trace = openai_chat.to_trace(chat, max_tool_chars=10)
    assert trace["artifacts"][-1]["content"] == "x" * 10
    assert trace["_meta"]["truncated"] == ["t3"]


def test_nothing_is_dropped_in_silence():
    """A reader that quietly loses a message makes the recording look better
    than it is, which is the one failure this project exists to catch."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "   "},
            {"role": "narrator", "content": "meanwhile"},
            {"role": "assistant", "content": "a"}]
    meta = openai_chat.to_trace(chat)["_meta"]
    assert meta["skipped_empty"] == ["assistant[1]"]
    assert meta["dropped_messages"] == ["narrator[2]"]


def test_content_parts_are_read_and_non_text_parts_are_not_invented():
    chat = [{"role": "user", "content": [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}}]},
        {"role": "assistant", "content": "a cat"}]
    trace = openai_chat.to_trace(chat)
    assert trace["artifacts"][0]["content"] == "look at this"


def test_a_log_with_no_model_text_has_no_answer_to_audit_back_from():
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r"}]
    trace = openai_chat.to_trace(chat)
    assert not [a for a in trace["artifacts"] if a["kind"] == "final_answer"]


def test_it_refuses_what_is_not_a_message_list():
    for bad in ({"artifacts": []}, [], {"messages": "no"}, 3):
        with pytest.raises(ValueError):
            openai_chat.to_trace(bad)


# ------------------------------------------------------------- the OTel reader

def _span(name, start, attrs, events=None):
    span = {"name": name, "startTimeUnixNano": str(start),
            "attributes": [{"key": k, "value": {"stringValue":
                            v if isinstance(v, str) else json.dumps(v)}}
                           for k, v in attrs.items()]}
    if events:
        span["events"] = events
    return span


IN1 = [{"role": "system", "parts": [{"type": "text", "content": "Cite."}]},
       {"role": "user", "parts": [{"type": "text", "content": "Revenue?"}]}]
OUT1 = [{"role": "assistant", "parts": [
    {"type": "text", "content": "Looking."},
    {"type": "tool_call", "id": "c1", "name": "search", "arguments": {}}]}]
IN2 = IN1 + OUT1 + [{"role": "user", "parts": [
    {"type": "tool_call_response", "id": "c1", "response": "Revenue was $4.2B."}]}]
OUT2 = [{"role": "assistant", "parts": [{"type": "text", "content": "It was $4.2B."}]}]


def test_a_span_export_becomes_the_same_shape_as_a_chat_log():
    doc = {"resourceSpans": [{"scopeSpans": [{"spans": [
        _span("chat", 100, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN1,
                            "gen_ai.output.messages": OUT1}),
        _span("chat", 200, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN2,
                            "gen_ai.output.messages": OUT2})]}]}]}
    trace = otel_genai.to_trace(doc)
    assert load_run(trace)
    assert [a["kind"] for a in trace["artifacts"]] == [
        "document", "document", "intermediate", "tool_result", "final_answer"]
    assert trace["_meta"]["otel"]["reading"] == [
        "gen_ai.input.messages/output.messages"]


def test_spans_out_of_order_still_read_as_the_run_that_happened():
    late = _span("chat", 200, {"gen_ai.operation.name": "chat",
                               "gen_ai.input.messages": IN2,
                               "gen_ai.output.messages": OUT2})
    early = _span("chat", 100, {"gen_ai.operation.name": "chat",
                                "gen_ai.input.messages": IN1,
                                "gen_ai.output.messages": OUT1})
    a = otel_genai.to_trace({"spans": [late, early]})
    b = otel_genai.to_trace({"spans": [early, late]})
    assert [x["content"] for x in a["artifacts"]] == \
           [x["content"] for x in b["artifacts"]]


def test_the_repeated_history_on_every_span_is_not_recorded_twice():
    doc = {"spans": [
        _span("chat", 100, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN1,
                            "gen_ai.output.messages": OUT1}),
        _span("chat", 200, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN2,
                            "gen_ai.output.messages": OUT2})]}
    contents = [a["content"] for a in otel_genai.to_trace(doc)["artifacts"]]
    assert contents.count("Cite.") == 1
    assert contents.count("Looking.") == 1


def test_a_rewritten_history_is_reported_not_hidden():
    """If a later span's prompt does not extend the earlier one, the run is not
    one growing conversation and a step's inputs are not what they look like."""
    other = [{"role": "user", "parts": [{"type": "text", "content": "Different."}]},
             {"role": "assistant", "parts": [{"type": "text", "content": "OK."}]}]
    doc = {"spans": [
        _span("chat", 100, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN1,
                            "gen_ai.output.messages": OUT1}),
        _span("chat", 200, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": other,
                            "gen_ai.output.messages": OUT2})]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("does not extend the earlier one" in n for n in notes)


def test_the_indexed_attributes_openllmetry_writes_are_read():
    doc = [{"name": "openai.chat", "startTimeUnixNano": "1", "attributes": {
        "gen_ai.prompt.0.role": "system", "gen_ai.prompt.0.content": "Cite.",
        "gen_ai.prompt.1.role": "user", "gen_ai.prompt.1.content": "Revenue?",
        "gen_ai.completion.0.role": "assistant",
        "gen_ai.completion.0.content": "It was $4.2B."}}]
    trace = otel_genai.to_trace(doc)
    assert [a["kind"] for a in trace["artifacts"]] == [
        "document", "document", "final_answer"]
    assert trace["_meta"]["otel"]["reading"] == ["gen_ai.prompt.N/completion.N"]


def test_the_older_event_convention_is_read():
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "Revenue?"}},
        {"name": "gen_ai.choice", "attributes": {
            "content": json.dumps({"message": {"content": "It was $4.2B."}})}}])]}
    trace = otel_genai.to_trace(doc)
    assert [a["kind"] for a in trace["artifacts"]] == ["document", "final_answer"]
    assert trace["_meta"]["otel"]["reading"] == ["gen_ai events"]


def test_an_execute_tool_span_becomes_a_tool_result():
    doc = {"spans": [
        _span("chat", 100, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN1,
                            "gen_ai.output.messages": OUT1}),
        _span("execute_tool search", 150, {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search", "gen_ai.tool.call.id": "c1",
            "gen_ai.tool.call.result": "Revenue was $4.2B."}),
        _span("chat", 200, {"gen_ai.operation.name": "chat",
                            "gen_ai.input.messages": IN1 + OUT1,
                            "gen_ai.output.messages": OUT2})]}
    trace = otel_genai.to_trace(doc)
    assert [a["kind"] for a in trace["artifacts"]] == [
        "document", "document", "intermediate", "tool_result", "final_answer"]


def test_content_is_opt_in_and_its_absence_is_said_plainly():
    """The commonest real case: spans with model names, latencies and token
    counts, and no text at all. That is not an unauditable agent - it is a log
    with nothing in it, and saying so is the only honest answer."""
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat",
                                       "gen_ai.request.model": "gpt-4o"})]}
    with pytest.raises(ValueError, match="Opt-In"):
        otel_genai.to_trace(doc)


def test_it_refuses_what_is_not_a_span_export():
    with pytest.raises(ValueError, match="no spans"):
        otel_genai.to_trace({"nothing": 1})


# ------------------------------------------------------------ choosing a reader

def test_a_tallystick_trace_is_never_re_read_by_an_adapter():
    trace = {"artifacts": [], "steps": []}
    assert detect(trace) == ["tallystick"]
    assert read_any(trace, source="auto")[1] == "tallystick"


def test_auto_reads_an_unmistakable_log_and_says_which_reader():
    raw, source = read_any(CHAT, source="auto")
    assert source == "openai"
    assert load_run(raw)


def test_auto_refuses_a_file_two_readers_could_claim():
    """A trace read the wrong way produces a confident audit of a run that did
    not happen, which is worse than an error."""
    both = {"messages": CHAT,
            "spans": [_span("chat", 1, {"gen_ai.prompt.0.role": "user",
                                        "gen_ai.prompt.0.content": "hi"})]}
    assert len(detect(both)) == 2
    with pytest.raises(TraceError, match="Say which it is"):
        read_any(both, source="auto")


def test_a_file_no_reader_knows_is_refused_with_somewhere_to_go():
    with pytest.raises(TraceError, match="auditable-traces"):
        read_any({"log": ["hello"]}, source="auto")


def test_asking_for_the_native_reader_on_a_chat_log_names_the_right_flag():
    with pytest.raises(TraceError, match="--from openai"):
        read_any(CHAT, source="tallystick")


# ------------------------------------------------------------------------- CLI

def _write(tmp_path, name, obj):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def test_convert_writes_a_trace_that_loads(tmp_path, capsys):
    src = _write(tmp_path, "chat.json", CHAT)
    out = str(tmp_path / "trace.json")
    assert main(["convert", src, "-o", out, "--from", "openai"]) == 0
    assert load_run(json.loads(Path(out).read_text(encoding="utf-8")))
    printed = capsys.readouterr().out
    assert "read as openai" in printed
    assert "check-trace" in printed


def test_check_trace_reads_a_chat_log_directly(tmp_path, capsys):
    src = _write(tmp_path, "chat.json", CHAT)
    assert main(["check-trace", src, "--from", "openai"]) == 0
    assert "Read as openai" in capsys.readouterr().out


def test_a_foreign_log_is_exit_2_not_a_verdict(tmp_path, capsys):
    """Exit 1 says 'this trace is bad'. A file we cannot read is exit 2, and
    the difference is the whole reason the codes exist."""
    src = _write(tmp_path, "mystery.json", {"log": ["hello"]})
    assert main(["check-trace", src]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_the_error_on_a_chat_log_says_which_flag_to_use(tmp_path, capsys):
    src = _write(tmp_path, "chat.json", CHAT)
    main(["check-trace", src, "--from", "tallystick"])
    assert "--from openai" in capsys.readouterr().err


def test_convert_refuses_to_write_a_file_it_could_not_read(tmp_path):
    src = _write(tmp_path, "mystery.json", {"log": ["hello"]})
    out = tmp_path / "trace.json"
    assert main(["convert", src, "-o", str(out)]) == 2
    assert not out.exists()


def test_the_tool_flags_reach_the_reader_from_the_command_line(tmp_path):
    chat = CHAT[:3] + [{"role": "tool", "tool_call_id": "c1",
                        "name": "final_answer", "content": "Revenue was $4.2B."}]
    src = _write(tmp_path, "chat.json", chat)
    out = str(tmp_path / "trace.json")
    assert main(["convert", src, "-o", out, "--from", "openai",
                 "--tool-returns-model-text", "final_answer"]) == 0
    trace = json.loads(Path(out).read_text(encoding="utf-8"))
    assert trace["artifacts"][-1]["kind"] == "final_answer"


# ------------------------- what adversarial review found, kept from coming back

def test_an_anthropic_tool_result_is_not_a_root():
    """The Messages API returns a tool's output as a `tool_result` block on a
    USER message. Read naively that is a `document` - the strongest root - so
    the audit would take a tool's digest for external evidence. This is the
    project's own named failure, committed by its own reader, and it was."""
    chat = [
        {"role": "user", "content": "Who won?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I'll check."},
            {"type": "tool_use", "id": "tu1", "name": "final_answer",
             "input": {"answer": "Jorge Mendez."}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text": "Jorge Mendez."}]}]},
        {"role": "assistant", "content": "Jorge Mendez."},
    ]
    plain = openai_chat.to_trace(chat)
    # Never a `document`: that is the failure this test was written for.
    assert plain["artifacts"][2]["kind"] != "document"
    # And since v0.7.5 the reader does not need to be told about this one: the
    # result is the literal the model sent in, so it is read as the model's own
    # text without any flag. The result of the operator declaring the tool is
    # the same, which is the point - the flag stays necessary only for tools
    # that echo without quoting.
    assert [a["kind"] for a in plain["artifacts"]] == [
        "document", "intermediate", "intermediate", "final_answer"]
    assert plain["_meta"]["echoed_back_tool_results"]    # and it says so
    assert plain["_meta"]["notes"]                       # the rewriting is declared

    told = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert told["artifacts"][2]["kind"] == "intermediate"   # not evidence


def test_an_echo_is_caught_through_the_escaping_its_arguments_are_written_in():
    """Arguments arrive as a JSON string, so a quote in the text the model typed
    is escaped there. Comparing the raw line misses it - 26 of 154 echoes in
    AgentHallu, and this is one of them, copied from SmolAgents/045: the
    interpreter printed back a title containing quotation marks."""
    chat = [
        {"role": "user", "content": "Which episode?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "Code Action Space", "arguments":
             r'final_answer("Episode 6, \"A New Member of the Wolfpack\"")'}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": 'Episode 6, "A New Member of the Wolfpack"'},
        {"role": "assistant", "content": 'Episode 6, "A New Member of the Wolfpack"'},
    ]
    trace = openai_chat.to_trace(chat)
    assert trace["artifacts"][1]["kind"] == "intermediate"
    assert trace["_meta"]["echoed_back_tool_results"]


def test_a_short_echo_quoted_in_its_own_call_is_not_evidence():
    """Review pass 2 found 19 of these in AgentHallu and they were laundering:
    an interpreter printing back `final_answer("Ottawa")` was recorded as a
    root, and the answer - the same word - then quoted it and grounded. The
    length floor does not apply to a line the arguments carry in quotes."""
    chat = [
        {"role": "user", "content": "Capital of Canada?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "run", "arguments":
             '{"code": "final_answer(\\"Ottawa\\")"}'}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "Execution logs:\nLast output from code snippet:\nOttawa"},
        {"role": "assistant", "content": "Ottawa"},
    ]
    trace = openai_chat.to_trace(chat)
    assert trace["artifacts"][1]["kind"] == "intermediate"


def test_a_short_line_that_merely_appears_in_the_arguments_stays_a_root():
    """The other side of the exemption: unquoted, short and incidental is a
    coincidence, not an echo - `6` inside a list of numbers."""
    chat = [
        {"role": "user", "content": "How many?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "run", "arguments":
             '{"code": "distances = [3, 7, 8, 9, 11]; print(count(distances))"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "5"},
        {"role": "assistant", "content": "5"},
    ]
    assert openai_chat.to_trace(chat)["artifacts"][1]["kind"] == "tool_result"


def test_an_echo_is_caught_when_the_quotes_were_escaped_twice():
    """Asked by the first reader of this rule: what about quotes? In an OpenAI
    log the arguments are a JSON string, so quotes the model's own code already
    escaped are escaped again by the JSON layer. Comparing one level deep
    missed exactly this shape."""
    args = json.dumps({"code": 'final_answer("Episode 6, \\"A New Member\\"")'})
    chat = [
        {"role": "user", "content": "Which episode?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "run", "arguments": args}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": 'Episode 6, "A New Member"'},
        {"role": "assistant", "content": 'Episode 6, "A New Member"'},
    ]
    assert openai_chat.to_trace(chat)["artifacts"][1]["kind"] == "intermediate"


def test_an_echo_is_caught_whichever_way_the_log_escapes_non_ascii():
    """`json.dumps` defaults to ensure_ascii=True, so a log written by an
    ordinary Python wrapper spells `Muller` with \\u00fc. Comparing only the
    other spelling lost 21 of 154 echoes in AgentHallu, measured."""
    for args in ('{"code": "final_answer(\\"Caf\\u00e9 M\\u00fcller\\")"}',
                 '{"code": "final_answer(\\"Café Müller\\")"}'):
        chat = [
            {"role": "user", "content": "Which piece?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "run", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "Café Müller"},
            {"role": "assistant", "content": "Café Müller"},
        ]
        assert openai_chat.to_trace(chat)["artifacts"][1]["kind"] == "intermediate", args


def test_the_echo_test_runs_before_the_result_is_truncated():
    """Truncation is a prompt-size knob. Asking after it would hide the echoed
    value behind a mid-document line and record a root because a limit was low."""
    tail = 'Paris is the capital of France.'
    chat = [
        {"role": "user", "content": "Capital?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "run", "arguments":
             '{"code": "final_answer(\\"%s\\")"}' % tail}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 400 + "\n" + tail},
        {"role": "assistant", "content": tail},
    ]
    for limit in (20000, 100):
        trace = openai_chat.to_trace(chat, max_tool_chars=limit)
        assert trace["artifacts"][1]["kind"] == "intermediate", limit


def test_a_quoted_fragment_of_a_longer_string_is_not_a_literal():
    """Review pass 3: with the length floor waived for quoted matches, a tool
    result whose last line was a single comma was read as the model's own words,
    because `","` sits inside `{"city":"Paris","units":"metric"}`. A quoted
    match only counts where it is the whole of a value."""
    for args, line in (('{"city":"Paris","units":"metric"}', ","),
                       ('{"a":"b"}', ":"),
                       ('{"cols": {"x": 1, "y": 2}}', "x")):     # a KEY, not a value
        chat = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": args}}]},
            {"role": "tool", "tool_call_id": "c1", "content": line},
            {"role": "assistant", "content": "done"},
        ]
        assert openai_chat.to_trace(chat)["artifacts"][1]["kind"] == "tool_result", args


def test_a_declared_echo_and_a_detected_one_are_read_the_same_way():
    """Review pass 3: for one pass the detected echoes were barred from the
    answer election and the declared ones were not, so declaring the tool
    truthfully took the verdict from exit 1 to exit 0 - a flag whose purpose is
    to make the audit stricter turning a gate green. Whatever the rule is, it
    has to be the same rule for both."""
    chat = [
        {"role": "user", "content": "Capital?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "python", "arguments":
             '{"code": "final_answer(\\"Ottawa is the capital.\\")"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Ottawa is the capital."},
    ]
    detected = openai_chat.to_trace(chat)
    declared = openai_chat.to_trace(chat, model_text_tools={"python"})
    assert [a["kind"] for a in detected["artifacts"]] == \
           [a["kind"] for a in declared["artifacts"]]
    # and the detection is reported with the line it fired on, so the reader can
    # check the decision instead of taking it on trust
    assert detected["_meta"]["echoed_back_tool_results"] == \
        ["tool[2] (python): Ottawa is the capital."]
    assert declared["_meta"]["echoed_back_tool_results"] == []


def test_a_tool_result_that_is_not_in_its_own_call_stays_a_root():
    """The other half of the rule above, and the one that keeps it from eating
    real evidence: a search result the model did not write is a root, even
    though the query it answers is in the call's arguments."""
    chat = [
        {"role": "user", "content": "Who won?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "search",
             "input": {"query": "who won the 2019 final"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text":
                          "Results for who won the 2019 final\nJorge Mendez lifted the cup."}]}]},
        {"role": "assistant", "content": "Jorge Mendez."},
    ]
    trace = openai_chat.to_trace(chat)
    assert trace["artifacts"][1]["kind"] == "tool_result"
    assert not trace["_meta"]["echoed_back_tool_results"]


def test_a_nested_tool_result_body_is_read_not_called_empty():
    chat = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t",
         "content": [{"type": "text", "text": "PAGE TEXT"}]}]}]
    trace = openai_chat.to_trace(chat)
    assert trace["artifacts"][0]["content"] == "PAGE TEXT"
    assert not trace["_meta"]["skipped_empty"]


def test_a_tool_message_with_no_id_is_matched_by_position_and_said():
    """Some gateways omit tool_call_id. Without this the tool is nameless, and
    --tool-returns-model-text is silently inert on the logs that need it."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "final_answer", "arguments": "{}"}}]},
            {"role": "tool", "content": "invented answer"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert trace["artifacts"][-1]["kind"] == "final_answer"
    assert trace["_meta"]["guessed_tool_names"] == ["tool[2] -> final_answer"]


def test_the_pre_2024_function_call_field_still_names_its_result():
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": None,
             "function_call": {"name": "notes", "arguments": "{}"}},
            {"role": "function", "content": "what the model wrote earlier"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"notes"})
    assert trace["artifacts"][-1]["kind"] == "final_answer"


def test_two_tool_batches_under_one_turn_get_two_step_ids():
    """The report names steps; a name pointing at two things names neither."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "thinking"},
            {"role": "tool", "name": "a", "content": "r1"},
            {"role": "assistant", "content": ""},
            {"role": "tool", "name": "b", "content": "r2"}]
    ids = [s["step_id"] for s in openai_chat.to_trace(chat)["steps"]]
    assert len(ids) == len(set(ids))


def test_a_part_with_no_text_is_recorded_as_left_out():
    chat = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}}]},
        {"role": "assistant", "content": "a cat"}]
    dropped = openai_chat.to_trace(chat)["_meta"]["dropped_messages"]
    assert any("image_url" in d for d in dropped)


def test_a_prompt_turn_with_no_role_is_read_as_the_model_not_as_evidence():
    """OTel drops attributes past its per-span limit, and some exporters record
    content alone. Defaulting such a turn to `user` would make the model's own
    replayed turns into roots - laundering, by default, invisibly."""
    doc = [{"name": "openai.chat", "startTimeUnixNano": "1", "attributes": {
        "gen_ai.prompt.0.content": "You are an agent.",
        "gen_ai.prompt.1.content": "Who won?",
        "gen_ai.prompt.2.content": "I think Team A won, but I am not sure.",
        "gen_ai.completion.0.content": "Team A won."}}]
    trace = otel_genai.to_trace(doc)
    assert not [a for a in trace["artifacts"] if a["kind"] in ("document", "tool_result")]
    assert any("no role" in n for n in trace["_meta"]["otel"]["notes"])


def test_content_that_could_not_be_parsed_is_reported_not_blamed_on_the_user():
    """OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT cuts a serialised message list, leaving
    invalid JSON. Dropping it silently makes check-trace say the step declared
    no inputs - a finding against a recorder that did its job."""
    good = json.dumps([{"role": "user", "parts": [
        {"type": "text", "content": "a question long enough to be cut"}]}])
    doc = {"spans": [{"name": "chat", "startTimeUnixNano": "1", "attributes": {
        "gen_ai.input.messages": good[:40],
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "parts": [
            {"type": "text", "content": "an answer"}]}])}}]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("could not be parsed" in n for n in notes)


def test_the_recommended_span_name_still_names_the_tool():
    """The convention recommends the span name `execute_tool {name}`. Left
    whole it never matches --tool-returns-model-text, and a final_answer tool
    stays a root."""
    inp = [{"role": "user", "parts": [{"type": "text", "content": "q"}]}]
    out = [{"role": "assistant", "parts": [{"type": "text", "content": "calling"}]}]
    doc = {"spans": [
        _span("chat", 1, {"gen_ai.input.messages": inp,
                          "gen_ai.output.messages": out}),
        _span("execute_tool final_answer", 2, {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.call.result": "the invented answer"})]}
    trace = otel_genai.to_trace(doc, model_text_tools={"final_answer"})
    assert trace["artifacts"][-1]["kind"] == "final_answer"


def test_a_trimmed_prompt_does_not_put_the_same_text_in_twice():
    """A compacted prompt used to re-emit the turns it repeated. Two artifacts
    with the same text is the one thing a provenance audit cannot cope with: a
    quote can then be attributed to either."""
    S = {"role": "system", "parts": [{"type": "text", "content": "POLICY"}]}
    U1 = {"role": "user", "parts": [{"type": "text", "content": "U1"}]}
    A1 = {"role": "assistant", "parts": [{"type": "text", "content": "A1"}]}
    U2 = {"role": "user", "parts": [{"type": "text", "content": "U2"}]}
    A2 = {"role": "assistant", "parts": [{"type": "text", "content": "A2"}]}
    doc = {"spans": [
        _span("chat", 1, {"gen_ai.input.messages": [S, U1],
                          "gen_ai.output.messages": [A1]}),
        _span("chat", 2, {"gen_ai.input.messages": [U1, A1, U2],
                          "gen_ai.output.messages": [A2]})]}
    trace = otel_genai.to_trace(doc)
    contents = [a["content"] for a in trace["artifacts"]]
    assert contents == ["POLICY", "U1", "A1", "U2", "A2"]
    assert any("trimmed, compacted or reordered" in n
               for n in trace["_meta"]["otel"]["notes"])
    # A sliding window aligns exactly, so nothing had to be thrown away.
    assert trace["_meta"]["otel"]["dropped_repeat_turns"] == 0


def test_a_choice_event_carrying_the_whole_choices_array_is_read():
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.choice", "attributes": {"content": json.dumps(
            [{"index": 0, "message": {"role": "assistant",
                                      "content": "the answer"}}])}}])]}
    assert [a["kind"] for a in otel_genai.to_trace(doc)["artifacts"]] == [
        "document", "final_answer"]


def test_one_untimed_span_does_not_reverse_the_whole_export():
    first = _span("chat", 100, {"gen_ai.prompt.0.role": "user",
                                "gen_ai.prompt.0.content": "FIRST"})
    untimed = {"name": "chat", "attributes": {"gen_ai.prompt.0.role": "user",
                                              "gen_ai.prompt.0.content": "MIDDLE"}}
    last = _span("chat", 300, {"gen_ai.prompt.0.role": "user",
                               "gen_ai.prompt.0.content": "LAST"})
    trace = otel_genai.to_trace({"spans": [last, first, untimed]})
    assert [a["content"] for a in trace["artifacts"]] == ["FIRST", "MIDDLE", "LAST"]


def test_a_log_that_calls_its_own_list_steps_still_finds_its_reader():
    """LangGraph and smolagents both write a `steps` key. Claiming it on the
    key alone produced 'steps[0] is missing step_id' - the 'yours is not a
    trace' bounce this release exists to remove."""
    log = {"steps": [{"name": "plan"}],
           "messages": [{"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"}]}
    # It still claims to be a trace on the key, so load_run still reports the
    # malformed field - which is the most useful thing it can say about a file
    # that really is a broken trace. What must not happen is that the user is
    # left there.
    hint = hint_for(log)
    assert "OpenAI chat message list" in hint
    # And not as a recommendation: the file carries a trace's own key too, so
    # reading it as a chat log would silently ignore whatever `steps` holds.
    assert "check which of the two it is" in hint


def test_a_malformed_native_trace_still_gets_its_precise_error(tmp_path, capsys):
    src = _write(tmp_path, "broken.json",
                 {"artifacts": [{"id": "a1", "kind": "document", "content": "x"}]})
    assert main(["check-trace", src]) == 2
    err = capsys.readouterr().err
    assert "artifact_id" in err          # the field, not a shrug
    assert "--from" not in err           # and no reader would take it


def test_check_trace_says_what_the_reading_left_out(tmp_path, capsys):
    """A reader that lost a message and said nothing turns its own bug into a
    finding against the user's recorder."""
    chat = [{"role": "user", "content": "q"},
            {"role": "narrator", "content": "meanwhile"},
            {"role": "assistant", "content": "a"}]
    src = _write(tmp_path, "chat.json", chat)
    main(["check-trace", src, "--from", "openai"])
    out = capsys.readouterr().out
    assert "left out" in out and "narrator" in out


def test_a_turn_that_genuinely_repeats_is_not_deleted_in_silence():
    """A user really does say "yes" twice. The de-duplication that stops the
    same text entering the trace twice must not quietly eat a real turn: where
    it cannot align the histories, it says how many turns it dropped."""
    S = {"role": "system", "parts": [{"type": "text", "content": "S"}]}
    YES = {"role": "user", "parts": [{"type": "text", "content": "yes"}]}
    OK = {"role": "assistant", "parts": [{"type": "text", "content": "ok"}]}
    doc = {"spans": [
        _span("chat", 1, {"gen_ai.input.messages": [S, YES],
                          "gen_ai.output.messages": [OK]}),
        _span("chat", 2, {"gen_ai.input.messages": [YES, OK, YES],
                          "gen_ai.output.messages": [
                              {"role": "assistant",
                               "parts": [{"type": "text", "content": "done"}]}]})]}
    meta = otel_genai.to_trace(doc)["_meta"]["otel"]
    # The window slid, so this one aligns and loses nothing.
    assert meta["dropped_repeat_turns"] == 0

    other = [{"role": "user", "parts": [{"type": "text", "content": "yes"}]}]
    doc2 = {"spans": [
        _span("chat", 1, {"gen_ai.input.messages": [S, YES],
                          "gen_ai.output.messages": [OK]}),
        _span("chat", 2, {"gen_ai.input.messages": other,
                          "gen_ai.output.messages": [
                              {"role": "assistant",
                               "parts": [{"type": "text", "content": "done"}]}]})]}
    meta2 = otel_genai.to_trace(doc2)["_meta"]["otel"]
    assert meta2["dropped_repeat_turns"] == 1
    assert any("missing from this reading" in n for n in meta2["notes"])


def test_an_unreadable_prompt_does_not_get_blamed_on_the_recorder():
    """One cut attribute used to reorder the conversation and then report that
    the user's agent had rewritten its history. The cause was the reader."""
    good = json.dumps([{"role": "user", "parts": [
        {"type": "text", "content": "the real question, long enough to be cut"}]}])
    doc = {"spans": [
        _span("chat", 1, {"gen_ai.input.messages": good[:30],
                          "gen_ai.output.messages": json.dumps(
                              [{"role": "assistant",
                                "parts": [{"type": "text", "content": "A"}]}])}),
    ]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("could not be parsed" in n for n in notes)
    assert not any("trimmed, compacted or reordered" in n for n in notes)


def test_a_role_less_turn_on_the_current_convention_is_not_a_root_either():
    """The fix for this was first applied to only one of the two readings, and
    the newer one - the convention the docstring lists first - kept defaulting
    a role-less turn to `user`, which is a root."""
    doc = {"spans": [_span("chat", 1, {"gen_ai.input.messages": [
        {"parts": [{"type": "text", "content": "The capital of Atlantis is Poseidonis."}]}]})]}
    trace = otel_genai.to_trace(doc)
    assert not [a for a in trace["artifacts"]
                if a["kind"] in ("document", "tool_result")]
    assert trace["_meta"]["otel"]["roleless_turns"] == 1


def test_a_span_export_with_no_model_output_says_it_has_no_answer():
    """Otherwise the run's reported answer is the user's own question."""
    doc = [{"name": "chat", "startTimeUnixNano": "1", "attributes": {
        "gen_ai.prompt.0.role": "system", "gen_ai.prompt.0.content": "Be helpful.",
        "gen_ai.prompt.1.role": "user",
        "gen_ai.prompt.1.content": "What is the capital of France?"}}]
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("no span recorded model output" in n for n in notes)


def test_a_converted_file_still_says_what_the_reading_left_out(tmp_path, capsys):
    """`convert` writes its `_meta` into the trace precisely so the next command
    can repeat it. check-trace used to print none of it, on the very path that
    `convert` prints as the next step."""
    chat = [{"role": "user", "content": "q"},
            {"role": "narrator", "content": "meanwhile"},
            {"role": "assistant", "content": "a"}]
    src = _write(tmp_path, "chat.json", chat)
    out = str(tmp_path / "trace.json")
    main(["convert", src, "-o", out, "--from", "openai"])
    capsys.readouterr()
    main(["check-trace", out])
    printed = capsys.readouterr().out
    assert "left out" in printed and "narrator" in printed


def test_the_weakest_readers_own_disclaimer_reaches_the_verdict(tmp_path, capsys):
    doc = {"spans": [_span("chat", 1, {
        "gen_ai.prompt.0.role": "user", "gen_ai.prompt.0.content": "q",
        "gen_ai.completion.0.content": "a"})]}
    src = _write(tmp_path, "spans.json", doc)
    main(["check-trace", src, "--from", "otel"])
    assert "not against a corpus" in capsys.readouterr().out


def test_a_named_result_still_consumes_its_call():
    """The queue invariant is that EVERY tool message consumes one declared
    call. A result that already knew its own name used to skip the queue, and
    the next id-less result then inherited the slot it left - handing a search's
    name to a final_answer echo and recording the model's words as a root."""
    chat = [{"role": "user", "content": "Who won the 2019 final?"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "name": "search", "content": "[]"},
            {"role": "tool", "content": "Team A won, beating Team B 3-1."}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert trace["artifacts"][-1]["kind"] == "final_answer"
    assert trace["_meta"]["guessed_tool_names"] == ["tool[3] -> final_answer"]


def test_a_result_whose_id_matches_nothing_open_does_not_eat_a_slot():
    """It belongs to some other turn's call. Consuming here would shift every
    later name by one; ignoring it silently would hide that it arrived."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c", "tool_calls": [
                {"id": "c9", "type": "function",
                 "function": {"name": "final_answer", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "zzz", "content": "from earlier"},
            {"role": "tool", "content": "the invented answer"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert trace["artifacts"][-1]["kind"] == "final_answer"
    assert trace["_meta"]["unmatched_tool_results"] == ["tool[2] (id zzz)"]


@pytest.mark.parametrize("attrs", [{"content": None}, {}, {"content": [1, 2, 3]}])
def test_a_choice_event_that_yielded_nothing_is_not_model_output(attrs):
    """Otherwise an empty event suppresses the very warning it should raise."""
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.choice", "attributes": attrs}])]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("no span recorded model output" in n for n in notes)


def test_the_dropped_turn_count_is_turns_not_fittings():
    """A conversation with a repeating block fits the window more than one way.
    What is at stake is how many turns the shortest fitting would have added,
    not how many fittings there were."""
    from tallystick.adapters.otel_genai import _merge
    say = lambda xs: [{"role": "u", "content": x} for x in xs]  # noqa: E731
    assert _merge(say("ABABAB"), say("ABABC"))[1] == 2


def test_a_result_claims_its_own_call_by_name_when_there_are_no_ids():
    """A name identifies a call as well as an id does, and names are what
    survive a gateway that omits ids. Taking the head of the queue on a result
    that named itself put a final_answer echo under a search's name - on the
    evidence side of the line."""
    chat = [{"role": "user", "content": "Who won the 2019 final?"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "name": "search", "content": "no results found"},
            {"role": "tool", "content": "Team A won, beating Team B 3-1."}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert [(a["kind"], a["title"]) for a in trace["artifacts"][-2:]] == [
        ("tool_result", "search"), ("final_answer", "final_answer")]


def test_an_id_on_the_result_but_not_on_the_call_still_matches_by_position():
    """The legacy `function_call` field carries no id, so the call is declared
    without one. If a gateway then stamps an id on the result, keying the queue
    by id declares every result to belong to another turn, nothing consumes a
    slot, and every unnamed result becomes an unnamed root."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "x1", "content": "no results"},
            {"role": "tool", "tool_call_id": "x2",
             "content": "Team A won, beating Team B 3-1."}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    # Nothing in this log says which result answered which call - the ids on
    # the results name calls that were never declared - so neither result is
    # evidence, and neither is asserted to be what the user saw.
    assert not [a for a in trace["artifacts"]
                if a["kind"] in ("tool_result", "document") and "won" in a["content"]]
    assert trace["_meta"]["unresolved_tool_results"]
    assert trace["_meta"]["unmatched_tool_results"] == []


def test_a_replayed_assistant_event_is_not_this_calls_output():
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.assistant.message",
         "attributes": {"content": "replayed history"}}])]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("no span recorded model output" in n for n in notes)


def test_a_real_choice_beside_an_unplaceable_event_is_still_output():
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.choice", "attributes": {"content": "the answer"}},
        {"name": "gen_ai.choice", "attributes": {"content": None}}])]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert not any("no span recorded model output" in n for n in notes)


def test_a_result_that_matched_nothing_reaches_the_user(tmp_path, capsys):
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c", "tool_calls": [
                {"id": "c9", "type": "function",
                 "function": {"name": "search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "zzz", "content": "from earlier"},
            {"role": "assistant", "content": "done"}]
    src = _write(tmp_path, "chat.json", chat)
    main(["check-trace", src, "--from", "openai"])
    assert "matched nothing open" in capsys.readouterr().out


# ---------------------------------------------- the rule that replaced the guess

def test_an_unresolvable_result_is_not_recorded_as_evidence():
    """Five rounds of review found five ways for the positional guess to hand
    a tool's name to the wrong result and record the model's own words as
    evidence, each opened by the repair of the last. The guess no longer
    decides that question: where the log does not say which call a result
    answers and any open call echoes the model, the result is model text."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "The model's own answer."},
            {"role": "tool", "content": "no results found"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["The model's own answer."] != "tool_result"
    assert trace["_meta"]["unresolved_tool_results"]


def test_a_stray_id_from_an_earlier_turn_takes_no_slot():
    """Round six's critical finding: the guard that stops a foreign id eating
    a slot was scoped to the current turn's calls, so it switched off for
    exactly the turn shape it was written for."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "t1", "tool_calls": [
                {"id": "old1", "type": "function",
                 "function": {"name": "lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old1", "content": "cache miss"},
            {"role": "assistant", "content": "t2", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old1", "content": "cache miss 2"},
            {"role": "tool", "content": "The model's own answer."},
            {"role": "tool", "content": "no results found"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["The model's own answer."] != "tool_result"
    assert trace["_meta"]["unmatched_tool_results"] == ["tool[4] (id old1)"]


def test_no_interleaving_records_an_echo_tools_output_as_evidence():
    """The invariant, checked over every small interleaving rather than over
    the cases someone thought of. Two parallel calls, one of which hands the
    model's words back; ids and names present or absent on either side, in
    either order. In none of them may the echo's text end up on the evidence
    side - by a right match, or by refusing to guess."""
    import itertools

    echo_text = "THE MODEL'S OWN ANSWER"
    other_text = "no results found"
    failures = []
    for call_ids, res_ids, res_names, order in itertools.product(
            (False, True), (False, True), (False, True), ((0, 1), (1, 0))):
        calls = []
        for n, name in enumerate(("final_answer", "search")):
            call = {"type": "function", "function": {"name": name,
                                                     "arguments": "{}"}}
            if call_ids:
                call["id"] = f"c{n}"
            calls.append(call)
        results = []
        for n, text in ((0, echo_text), (1, other_text)):
            msg = {"role": "tool", "content": text}
            if res_ids:
                msg["tool_call_id"] = f"c{n}"
            if res_names:
                msg["name"] = ("final_answer", "search")[n]
            results.append(msg)
        chat = ([{"role": "user", "content": "q"},
                 {"role": "assistant", "content": "checking", "tool_calls": calls}]
                + [results[i] for i in order])
        trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
        kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
        if kinds.get(echo_text) in ("tool_result", "document"):
            failures.append((call_ids, res_ids, res_names, order,
                             kinds.get(echo_text)))
    assert not failures, f"the model's own text recorded as a root in {failures}"


# ------------------ the surfaces where false coverage kept hiding, now tested

def test_the_json_report_carries_what_the_reading_had_to_decide(tmp_path):
    """`--quiet --json` is the CI shape, and its own comment calls it the one
    place nobody is watching. Three rounds running, a commit said these keys
    reached it and they did not."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "The model's own answer."},
            {"role": "tool", "content": "no results found"}]
    src = _write(tmp_path, "chat.json", chat)
    out = str(tmp_path / "report.json")
    main(["check-trace", src, "--from", "openai", "--quiet", "--json", out,
          "--tool-returns-model-text", "final_answer"])
    reading = json.loads(Path(out).read_text(encoding="utf-8"))["reading"]
    assert reading["unresolved_tool_results"]
    assert reading["model_text_tools"] == ["final_answer"]
    assert "unmatched_tool_results" in reading


def test_an_unresolved_result_is_named_in_the_terminal(tmp_path, capsys):
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "The model's own answer."},
            {"role": "tool", "content": "no results found"}]
    src = _write(tmp_path, "chat.json", chat)
    main(["check-trace", src, "--from", "openai",
          "--tool-returns-model-text", "final_answer"])
    assert "unresolved" in capsys.readouterr().out


@pytest.mark.parametrize("body", ['{"message": {"content": ""}}',
                                  '{"message": {"content": "   "}}', ""])
def test_a_choice_that_said_nothing_is_not_output(body):
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.choice", "attributes": {"content": body}}])]}
    notes = otel_genai.to_trace(doc)["_meta"]["otel"]["notes"]
    assert any("no span recorded model output" in n for n in notes)


@pytest.mark.parametrize("chat,label", [
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "content": "c", "tool_calls": [
          {"id": "d", "type": "function",
           "function": {"name": "search", "arguments": "{}"}},
          {"id": "d", "type": "function",
           "function": {"name": "final_answer", "arguments": "{}"}}]},
      {"role": "tool", "tool_call_id": "d", "content": "external"},
      {"role": "tool", "tool_call_id": "d", "content": "ECHO"}],
     "a duplicate call id determines nothing"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "content": "t1", "tool_calls": [
          {"id": "a", "type": "function",
           "function": {"name": "lookup", "arguments": "{}"}}]},
      {"role": "tool", "tool_call_id": "a", "content": "miss"},
      {"role": "assistant", "content": "t2", "tool_calls": [
          {"type": "function", "function": {"name": "search", "arguments": "{}"}},
          {"type": "function", "function": {"name": "final_answer",
                                            "arguments": "{}"}}]},
      {"role": "tool", "tool_call_id": "a", "content": "external"},
      {"role": "tool", "content": "ECHO"}],
     "an id replayed from an earlier turn"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "content": "c", "tool_calls": [
          {"type": "function", "function": {"name": "search", "arguments": "{}"}},
          {"type": "function", "function": {"name": "final_answer",
                                            "arguments": "{}"}}]},
      {"role": "tool", "content": "external"},
      {"role": "assistant", "content": "hm"},
      {"role": "tool", "content": "ECHO"}],
     "an assistant turn between two results of one batch"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "content": "c", "tool_calls": [
          {"type": "function", "function": {"name": "search", "arguments": "{}"}},
          {"type": "function", "function": {"name": "final_answer",
                                            "arguments": "{}"}}]},
      {"role": "tool", "name": "nosuch", "content": "external"},
      {"role": "tool", "content": "ECHO"}],
     "a result naming a tool that was never called"),
])
def test_the_echo_never_reaches_the_evidence_side(chat, label):
    """The five axes round seven found one step outside the invariant test.
    Each of these put the model's own words on the evidence side."""
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["ECHO"] not in ("tool_result", "document"), label


def test_no_arrangement_of_calls_and_results_makes_an_echo_into_evidence():
    """The invariant, swept rather than sampled: two, three and four parallel
    calls; ids absent, present or duplicated on the calls; ids absent, matching
    or naming nothing on the results; names present or absent; every order of
    arrival; and each of those again with a user message landing in the middle
    of the batch.

    The sampled version of this test could not fail on three of the four
    defects it was written for. That is the same failure as a benchmark pass
    that cannot reach its code, one level up, and it is why this one sweeps."""
    import itertools

    echo, names = "ECHO", ("final_answer", "search", "read_file", "lookup")
    failures = []
    for n_calls, gap in itertools.product((2, 3, 4), (False, True)):
        for call_ids, res_ids, res_names in itertools.product((0, 1, 2), (0, 1, 2),
                                                              (0, 1)):
            for order in itertools.permutations(range(n_calls)):
                calls = []
                for i in range(n_calls):
                    call = {"type": "function",
                            "function": {"name": names[i], "arguments": "{}"}}
                    if call_ids == 1:
                        call["id"] = f"c{i}"
                    elif call_ids == 2:
                        call["id"] = "dup"        # one id for every call
                    calls.append(call)
                results = []
                for i in range(n_calls):
                    msg = {"role": "tool",
                           "content": echo if i == 0 else f"external {i}"}
                    if res_ids == 1:
                        msg["tool_call_id"] = "dup" if call_ids == 2 else f"c{i}"
                    elif res_ids == 2:
                        msg["tool_call_id"] = f"zz{i}"   # names nothing
                    if res_names:
                        msg["name"] = names[i]
                    results.append(msg)
                seq = [results[i] for i in order]
                if gap:
                    seq = seq[:1] + [{"role": "user", "content": "hurry"}] + seq[1:]
                chat = [{"role": "user", "content": "q"},
                        {"role": "assistant", "content": "c",
                         "tool_calls": calls}] + seq
                trace = openai_chat.to_trace(
                    chat, model_text_tools={"final_answer"},
                    verbatim_tools={"read_file"})
                kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
                if kinds.get(echo) in ("tool_result", "document"):
                    failures.append((n_calls, gap, call_ids, res_ids,
                                     res_names, order))
    assert not failures, (
        f"{len(failures)} arrangement(s) recorded the model's own text as "
        f"evidence, first: {failures[:3]}")


def test_a_trace_whose_answer_cannot_be_placed_records_no_answer():
    """Promoting an artifact the reader could not place asserts the user saw
    it. Promoting the one before it asserts they saw THAT. Neither is known, so
    the trace records no answer and check-trace says `no_final_answer` - which
    is the true state of that reading, and a verdict of unauditable rather than
    a confident audit of the wrong text."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "no results found"},
            {"role": "tool", "content": "THE MODEL'S OWN ANSWER"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    assert not [a for a in trace["artifacts"] if a["kind"] == "final_answer"]
    assert any("records no final answer" in n for n in trace["_meta"]["notes"])
    result = check_trace(load_run(trace))
    assert result.verdict == "unauditable"


def test_a_doubt_ending_early_is_caught():
    """A root message inside a batch used to clear the batch's doubt while
    leaving the queue aligned, so the next guess looked certain. A regression
    there is not visible without the interleaved message."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c", "tool_calls": [
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "external"},
            {"role": "user", "content": "hurry up"},
            {"role": "tool", "content": "ECHO"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["ECHO"] not in ("tool_result", "document")


# --------------- the five guards a mutation sweep found no test would notice

def test_doubt_does_not_outlive_the_queue_it_belongs_to():
    """Doubt is cleared where the queue is replaced. If it is never cleared, a
    batch that was resolved cleanly inherits the uncertainty of an earlier one
    and its results are downgraded for no reason - a false alarm manufactured
    by the reader."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c1", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "first external"},
            {"role": "tool", "content": "second external"},
            {"role": "assistant", "content": "c2", "tool_calls": [
                {"type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "content": "PAGE TEXT"},
            {"role": "assistant", "content": "done"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["PAGE TEXT"] == "tool_result"      # its own batch was clean


def test_a_result_with_nothing_open_is_weighed_against_every_echo_tool():
    """A result arriving with no call open could have been any tool in the run.
    Measuring it against an empty set says 'no echo tool could have produced
    this', which is not something the file supports."""
    chat = [{"role": "user", "content": "q"},
            {"role": "tool", "content": "THE MODEL'S OWN ANSWER"},
            {"role": "assistant", "content": "done"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["THE MODEL'S OWN ANSWER"] not in ("tool_result", "document")


def test_a_name_no_declared_call_bears_settles_nothing():
    """If the batch declared calls and none of them is named `search`, a result
    calling itself `search` contradicts the log. Trusting it there is trusting
    the one statement the file already contradicts."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c", "tool_calls": [
                {"type": "function", "function": {"name": "final_answer",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "name": "search", "content": "THE MODEL'S ANSWER"},
            {"role": "assistant", "content": "done"}]
    trace = openai_chat.to_trace(chat, model_text_tools={"final_answer"})
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["THE MODEL'S ANSWER"] not in ("tool_result", "document")


def test_a_positional_match_consumes_the_call_it_took():
    """If the queue is read without being consumed, every result in a batch is
    matched to the first call - so the second result of a two-call batch wears
    the first call's name."""
    chat = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "c", "tool_calls": [
                {"type": "function", "function": {"name": "read_file",
                                                  "arguments": "{}"}},
                {"type": "function", "function": {"name": "search",
                                                  "arguments": "{}"}}]},
            {"role": "tool", "content": "the page"},
            {"role": "tool", "content": "the search results"},
            {"role": "assistant", "content": "done"}]
    trace = openai_chat.to_trace(chat, verbatim_tools={"read_file"})
    titles = [a["title"] for a in trace["artifacts"] if a["artifact_id"].startswith("t")]
    assert titles == ["read_file", "search"]
    kinds = {a["content"]: a["kind"] for a in trace["artifacts"]}
    assert kinds["the page"] == "document"          # the verbatim tool
    assert kinds["the search results"] == "tool_result"


@pytest.mark.parametrize("body", ['{"message": {"content": []}}',
                                  '{"message": {"content": {}}}',
                                  '{"message": {"content": 0}}',
                                  '{"message": {"content": false}}'])
def test_an_empty_container_is_not_the_runs_answer(body):
    """`[]` rendered as the string "[]" became the final answer and suppressed
    the warning that the export recorded no output at all."""
    doc = {"spans": [_span("chat", 1, {"gen_ai.operation.name": "chat"}, events=[
        {"name": "gen_ai.user.message", "attributes": {"content": "q"}},
        {"name": "gen_ai.choice", "attributes": {"content": body}}])]}
    trace = otel_genai.to_trace(doc)
    assert not [a for a in trace["artifacts"] if a["kind"] == "final_answer"]
    assert any("no span recorded model output" in n
               for n in trace["_meta"]["otel"]["notes"])


# ------------------------------------- text that is not English ASCII

def test_a_file_saved_with_a_byte_order_mark_just_opens(tmp_path):
    """Windows Notepad and several exporters write one. Plain utf-8 fails on
    the first character with a message about BOMs, which tells someone who did
    not choose the encoding nothing at all."""
    chat = [{"role": "user", "content": "вопрос"},
            {"role": "assistant", "content": "ответ"}]
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(chat, ensure_ascii=False).encode())
    assert main(["check-trace", str(path), "--quiet"]) == 0


def test_a_file_in_another_encoding_is_refused_in_words_not_codec_terms(tmp_path, capsys):
    """Guessing the encoding would change the letters - and every promise this
    tool makes is about letters being the same."""
    chat = [{"role": "user", "content": "вопрос"},
            {"role": "assistant", "content": "ответ"}]
    path = tmp_path / "cp1251.json"
    path.write_bytes(json.dumps(chat, ensure_ascii=False).encode("cp1251"))
    assert main(["check-trace", str(path)]) == 2
    err = capsys.readouterr().err
    assert "not saved as UTF-8" in err and "Save As" in err


@pytest.mark.parametrize("text,quoted", [
    ("Выручка «Северного пути» выросла", 'Выручка "Северного пути" выросла'),
    ("The “Northwind” filing", 'The "Northwind" filing'),
    ("O’Brien said", "O'Brien said"),
    ("2023—2024", "2023-2024"),
    ("1 840 млн", "1 840 млн"),
    ("营业收入增长", "营业收入增长"),
    ("рост 📈 14%", "рост 📈 14%"),
])
def test_typography_does_not_break_a_verbatim_quote(text, quoted):
    """A person pastes from a PDF, a browser or Word; the quote marks, dashes
    and spaces come out different from the source without a single letter
    changing. None of that may turn a real quote into a missing one."""
    from tallystick.normalize import normalize
    assert normalize(text) == normalize(quoted)


def test_offsets_count_characters_not_bytes():
    """A claim points at start and end. In Russian or Chinese one character is
    several bytes, and counting bytes would slice a word in half."""
    from tallystick import load_run
    run = load_run({"artifacts": [{"artifact_id": "a", "kind": "final_answer",
                                   "content": "Выручка выросла на 14%."}],
                    "steps": [{"step_id": "s", "inputs": [], "outputs": ["a"]}],
                    "claims": [{"claim_id": "c", "artifact_id": "a",
                                "start": 0, "end": 7}]})
    assert run.claims["c"].text == "Выручка"
