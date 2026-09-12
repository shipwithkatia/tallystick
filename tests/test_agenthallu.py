"""The AgentHallu adapter and harness, offline, on five real trajectories."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import agenthallu as harness  # noqa: E402

from tallystick import audit, load_run  # noqa: E402
from tallystick.adapters.agenthallu import ECHO_TOOLS, to_trace  # noqa: E402
from tallystick.propose import post_run  # noqa: E402
from tallystick.propose.base import Proposer  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "bench" / "sample-agenthallu"


def _load(rel: str):
    return json.loads((SAMPLE / rel).read_text(encoding="utf-8"))


@pytest.fixture
def traces():
    return {f.parent.name + "/" + f.name: to_trace(_load(f.parent.name + "/" + f.name),
                                                  name=f.parent.name + "/" + f.name)
            for f in sorted(SAMPLE.glob("*/*.json"))}


def test_every_sample_trajectory_becomes_a_valid_run(traces):
    assert len(traces) == 5
    for name, t in traces.items():
        run = load_run({k: t[k] for k in ("artifacts", "steps")})
        kinds = {a.kind.value for a in run.artifacts.values()}
        assert "final_answer" in kinds and "document" in kinds, name
        # every step's inputs are artifacts produced earlier, never later
        produced: set = set()
        for s in run.steps:
            assert set(s.inputs) <= produced | {"question"}, (name, s.step_id)
            produced |= set(s.outputs)


def test_step_ids_map_back_to_history_step_numbers(traces):
    t = traces["OpenDeepSearch/082.json"]
    m = t["_meta"]
    assert m["history_step_of"]["s3"] == 3 and m["history_step_of"]["s3.tools"] == 3
    assert m["hallucination_step"] == 3 and m["hallucination_subcategory"] == "Factual Reasoning"
    assert m["label_at_tool_boundary"] is False


def test_a_label_on_a_tool_only_step_is_marked_beyond_the_boundary(traces):
    """OpenDeepSearch/045: the labelled step 2 is a web_search call and its
    digest, no model prose. The hallucination is inside the tool result."""
    m = traces["OpenDeepSearch/045.json"]["_meta"]
    assert m["hallucination_step"] == 2 and m["label_at_tool_boundary"] is True
    arts = {a["artifact_id"]: a for a in traces["OpenDeepSearch/045.json"]["artifacts"]}
    assert "s2" not in arts and arts["s2.t0"]["kind"] == "tool_result"


def test_echo_tools_are_model_text_not_roots(traces):
    """A `final_answer` tool hands the agent's own text back; treating it as a
    root would ground every answer on itself."""
    arts = {a["artifact_id"]: a for a in traces["SmolAgents/049.json"]["artifacts"]}
    echo = [a for a in arts.values() if a["title"].split(" ")[0] in ECHO_TOOLS]
    assert echo and all(a["kind"] == "intermediate" for a in echo)
    web = [a for a in arts.values() if a["title"].startswith("python_interpreter")]
    assert all(a["kind"] == "tool_result" for a in web)


def test_a_clean_trajectory_has_no_label(traces):
    m = traces["Camel/078.json"]["_meta"]
    assert m["is_hallucination"] is False and m["hallucination_step"] is None
    assert m["label_at_tool_boundary"] is False


def test_long_tool_results_are_cut_and_the_cut_is_recorded():
    obj = _load("OpenDeepSearch/082.json")
    t = to_trace(obj, max_tool_chars=50)
    arts = {a["artifact_id"]: a for a in t["artifacts"]}
    assert t["_meta"]["truncated"] and all(len(arts[i]["content"]) == 50 for i in t["_meta"]["truncated"])
    load_run({k: t[k] for k in ("artifacts", "steps")})


def test_duplicate_history_step_numbers_get_distinct_ids():
    obj = _load("Camel/078.json")
    obj["history"].insert(2, dict(obj["history"][1]))   # two entries numbered the same
    t = to_trace(obj)
    ids = [a["artifact_id"] for a in t["artifacts"]]
    assert len(ids) == len(set(ids))
    assert t["_meta"]["history_step_of"].get("s2_2") == 2
    load_run({k: t[k] for k in ("artifacts", "steps")})


class _Counting(Proposer):
    """Segments every artifact into its whole text; credits nothing."""
    name = "counting"

    def __init__(self):
        self.credit_calls = []

    def complete(self, system, user):
        if user.startswith("TEXT:"):
            body = re.search(r"(<+)\n(.*?)\n>+\n", user, re.S).group(2)
            return json.dumps({"claims": [body]})
        self.credit_calls.append(user)
        return json.dumps({"credits": []})


def test_on_demand_posting_asks_only_for_claims_the_answer_reaches():
    run = load_run({
        "artifacts": [
            {"artifact_id": "d", "kind": "document", "content": "Revenue grew 14%."},
            {"artifact_id": "s1", "kind": "intermediate", "content": "Plan: look things up."},
            {"artifact_id": "s2", "kind": "intermediate", "content": "Revenue grew 14%."},
            {"artifact_id": "f", "kind": "final_answer", "content": "Revenue grew 14%."},
        ],
        "steps": [
            {"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
            {"step_id": "s1", "kind": "plan", "inputs": ["d"], "outputs": ["s1"]},
            {"step_id": "s2", "kind": "generate", "inputs": ["d", "s1"], "outputs": ["s2"]},
            {"step_id": "s3", "kind": "answer", "inputs": ["d", "s1", "s2"], "outputs": ["f"]},
        ],
    })
    p = _Counting()
    posted = post_run(run, p, on_demand=True)
    # f.c1 asked; its self-evident credits reach d (root) and s2 -> s2.c1 asked;
    # s1's plan sentence is reached by nothing and never asked.
    assert len(p.credit_calls) == 2
    assert posted["_proposal"]["unasked_claims"] == 1
    bal = audit(posted)
    assert bal.audits["f.c1"].status.value == "grounded"
    assert bal.audits["s1.c1"].status.value == "unsupported"
    assert any("on_demand" in w for w in posted["_proposal"]["warnings"])
    # the same run posted exhaustively asks for every claim
    p2 = _Counting()
    post_run(run, p2)
    assert len(p2.credit_calls) == 3


def test_selection_is_deterministic_and_matches_clean_per_framework(tmp_path):
    files = harness.select(SAMPLE, "factual", 7, None)
    again = harness.select(SAMPLE, "factual", 7, None)
    assert files == again
    names = sorted(f.parent.name + "/" + f.name for f in files)
    # factual: 049 (Fact Derive) and 045 (Summarize Misalign); 082 is
    # OpenDeepSearch-CodeAct and excluded by default. Clean from the same
    # frameworks: OpenDeepSearch/054 (SmolAgents has no clean sample here).
    assert names == ["OpenDeepSearch/045.json", "OpenDeepSearch/054.json", "SmolAgents/049.json"]
    with_code = sorted(f.parent.name + "/" + f.name
                       for f in harness.select(SAMPLE, "factual", 7, None, include_codeact=True))
    assert with_code == ["OpenDeepSearch/045.json", "OpenDeepSearch/054.json",
                         "OpenDeepSearch/082.json", "SmolAgents/049.json"]
    assert harness.select(SAMPLE, "retrieval", 7, None) != []
    # --limit caps the labelled runs, then clean runs are matched to those
    limited = harness.select(SAMPLE, "all", 7, 1)
    kinds = [to_trace(_load(f.parent.name + "/" + f.name))["_meta"]["is_hallucination"] for f in limited]
    assert kinds.count(True) == 1 and kinds.count(False) <= 1


def test_score_reads_the_earliest_failing_history_step(traces):
    """Camel/078 (clean): the answer "3√13" is found word for word in the
    step-1 task text and in the step-4 note, both model prose; the counting
    proposer funds neither, so the chain breaks at history step 1."""
    t = traces["Camel/078.json"]
    run = load_run({k: t[k] for k in ("artifacts", "steps")})
    posted = post_run(run, _Counting(), on_demand=True)
    sc = harness.score(t, posted)
    assert sc["flagged"] is True and sc["break_step"] == 1 and sc["break_steps"] == [1]
    assert sc["label_step"] is None and sc["exact"] is False and sc["within_one"] is False


def test_a_break_at_the_answer_step_is_numbered_after_the_last_history_step():
    """Fresh answer text nothing in the run contains: the only break is at the
    answer itself, numbered one past the last history step - even when step
    numbers skip (1, 2, 4)."""
    obj = _load("Camel/078.json")
    obj["agent_answer"] = "Nothing in this run says this sentence at all."
    obj["history"][2]["step"] = 4                       # steps 1, 2, 4
    t = to_trace(obj)
    run = load_run({k: t[k] for k in ("artifacts", "steps")})
    sc = harness.score(t, post_run(run, _Counting(), on_demand=True))
    assert sc["answer_step"] == 5 and sc["break_steps"] == [5] and sc["break_step"] == 5
    # a label on the last real step is not "exact" for an answer-level break
    obj["is_hallucination"] = "true"
    obj["hallucination_step"] = "4"
    t = to_trace(obj)
    sc = harness.score(t, post_run(load_run({k: t[k] for k in ("artifacts", "steps")}),
                                   _Counting(), on_demand=True))
    assert sc["exact"] is False and sc["label_hit"] is False and sc["within_one"] is True


def test_label_hit_counts_the_labelled_step_among_all_breaks():
    """Two answer claims that break at different steps: `exact` looks only at
    the earliest, `label_hit` at all of them."""
    obj = _load("Camel/078.json")
    # a second answer sentence that only step 4's note contains
    obj["history"][3]["content"] = "The radius is therefore twelve units long."
    obj["agent_answer"] = "3√13. The radius is therefore twelve units long."
    obj["is_hallucination"] = "true"
    obj["hallucination_step"] = "4"
    t = to_trace(obj)
    run = load_run({k: t[k] for k in ("artifacts", "steps")})

    class Sentences(_Counting):
        def complete(self, system, user):
            if user.startswith("TEXT:"):
                body = re.search(r"(<+)\n(.*?)\n>+\n", user, re.S).group(2)
                return json.dumps({"claims": [x for x in body.split(". ") if x]})
            return super().complete(system, user)

    posted = post_run(run, Sentences(), on_demand=True)
    sc = harness.score(t, posted)
    assert sc["break_steps"] == [1, 4]
    assert sc["break_step"] == 1 and sc["exact"] is False
    assert sc["label_hit"] is True


def test_echoes_answer_needs_the_answer_as_the_last_output_line():
    from tallystick.adapters.agenthallu import _echoes_answer
    assert _echoes_answer("Execution logs:\nLast output from code snippet:\nJoyce McLaughlin",
                          'final_answer("Joyce McLaughlin")', "Joyce McLaughlin") is True
    # the answer only somewhere inside a search result is not an echo
    assert _echoes_answer("Search result: ... in March 2022 the ministry ...\nmore text",
                          'web_search(query="tornado replacement March 2022")', "March 2022") is False
    assert _echoes_answer("Output: 42", "print(6*7)", "42") is False        # under 8 chars
    assert _echoes_answer("Last output:\nJoyce McLaughlin", "print(x)", "Joyce McLaughlin") is False


def test_a_label_on_an_echoed_note_step_is_reachable_not_boundary():
    """Camel writes notes through append_note; a label on a note-only step
    points at model text the audit can reach, not at a tool result."""
    obj = _load("Camel/078.json")
    obj["history"][2]["tool_calls"] = [{"name": "append_note", "arguments": {"note": "3√13"}}]
    obj["history"][2]["tool_responses"] = ["Note appended: the answer is 3√13."]
    obj["history"][2]["content"] = ""
    obj["is_hallucination"] = "true"
    obj["hallucination_step"] = str(obj["history"][2]["step"])
    t = to_trace(obj)
    assert t["_meta"]["label_at_tool_boundary"] is False
    obj["history"][2]["tool_calls"] = [{"name": "search_google", "arguments": {"q": "x"}}]
    assert to_trace(obj)["_meta"]["label_at_tool_boundary"] is True


def test_a_codeact_trajectory_is_marked(traces):
    """OpenDeepSearch-CodeAct calls web_search from inside Python: the search
    result and the model's own computed string land in one execution log."""
    assert traces["OpenDeepSearch/082.json"]["_meta"]["codeact"] is True
    assert traces["OpenDeepSearch/054.json"]["_meta"]["codeact"] is False   # ReAct variant
    assert traces["SmolAgents/049.json"]["_meta"]["codeact"] is False
    assert traces["Camel/078.json"]["_meta"]["codeact"] is False


def test_summary_counts_boundary_rows_apart(traces):
    rows = []
    for name, t in traces.items():
        run = load_run({k: t[k] for k in ("artifacts", "steps")})
        posted = post_run(run, _Counting(), on_demand=True)
        rows.append({"file": name, "meta": t["_meta"], "score": harness.score(t, posted)})
    s = harness.summarise(rows)
    assert s["hallucinated"]["n"] == 3 and s["clean"]["n"] == 2
    assert s["beyond_tool_boundary"]["n"] == 1 and s["reachable"]["n"] == 2
    # Camel/078 breaks in model prose (see above); OpenDeepSearch/054's answer
    # sits verbatim in a web_search result, a root, so it grounds with no
    # credit from the proposer at all.
    assert s["clean"]["flagged"] == 1
    text = harness.render(s, "counting", "factual")
    assert "beyond the audit's boundary" in text and "| clean | 2 |" in text


def test_reuse_proposer_answers_old_questions_from_the_posted_file_and_new_ones_from_the_model(tmp_path):
    """A rerun after a deterministic change must not pay the model again for
    the same questions, nor add its noise. Claims the old run never reached
    still go to the model."""
    obj = _load("Camel/078.json")
    t = to_trace(obj, name="Camel/078.json")
    run = load_run({k: t[k] for k in ("artifacts", "steps")})

    class Recording(_Counting):
        def complete(self, system, user):
            if user.startswith("TEXT:"):
                body = re.search(r"(<+)\n(.*?)\n>+\n", user, re.S).group(2)
                return json.dumps({"claims": [x for x in body.split(". ") if len(x) > 3]})
            self.credit_calls.append(user)
            return json.dumps({"credits": [{"artifact_id": "s1", "quote": "3√13"}]})

    first = post_run(run, Recording(), on_demand=True)
    (tmp_path / "Camel__078.json").write_text(json.dumps(first), encoding="utf-8")

    inner = _Counting()
    reuse = harness.ReuseProposer(inner, tmp_path)
    reuse.start("Camel/078.json")
    second = post_run(run, reuse, on_demand=True)
    assert reuse.calls["segment_reused"] > 0 and reuse.calls["credits_reused"] > 0
    assert inner.credit_calls == []                     # nothing new was reached
    assert [c["claim_id"] for c in second["claims"]] == [c["claim_id"] for c in first["claims"]]
    assert sorted(e["account"] for e in second["entries"]) == sorted(e["account"] for e in first["entries"])
    # a trajectory with no old file goes straight to the model
    reuse.start("Camel/999.json")
    third = post_run(run, reuse, on_demand=True)
    assert reuse.calls["model"] > 0 and third["_proposal"]["claims_posted"] >= 1


def test_diagnose_agenthallu_classifier_buckets():
    import diagnose_agenthallu as dg
    posted = {
        "artifacts": [{"artifact_id": "d", "kind": "tool_result",
                       "content": "The Greenland shark is the longest-lived vertebrate, living 400 years."},
                      {"artifact_id": "f", "kind": "final_answer",
                       "content": "The longest-lived vertebrate is the Greenland shark. 1 gallon = 3,785.41 cm³. Water is wet."}],
        "claims": [{"claim_id": "f.c1", "artifact_id": "f", "start": 0, "end": 51},
                   {"claim_id": "f.c2", "artifact_id": "f", "start": 52, "end": 76},
                   {"claim_id": "f.c3", "artifact_id": "f", "start": 77, "end": 90}],
        "_proposal": {"dropped_credits": [{"claim_id": "f.c3", "artifact_id": "d", "quote": "Water is wet",
                                           "reason": "quote is not a verbatim substring of the artifact"}]},
    }
    assert dg.classify(posted, "f.c1", "")[0] == "paraphrase_of_tool_result"
    assert dg.classify(posted, "f.c2", "")[0] == "computation_or_formula"
    assert dg.classify(posted, "f.c3", "")[0] == "quote_offered_not_verbatim"
    assert dg.classify(posted, "f.c1", "cited span not fully accounted for")[0] == "unaccounted_span"
    assert dg._FORMULA.search("p-propenyl benzoate with -CH=CH-CH₃ groups") is None


def test_reuse_credits_offer_a_straddling_quote_once_and_nothing_for_a_prior_only_claim(tmp_path):
    old = {
        "artifacts": [{"artifact_id": "s", "kind": "intermediate",
                       "content": "Revenue grew 14%. In summary, costs fell 3%."}],
        "steps": [{"step_id": "s2", "kind": "summarize", "inputs": [], "outputs": ["s"]}],
        "claims": [{"claim_id": "f.c1", "artifact_id": "s", "start": 0, "end": 17},
                   {"claim_id": "f.c2", "artifact_id": "s", "start": 30, "end": 44}],
        "entries": [
            {"entry_id": "f.c1.e1", "claim_id": "f.c1", "account": "EVIDENCE:s#0-17",
             "quoted_span": "Revenue grew 14%.", "proposed_by": "fake", "group": "f.c1.g1"},
            {"entry_id": "f.c2.e0", "claim_id": "f.c2", "account": "PRIOR:model",
             "quoted_span": "", "proposed_by": "fake"},
        ],
        "_proposal": {"dropped_claims": [], "dropped_credits": [
            {"claim_id": "f.c1", "artifact_id": "s", "quote": "Revenue grew 14%. In summary, costs",
             "reason": "quote only partially overlaps claim s#30-44; refused as ambiguous"},
            {"claim_id": "f.c1", "artifact_id": "s", "quote": "nowhere in the text",
             "reason": "quote is not a verbatim substring of the artifact"},
        ]},
    }
    (tmp_path / "X__1.json").write_text(json.dumps(old), encoding="utf-8")
    reuse = harness.ReuseProposer(_Counting(), tmp_path)
    reuse.start("X/1.json")
    old = reuse._olds[0]
    assert reuse._credits_of(old, "f.c1") == [{"artifact_id": "s", "quote": "Revenue grew 14%."},
                                              {"artifact_id": "s", "quote": "nowhere in the text"}]
    assert reuse._credits_of(old, "f.c2") == []


def _twin_run():
    """A last step and a final answer with the same text, as OpenManus and the
    SmolAgents `final_answer` echo produce: the step gets coverage claims, the
    answer by design does not."""
    text = "Revenue grew 14%. Costs fell by three percent over the year."
    return load_run({
        "artifacts": [{"artifact_id": "d", "kind": "document", "content": "Revenue grew 14%."},
                      {"artifact_id": "s6", "kind": "intermediate", "content": text},
                      {"artifact_id": "answer", "kind": "final_answer", "content": text}],
        "steps": [{"step_id": "s6", "kind": "generate", "inputs": ["d"], "outputs": ["s6"]},
                  {"step_id": "answer", "kind": "answer", "inputs": ["d", "s6"], "outputs": ["answer"]}],
    })


class _Segmenter(_Counting):
    """Returns one claim per artifact: the first sentence. Credits it to `d`."""
    def complete(self, system, user):
        if user.startswith("TEXT:"):
            return json.dumps({"claims": ["Revenue grew 14%."]})
        self.credit_calls.append(user)
        return json.dumps({"credits": [{"artifact_id": "d", "quote": "Revenue grew 14%."}]})


def test_posted_claims_say_who_proposed_them():
    posted = post_run(_twin_run(), _Segmenter(), on_demand=True)
    by = {c["claim_id"]: c["proposed_by"] for c in posted["claims"]}
    assert by["s6.c1"] == "counting" and by["s6.c2"] == "coverage"
    assert [c for c in posted["claims"] if c["artifact_id"] == "answer"] and all(
        by[c["claim_id"]] == "counting" for c in posted["claims"] if c["artifact_id"] == "answer")


def test_reuse_does_not_replay_coverage_claims_onto_a_twin_final_answer(tmp_path):
    """The v0.7.1 AgentHallu run did: the segment call for the answer matched
    the last step's text, and the step's coverage claims ("I will now
    terminate the interaction.") came back as if the segmenter had returned
    them for the answer - a claim to fund that the answer never had."""
    run = _twin_run()
    first = post_run(run, _Segmenter(), on_demand=True)
    (tmp_path / "X__1.json").write_text(json.dumps(first), encoding="utf-8")
    inner = _Segmenter()
    reuse = harness.ReuseProposer(inner, tmp_path)
    reuse.start("X/1.json")
    second = post_run(run, reuse, on_demand=True)
    assert inner.credit_calls == [] and reuse.calls["model"] == 0
    answer_claims = [c for c in second["claims"] if c["artifact_id"] == "answer"]
    assert [c["end"] - c["start"] for c in answer_claims] == [17]
    assert [c["claim_id"] for c in second["claims"]] == [c["claim_id"] for c in first["claims"]]
    # an untagged file (written before 0.7.2) is read the same way: the final
    # answer is preferred among artifacts with the same text
    untagged = json.loads(json.dumps(first))
    for c in untagged["claims"]:
        c.pop("proposed_by")
    (tmp_path / "X__2.json").write_text(json.dumps(untagged), encoding="utf-8")
    reuse.start("X/2.json")
    third = post_run(run, reuse, on_demand=True)
    assert [c["claim_id"] for c in third["claims"]] == [c["claim_id"] for c in first["claims"]]


def test_reuse_replays_the_segmenter_not_coverage_on_a_lone_step(tmp_path):
    """A tagged file: only the claim the model returned comes back for a step;
    the pipeline adds coverage again itself, and says so. An untagged file
    cannot tell the two apart, so its replayed claims are tagged `replay`."""
    run = load_run({
        "artifacts": [{"artifact_id": "d", "kind": "document", "content": "Revenue grew 14%."},
                      {"artifact_id": "s6", "kind": "intermediate",
                       "content": "Revenue grew 14%. Costs fell by three percent over the year."},
                      {"artifact_id": "answer", "kind": "final_answer", "content": "Revenue grew 14%."}],
        "steps": [{"step_id": "s6", "kind": "generate", "inputs": ["d"], "outputs": ["s6"]},
                  {"step_id": "answer", "kind": "answer", "inputs": ["d", "s6"], "outputs": ["answer"]}],
    })
    first = post_run(run, _Segmenter(), on_demand=True)
    assert {c["proposed_by"] for c in first["claims"] if c["artifact_id"] == "s6"} == {"counting", "coverage"}
    (tmp_path / "X__1.json").write_text(json.dumps(first), encoding="utf-8")
    reuse = harness.ReuseProposer(_Segmenter(), tmp_path)
    reuse.start("X/1.json")
    second = post_run(run, reuse, on_demand=True)
    by = {c["claim_id"]: c["proposed_by"] for c in second["claims"]}
    assert by == {c["claim_id"]: c["proposed_by"] for c in first["claims"]}
    assert by["s6.c2"] == "coverage" and second["_proposal"]["coverage_claims"] == 1
    untagged = json.loads(json.dumps(first))
    for c in untagged["claims"]:
        c.pop("proposed_by")
    (tmp_path / "X__2.json").write_text(json.dumps(untagged), encoding="utf-8")
    reuse.start("X/2.json")
    third = post_run(run, reuse, on_demand=True)
    assert [c["claim_id"] for c in third["claims"]] == [c["claim_id"] for c in first["claims"]]
    by = {c["claim_id"]: c["proposed_by"] for c in third["claims"]}
    # the step's claims cannot be told from coverage; the answer's can (a
    # final answer never carries coverage), so they stay the model's
    assert by["s6.c1"] == by["s6.c2"] == "replay" and by["answer.c1"] == "counting"
    # and a run that replays that run's file does not promote them to the model's
    handed_on = tmp_path / "again" / "posted"
    handed_on.mkdir(parents=True)
    (handed_on / "X__2.json").write_text(json.dumps(third), encoding="utf-8")
    reuse = harness.ReuseProposer(_Segmenter(), handed_on)
    reuse.start("X/2.json")
    fifth = post_run(run, reuse, on_demand=True)
    assert reuse.calls["model"] == 0
    assert {c["claim_id"]: c["proposed_by"] for c in fifth["claims"]} == by
    # a root with the same text is not an answer to a segment question
    rooty = json.loads(json.dumps(first))
    rooty["artifacts"].insert(0, {"artifact_id": "t", "kind": "tool_result", "content": "Revenue grew 14%."})
    (tmp_path / "X__3.json").write_text(json.dumps(rooty), encoding="utf-8")
    reuse.start("X/3.json")
    fourth = post_run(run, reuse, on_demand=True)
    assert [c["claim_id"] for c in fourth["claims"] if c["artifact_id"] == "answer"] == ["answer.c1"]


def test_reuse_searches_directories_in_order_and_skips_segments_of_a_reuse_run(tmp_path):
    run = _twin_run()
    first = post_run(run, _Segmenter(), on_demand=True)
    fresh = tmp_path / "fresh" / "posted"
    fresh.mkdir(parents=True)
    (fresh / "X__1.json").write_text(json.dumps(first), encoding="utf-8")
    # a directory written by a reuse run, untagged: its answer may carry
    # replayed coverage, so it answers credit questions only
    polluted = json.loads(json.dumps(first))
    for c in polluted["claims"]:
        c.pop("proposed_by")
    later = tmp_path / "later" / "posted"
    later.mkdir(parents=True)
    (later / "X__1.json").write_text(json.dumps(polluted), encoding="utf-8")
    (later / "X__9.json").write_text(json.dumps(polluted), encoding="utf-8")
    (tmp_path / "later" / "rows.meta.json").write_text(json.dumps({"reuse": [str(fresh)]}))

    inner = _Segmenter()
    reuse = harness.ReuseProposer(inner, [later, fresh])
    reuse.start("X/1.json")
    second = post_run(run, reuse, on_demand=True)
    assert [c["claim_id"] for c in second["claims"]] == [c["claim_id"] for c in first["claims"]]
    assert reuse.calls["model"] == 0 and reuse.calls["segment_reused"] == 2
    # a file only the reuse-run directory has: segments go to the model,
    # credits are still read from the file
    reuse.start("X/9.json")
    inner.credit_calls.clear()
    third = post_run(run, reuse, on_demand=True)
    assert reuse.calls["model"] == 2 and inner.credit_calls == []
    assert [c["claim_id"] for c in third["claims"]] == [c["claim_id"] for c in first["claims"]]
    # a file that records its own reuse is recognised without the meta file
    marked = json.loads(json.dumps(polluted))
    marked["_proposal"]["reused_from"] = [str(fresh)]
    alone = tmp_path / "alone" / "posted"
    alone.mkdir(parents=True)
    (alone / "X__1.json").write_text(json.dumps(marked), encoding="utf-8")
    reuse = harness.ReuseProposer(_Segmenter(), alone)
    reuse.start("X/1.json")
    post_run(run, reuse, on_demand=True)
    assert reuse.calls["segment_reused"] == 0 and reuse.calls["credits_reused"] > 0


def test_resume_redoes_trajectories_that_failed_last_time(tmp_path, monkeypatch):
    """The v0.7.1 rerun lost 19 trajectories to an exhausted API balance;
    a resume must redo them, not count them as done."""
    import tallystick.propose as tp

    class Flaky(_Counting):
        def __init__(self, *a, **k):
            super().__init__()

    monkeypatch.setattr(tp, "AnthropicProposer", Flaky)
    work = tmp_path / "w"
    args = ["--data", str(SAMPLE), "--work", str(work), "--select", "all"]
    assert harness.main(args) == 0
    rows = [json.loads(l) for l in (work / "rows.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and not any(r.get("error") for r in rows)
    # fake a failure on one row and resume
    rows[1] = {k: v for k, v in rows[1].items() if k not in ("score", "proposal")}
    rows[1]["error"] = "BadRequestError: credit balance is too low"
    (work / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert harness.main(args) == 0
    again = [json.loads(l) for l in (work / "rows.jsonl").read_text().splitlines()]
    assert len(again) == 3 and not any(r.get("error") for r in again)
    assert {r["file"] for r in again} == {r["file"] for r in rows}


def test_reuse_does_not_replay_a_whole_answer_claim_as_the_segmenter_s(tmp_path):
    run = load_run({
        "artifacts": [{"artifact_id": "t", "kind": "tool_result", "content": "Result: 1898"},
                      {"artifact_id": "answer", "kind": "final_answer", "content": "1898"}],
        "steps": [{"step_id": "s1", "kind": "tool", "inputs": [], "outputs": ["t"]},
                  {"step_id": "answer", "kind": "answer", "inputs": ["t"], "outputs": ["answer"]}],
    })

    class Silent(_Counting):
        def complete(self, system, user):
            if user.startswith("TEXT:"):
                return json.dumps({"claims": []})
            return super().complete(system, user)

    first = post_run(run, Silent(), on_demand=True)
    assert [c["proposed_by"] for c in first["claims"]] == ["whole-answer"]
    (tmp_path / "X__1.json").write_text(json.dumps(first), encoding="utf-8")
    reuse = harness.ReuseProposer(Silent(), tmp_path)
    reuse.start("X/1.json")
    second = post_run(run, reuse, on_demand=True)
    assert [c["proposed_by"] for c in second["claims"]] == ["whole-answer"]
