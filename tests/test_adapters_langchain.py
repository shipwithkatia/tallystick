"""The LangChain recorder, exercised offline with LangChain's own fakes.

The property under test is not "callbacks fire" - LangChain guarantees that - but
that the recorder recovers each model call's *inputs* from its prompt, honestly:
present verbatim -> input; absent -> not an input, whatever happened earlier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.fake import FakeListLLM  # noqa: E402

from tallystick import audit, load_run  # noqa: E402
from tallystick.adapters.langchain import TraceRecorder  # noqa: E402
from tallystick.propose import FakeProposer, post_run  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import langchain_demo as demo  # noqa: E402


def test_recorder_recovers_inputs_from_the_prompt():
    rec = TraceRecorder()
    demo.build_and_run(rec)
    run = rec.run()
    kinds = {a["artifact_id"]: a["kind"] for a in run["artifacts"]}
    assert kinds == {"doc_1": "document", "tool_1": "tool_result",
                     "llm_1": "intermediate", "llm_2": "final_answer"}
    by_out = {s["outputs"][0]: s for s in run["steps"]}
    assert sorted(by_out["llm_1"]["inputs"]) == ["doc_1", "tool_1"]
    assert by_out["llm_2"]["inputs"] == ["llm_1"]      # the answer saw only the summary
    load_run(run)                                       # format is valid


def test_an_artifact_absent_from_the_prompt_is_not_an_input():
    """The conservation rule at recording time: earlier is not the same as seen."""
    rec = TraceRecorder()
    config = {"callbacks": [rec]}
    demo.FilingRetriever().invoke("q", config=config)
    llm = FakeListLLM(responses=["Some answer that ignores the filing entirely."])
    llm.invoke("Write one sentence about the weather.", config=config)
    step = rec.run()["steps"][-1]
    assert step["inputs"] == []


def test_short_artifacts_are_not_matched_by_coincidence():
    rec = TraceRecorder(min_chars=20)
    config = {"callbacks": [rec]}
    demo.headcount.invoke({"company": "x"}, config=config)     # long enough: matched
    llm = FakeListLLM(responses=["ok."])
    llm.invoke("data: " + rec.artifacts[0]["content"], config=config)
    assert rec.run()["steps"][-1]["inputs"] == ["tool_1"]
    rec2 = TraceRecorder(min_chars=200)
    llm2 = FakeListLLM(responses=["ok."])
    demo.headcount.invoke({"company": "x"}, config={"callbacks": [rec2]})
    llm2.invoke("data: " + rec2.artifacts[0]["content"], config={"callbacks": [rec2]})
    assert rec2.run()["steps"][-1]["inputs"] == []


def test_recorded_trace_audits_end_to_end():
    import json
    rec = TraceRecorder()
    demo.build_and_run(rec)
    script = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "fake_answers_langchain.json")
        .read_text(encoding="utf-8"))
    posted = post_run(load_run(rec.run()), FakeProposer(script))
    balance = audit(posted)
    assert balance.laundering_rate == pytest.approx(1 / 3)
    bad = balance.injection_points()[0]
    assert bad.break_step_id == "generate_1"


def test_final_answer_can_be_chosen_explicitly():
    rec = TraceRecorder()
    demo.build_and_run(rec)
    run = rec.run(final_artifact_id="llm_1")
    kinds = {a["artifact_id"]: a["kind"] for a in run["artifacts"]}
    assert kinds["llm_1"] == "final_answer" and kinds["llm_2"] == "intermediate"


# --------------------------------------------------------------------------- #
# Found in review of the v0.3 draft
# --------------------------------------------------------------------------- #


def test_a_short_model_output_is_still_an_input_of_the_next_step():
    """min_chars guards against coincidental matches of tiny tool results. It must
    not sever the chain when a model writes one short sentence."""
    rec = TraceRecorder()
    config = {"callbacks": [rec]}
    demo.FilingRetriever().invoke("q", config=config)
    llm = FakeListLLM(responses=["Margin: 11.2%.", "Final."])
    llm.invoke("Summarise: " + demo.FILING, config=config)
    llm.invoke("Given this note: Margin: 11.2%. Answer.", config=config)
    steps = {s["outputs"][0]: s for s in rec.run()["steps"]}
    assert steps["llm_2"]["inputs"] == ["llm_1"]


def test_content_block_messages_match_multiline_documents():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage
    from langchain_core.documents import Document
    rec = TraceRecorder()
    config = {"callbacks": [rec]}
    multi = "Line one of the filing.\nLine two of the filing.\nLine three."
    rec.on_retriever_end([Document(page_content=multi)], run_id=__import__("uuid").uuid4())
    chat = FakeListChatModel(responses=["ok, noted."])
    chat.invoke([HumanMessage(content=[{"type": "text", "text": "Read:\n" + multi}])],
                config=config)
    assert rec.run()["steps"][-1]["inputs"] == ["doc_1"]


def test_nested_and_duplicate_documents_are_credited_tightly():
    from langchain_core.documents import Document
    import uuid
    rec = TraceRecorder()
    inner = "Operating margin was 11.2%, down from 12.9% in the prior-year period."
    outer = "Northwind reported revenue growth. " + inner + " No guidance was given."
    rec.on_retriever_end([Document(page_content=outer), Document(page_content=inner),
                          Document(page_content=outer)], run_id=uuid.uuid4())
    llm = FakeListLLM(responses=["x."])
    llm.invoke("Context: " + outer, config={"callbacks": [rec]})
    # not doc_2 (nested); of the two identical copies, the most recent one
    assert rec.run()["steps"][-1]["inputs"] == ["doc_3"]


def test_run_refuses_an_unknown_or_missing_final_answer():
    rec = TraceRecorder()
    with pytest.raises(ValueError, match="no model output"):
        rec.run()
    demo.build_and_run(rec)
    with pytest.raises(ValueError, match="not a recorded artifact"):
        rec.run(final_artifact_id="llm_99")


def test_step_ids_are_numbered_per_kind():
    rec = TraceRecorder()
    demo.build_and_run(rec)
    assert [s["step_id"] for s in rec.run()["steps"]] == \
        ["retrieve_1", "tool_step_1", "generate_1", "generate_2"]


# --------------------------------------------------------------------------- #
# Found in the second review of v0.3
# --------------------------------------------------------------------------- #


def _doc(text):
    from langchain_core.documents import Document
    return Document(page_content=text)


def _uuid():
    import uuid
    return uuid.uuid4()


def test_nesting_is_judged_on_normalised_length_not_raw_length():
    """A whitespace-padded nested doc can be raw-longer than its container."""
    inner = "Operating margin was 11.2%, down from 12.9% in the prior-year period."
    outer = "Northwind grew. " + inner + " No guidance."
    padded_inner = inner.replace(" ", "     ") + "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
    assert len(padded_inner) > len(outer)
    rec = TraceRecorder()
    rec.on_retriever_end([_doc(outer), _doc(padded_inner)], run_id=_uuid())
    llm = FakeListLLM(responses=["x."])
    llm.invoke("Context: " + outer, config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == ["doc_1"]


def test_min_chars_is_measured_on_normalised_content():
    rec = TraceRecorder(min_chars=20)
    rec.on_tool_end("  2840  " + " " * 40, run_id=_uuid())    # 48 raw, 4 normalised
    llm = FakeListLLM(responses=["x."])
    llm.invoke("the headcount is 2840 people", config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == []


def test_nul_content_cannot_forge_an_input_edge():
    rec = TraceRecorder()
    rec.on_retriever_end([_doc(demo.FILING), _doc("\0" * 10)], run_id=_uuid())
    llm = FakeListLLM(responses=["x."])
    llm.invoke("Read: " + demo.FILING, config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == ["doc_1"]


def test_empty_generations_record_nothing():
    from langchain_core.outputs import LLMResult
    rec = TraceRecorder()
    rec.on_llm_start({}, ["p"], run_id=_uuid())
    rec.on_llm_end(LLMResult(generations=[]), run_id=_uuid())
    assert rec.artifacts == [] and rec.steps == []


def test_tool_call_only_messages_are_not_model_prose():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult
    rec = TraceRecorder()
    rid = _uuid()
    rec.on_chat_model_start({}, [[]], run_id=rid)
    msg = AIMessage(content=[{"type": "tool_use", "id": "t1", "name": "f", "input": {"q": 1}}])
    rec.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rid)
    assert rec.artifacts == []
    # but a message mixing a text block and a tool call keeps the text only
    rid2 = _uuid()
    rec.on_chat_model_start({}, [[]], run_id=rid2)
    msg2 = AIMessage(content=[{"type": "text", "text": "Revenue rose 14% in Q2."},
                              {"type": "tool_use", "id": "t2", "name": "f", "input": {}}])
    rec.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg2)]]), run_id=rid2)
    assert rec.artifacts[-1]["content"] == "Revenue rose 14% in Q2."


def test_non_text_tool_outputs_do_not_become_phantom_artifacts():
    rec = TraceRecorder()
    rec.on_tool_end(None, run_id=_uuid())
    assert rec.artifacts == []
    rec.on_tool_end(b"bytes from a tool, decoded", run_id=_uuid())
    assert rec.artifacts[-1]["content"] == "bytes from a tool, decoded"
    rec.on_tool_end(_doc("a document returned by a tool"), run_id=_uuid())
    assert rec.artifacts[-1]["content"] == "a document returned by a tool"


def test_a_reused_recorder_credits_the_current_runs_copy():
    rec = TraceRecorder()
    demo.build_and_run(rec)
    demo.build_and_run(rec)          # same content again; ids doc_2, tool_2, llm_3, llm_4
    steps = {s["outputs"][0]: s for s in rec.run()["steps"]}
    assert sorted(steps["llm_3"]["inputs"]) == ["doc_2", "tool_2"]
    assert steps["llm_4"]["inputs"] == ["llm_3"]
    rec.reset()
    assert rec.artifacts == [] and rec.steps == []


def test_empty_tool_output_does_not_leak_its_name():
    rec = TraceRecorder()
    rid = _uuid()
    rec.on_tool_start({"name": "f"}, "x", run_id=rid)
    rec.on_tool_end("", run_id=rid)
    assert rec._tool_names == {}


# --------------------------------------------------------------------------- #
# Found in the third review of v0.3
# --------------------------------------------------------------------------- #


def test_structured_tool_outputs_are_kept_and_match_their_prompt_appearance():
    """A search_result / json block or a list of dicts is evidence. It must be
    recorded, and recorded in the same string form LangChain later puts in a
    prompt, so the step that reads it gets the input edge."""
    rec = TraceRecorder()
    blocks = [{"type": "search_result", "title": "10-Q",
               "content": [{"type": "text", "text": "Revenue rose twelve percent in the quarter."}]}]
    rec.on_tool_end(blocks, run_id=_uuid())
    assert rec.artifacts and "Revenue rose twelve percent" in rec.artifacts[-1]["content"]
    rows = [{"name": "alpha", "v": 1}, {"name": "beta", "v": 2}]
    rec.on_tool_end(rows, run_id=_uuid())
    assert len(rec.artifacts) == 2
    # the prompt reconstruction uses the same stringification
    from langchain_core.messages import HumanMessage
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    chat = FakeListChatModel(responses=["ok noted."])
    chat.invoke([HumanMessage(content=blocks)], config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == ["tool_1"]


def test_a_document_containing_a_nul_byte_still_matches():
    rec = TraceRecorder()
    text = "Northwind reported revenue of $412.6 million\x00 in the second quarter."
    rec.on_retriever_end([_doc(text)], run_id=_uuid())
    llm = FakeListLLM(responses=["x."])
    llm.invoke("Read: " + text, config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == ["doc_1"]


def test_the_same_artifact_twice_in_a_prompt_is_credited_once():
    rec = TraceRecorder()
    rec.on_retriever_end([_doc(demo.FILING)], run_id=_uuid())
    llm = FakeListLLM(responses=["x."])
    llm.invoke(demo.FILING + "\n---\n" + demo.FILING, config={"callbacks": [rec]})
    assert rec.run()["steps"][-1]["inputs"] == ["doc_1"]
