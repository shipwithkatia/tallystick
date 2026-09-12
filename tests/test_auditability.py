"""`tallystick check-trace`: what an audit of this trace will be able to see.

No model, no claims, no network. Everything here is the file and plain code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tallystick import load_run
from tallystick.auditability import DEFAULT_MIN_REACHABLE, check_trace as check, report
from tallystick.cli import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))


def _run(artifacts, steps):
    return load_run({"artifacts": artifacts, "steps": steps})


DOC = {"artifact_id": "d", "kind": "document", "content": "Revenue grew 14% in Q4."}
SUM = {"artifact_id": "s", "kind": "intermediate", "content": "Revenue grew 14%."}
ANS = {"artifact_id": "f", "kind": "final_answer", "content": "Revenue grew 14%, so Q4 was good."}
TOOL = {"artifact_id": "t", "kind": "tool_result", "content": "headcount=1200"}


def test_a_fully_recorded_run_is_auditable_and_nothing_is_opaque():
    a = check(_run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert a.verdict == "auditable" and a.findings == ()
    # the document is external text stored verbatim - that is what a trace is
    # for, so it is left out of the fraction rather than counted against it
    assert a.derived == 2 and a.judged_artifacts == 2 and a.reachable_share == 1.0
    assert a.ingest_steps == ("s1",) and a.opaque_steps == ()
    assert "AUDITABLE" in report(a)


def test_a_step_that_recorded_only_a_tool_result_is_opaque_but_not_a_defect():
    a = check(_run([DOC, TOOL, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "tool", "inputs": ["d"], "outputs": ["t"]},
        {"step_id": "s3", "kind": "summarize", "inputs": ["d", "t"], "outputs": ["s"]},
        {"step_id": "s4", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert a.opaque_steps == ("s2",) and a.findings == ()
    assert a.derived == 2 and a.tool_results == 1 and a.judged_artifacts == 3
    assert a.verdict == "auditable"          # a boundary is not a defect
    text = report(a)
    assert "only a tool result: s2" in text and "ends on trust" in text


def test_the_share_gates_the_verdict_only_when_it_is_asked_for():
    arts = [TOOL, {"artifact_id": "t2", "kind": "tool_result", "content": "x=1"}, SUM, ANS]
    steps = [{"step_id": "s1", "kind": "tool", "inputs": [], "outputs": ["t"]},
             {"step_id": "s2", "kind": "tool", "inputs": [], "outputs": ["t2"]},
             {"step_id": "s3", "kind": "summarize", "inputs": ["t", "t2"], "outputs": ["s"]},
             {"step_id": "s4", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]
    assert check(_run(arts, steps)).reachable_share == 0.5
    assert check(_run(arts, steps)).verdict == "auditable"
    gated = check(_run(arts, steps), min_reachable=DEFAULT_MIN_REACHABLE)
    assert gated.verdict == "partial" and "Below the 80%" in report(gated)
    assert check(_run(arts, steps), min_reachable=0.4).verdict == "auditable"


def test_model_text_written_by_a_step_that_declares_no_inputs_is_a_defect():
    """The conservation rule is the whole point: a credit is only verifiable
    against something the step actually had. A step with no declared inputs can
    never fund anything, and that is a recorder bug, not a model failure."""
    a = check(_run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": [], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    codes = [f.code for f in a.findings]
    assert "undeclared_inputs" in codes and a.verdict == "partial"
    assert [f.subject for f in a.findings if f.code == "undeclared_inputs"] == ["s2"]
    # and the document nobody took in is named too: with s2 declaring no inputs,
    # `d` is recorded and unreachable, which is the same bug seen from the
    # other end
    assert [f.subject for f in a.findings if f.code == "orphan_root"] == ["d"]


def test_derived_text_no_step_admits_to_writing():
    a = check(_run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert "orphan_derived" in {f.code for f in a.findings}
    assert {f.subject for f in a.findings if f.code == "orphan_derived"} == {"s"}


def test_two_artifacts_with_the_same_text_cannot_be_told_apart():
    """The v0.7.1 AgentHallu run died of exactly this: a final answer repeating
    its last step word for word, and a reuse that matched artifacts by text."""
    twin = dict(ANS, content=SUM["content"])
    a = check(_run([DOC, SUM, twin], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    dup = [f for f in a.notes if f.code == "duplicate_content"]
    assert len(dup) == 1 and dup[0].subject == "f, s"
    # and it is a note, not a defect: real agents do this constantly, and the
    # fix is a change to the agent rather than to the recorder
    assert a.findings == () and a.verdict == "auditable"
    assert "does not decide the verdict" in report(a)
    # two artifacts that are both blank are reported as empty, not as twins
    a = check(_run([dict(DOC, content="   "), dict(SUM, content="   ")], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]}]))
    assert "duplicate_content" not in {f.code for f in a.notes}
    assert {f.code for f in a.findings} >= {"empty_artifact", "no_final_answer"}


def test_a_contradictory_trace_is_unreadable_not_merely_thin(tmp_path):
    """Two steps claiming the same output, or a reference to an artifact that is
    not there, is a broken file: exit 2, the "could not run" code, never a
    verdict about auditability."""
    bad = {"artifacts": [DOC, SUM, ANS],
           "steps": [{"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
                     {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
                     {"step_id": "s2b", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
                     {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert main(["check-trace", str(path), "--quiet"]) == 2


def test_a_trace_with_nothing_to_audit_back_from_is_unauditable():
    no_answer = check(_run([DOC, SUM], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]}]))
    assert no_answer.verdict == "unauditable"
    assert [f.code for f in no_answer.fatal] == ["no_final_answer"]
    all_roots = check(_run([DOC, TOOL], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "tool", "inputs": ["d"], "outputs": ["t"]}]))
    assert all_roots.verdict == "unauditable"
    assert {f.code for f in all_roots.fatal} == {"no_final_answer", "no_model_text"}
    empty = check(_run([], []))
    assert empty.verdict == "unauditable" and "no_steps" in {f.code for f in empty.fatal}
    assert empty.reachable_share is None       # undefined, not a confident 0%
    assert "n/a" in report(empty)


def test_a_truncated_artifact_is_read_from_the_recorder_s_meta():
    run = _run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}])
    assert check(run).findings == ()
    a = check(run, meta={"truncated": ["d"]})
    assert [(f.code, f.subject) for f in a.findings] == [("truncated", "d")]
    assert check(run, meta={}).findings == () and check(run, meta={"truncated": None}).findings == ()


def test_the_report_is_json_serialisable_and_keeps_the_verdict():
    a = check(_run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": [], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    payload = json.loads(json.dumps(a.as_dict()))
    assert payload["verdict"] == "partial" and payload["reachable_share"] == 1.0
    assert payload["findings"][0]["code"] == "undeclared_inputs"
    assert payload["min_reachable"] is None


def test_it_finds_on_a_real_trajectory_the_defect_that_broke_the_v071_run(tmp_path):
    """AgentHallu's final answers usually repeat an earlier artifact word for
    word - 210 of 223 in the v0.7.1 run. check-trace names that before any
    money is spent."""
    from agenthallu import to_trace                      # noqa: E402
    sample = Path(__file__).resolve().parents[1] / "bench" / "sample-agenthallu"
    twins = 0
    for f in sorted(sample.glob("*/*.json")):
        trace = to_trace(json.loads(f.read_text(encoding="utf-8")), name=f.name)
        a = check(load_run({k: trace[k] for k in ("artifacts", "steps")}),
                  meta=trace["_meta"])
        assert a.verdict in ("auditable", "partial")
        twins += any(f_.code == "duplicate_content" for f_ in a.notes)
    assert twins >= 1


@pytest.mark.parametrize("args,expected", [
    (["check-trace", "examples/laundered_summary.json", "--quiet"], 0),
    (["check-trace", "examples/laundered_summary.json", "--min-reachable", "--quiet"], 1),
    (["check-trace", "examples/does-not-exist.json", "--quiet"], 2),
])
def test_cli_exit_codes(args, expected, capsys):
    root = Path(__file__).resolve().parents[1]
    args = [a if not a.startswith("examples/") else str(root / a) for a in args]
    assert main(args) == expected


def test_cli_writes_the_json_report(tmp_path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "report.json"
    rc = main(["check-trace", str(root / "examples" / "laundered_summary.json"),
               "--json", str(out), "--quiet"])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"] == "auditable" and payload["opaque_steps"] == ["s2"]
    assert payload["reachable_share"] == 0.6667 and payload["notes"] == []


def test_the_share_is_counted_over_artifacts_so_step_granularity_cannot_move_it():
    """One agent turn logged as a single step, and the same turn logged as two,
    are the same run. A step-based fraction scores them differently; this one
    must not."""
    arts = [{"artifact_id": "m", "kind": "intermediate", "content": "I will look it up."},
            {"artifact_id": "t", "kind": "tool_result", "content": "Berlin, 3.6m"},
            ANS]
    one_step = [{"step_id": "s1", "kind": "generate", "inputs": [], "outputs": ["m", "t"]},
                {"step_id": "s2", "kind": "answer", "inputs": ["m", "t"], "outputs": ["f"]}]
    two_steps = [{"step_id": "s1", "kind": "generate", "inputs": [], "outputs": ["m"]},
                 {"step_id": "s1.tools", "kind": "tool", "inputs": ["m"], "outputs": ["t"]},
                 {"step_id": "s2", "kind": "answer", "inputs": ["m", "t"], "outputs": ["f"]}]
    a, b = check(_run(arts, one_step)), check(_run(arts, two_steps))
    assert a.reachable_share == b.reachable_share == 2 / 3
    # the merged recording is still told that a tool result is in there
    assert [n.code for n in a.notes] == ["mixed_outputs"] and b.opaque_steps == ("s1.tools",)


def test_a_step_that_produced_nothing_is_named_rather_than_vanishing():
    a = check(_run([DOC, SUM, ANS], [
        {"step_id": "s0", "kind": "plan", "inputs": [], "outputs": []},
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert a.silent_steps == ("s0",) and a.verdict == "auditable"
    assert "no_outputs" in {n.code for n in a.notes}
    assert a.steps == len(a.silent_steps) + len(a.ingest_steps) + len(a.opaque_steps) + a.reachable_steps


def test_two_answers_are_a_defect_because_the_audit_cannot_tell_which_one_was_seen():
    a = check(_run([DOC, SUM, ANS, dict(ANS, artifact_id="f2", content="Something else.")], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f", "f2"]}]))
    assert [f.code for f in a.findings] == ["multiple_final_answers"]
    assert a.findings[0].subject == "f, f2" and a.verdict == "partial"


def test_a_run_object_that_never_went_through_load_run_is_still_validated():
    from tallystick.types import Artifact, ArtifactKind, Run, Step, TraceError
    run = Run(artifacts={"f": Artifact("f", ArtifactKind.FINAL_ANSWER, "x")},
              steps=[Step("s1", "tool", (), ("ghost",))])
    with pytest.raises(TraceError):
        check(run)


def test_min_reachable_outside_zero_to_one_is_a_could_not_run(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    trace = str(root / "examples" / "laundered_summary.json")
    assert main(["check-trace", trace, "--min-reachable", "nan", "--quiet"]) == 2
    assert main(["check-trace", trace, "--min-reachable", "1.5", "--quiet"]) == 2
    assert main(["check-trace", trace, "--min-reachable", "-0.1", "--quiet"]) == 2


def test_truncated_is_only_read_when_it_is_a_list():
    run = _run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "summarize", "inputs": ["d"], "outputs": ["s"]},
        {"step_id": "s3", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}])
    # a bare string would otherwise iterate character by character
    assert check(run, meta={"truncated": "doc1"}).findings == ()
    assert check(run, meta={"truncated": 7}).findings == ()
    assert [f.subject for f in check(run, meta={"truncated": ["doc1"]}).findings] == ["doc1"]


def test_a_document_no_step_took_in_is_named_before_the_audit_blames_the_model():
    """The recorder's own documented failure: when a prompt reformats or
    truncates a document, `TraceRecorder` cannot match it and drops it from the
    step's inputs. The audit then reports every claim resting on it as
    unfunded, and says nothing about why. check-trace has to catch that here."""
    a = check(_run([DOC, TOOL, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s2", "kind": "tool", "inputs": ["d"], "outputs": ["t"]},
        # the summariser really saw `d` too, but the recorder could not match it
        {"step_id": "s3", "kind": "summarize", "inputs": ["t"], "outputs": ["s"]},
        {"step_id": "s4", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert [f.code for f in a.findings] == []          # d IS consumed, by s2
    b = check(_run([DOC, SUM, ANS], [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["d"]},
        {"step_id": "s3", "kind": "summarize", "inputs": [], "outputs": ["s"]},
        {"step_id": "s4", "kind": "answer", "inputs": ["s"], "outputs": ["f"]}]))
    assert [f.subject for f in b.findings if f.code == "orphan_root"] == ["d"]
    assert b.verdict == "partial"


def test_the_threshold_is_at_or_above_not_above():
    """9 of the 225 published trajectories sit exactly on 0.8. Which way the
    boundary falls moves the headline from 5 of 24 to 2 of 19, so it is pinned."""
    arts = [SUM, dict(SUM, artifact_id="s2"), dict(SUM, artifact_id="s3"),
            TOOL, ANS]
    steps = [{"step_id": "s1", "kind": "summarize", "inputs": [], "outputs": ["s"]},
             {"step_id": "sb", "kind": "summarize", "inputs": ["s"], "outputs": ["s2"]},
             {"step_id": "sc", "kind": "summarize", "inputs": ["s2"], "outputs": ["s3"]},
             {"step_id": "st", "kind": "tool", "inputs": ["s3"], "outputs": ["t"]},
             {"step_id": "sd", "kind": "answer", "inputs": ["s3", "t"], "outputs": ["f"]}]
    a = check(_run(arts, steps), min_reachable=DEFAULT_MIN_REACHABLE)
    assert a.reachable_share == 0.8                      # 4 derived, 1 tool result
    assert a.verdict != "partial" or "Below the" not in report(a)
    assert check(_run(arts, steps), min_reachable=0.8000001).verdict == "partial"


def test_an_empty_artifact_does_not_lift_the_share():
    """A blank artifact is not evidence of anything; counting it as the model's
    own words would let a recorder raise its score by writing nothing."""
    arts = [dict(SUM, artifact_id="s", content="Revenue grew 14%."),
            dict(SUM, artifact_id="blank", content="   "), TOOL, ANS]
    steps = [{"step_id": "s1", "kind": "summarize", "inputs": [], "outputs": ["s"]},
             {"step_id": "s2", "kind": "summarize", "inputs": ["s"], "outputs": ["blank"]},
             {"step_id": "s3", "kind": "tool", "inputs": ["s"], "outputs": ["t"]},
             {"step_id": "s4", "kind": "answer", "inputs": ["s", "t"], "outputs": ["f"]}]
    a = check(_run(arts, steps))
    assert a.derived == 3 and a.tool_results == 1 and a.empty_derived == 1
    assert a.judged_artifacts == 3 and a.reachable_share == 2 / 3
    assert "empty_artifact" in {f.code for f in a.findings}


def test_the_printed_share_is_floored_so_it_never_reads_above_the_line():
    arts = [dict(SUM, artifact_id=f"s{i}", content=f"Sentence number {i}.") for i in range(38)]
    arts += [dict(TOOL, artifact_id=f"t{i}", content=f"tool {i}") for i in range(10)]
    arts += [ANS]
    steps = [{"step_id": "g", "kind": "generate", "inputs": [],
              "outputs": [a["artifact_id"] for a in arts if a["kind"] != "tool_result"]},
             {"step_id": "t", "kind": "tool", "inputs": ["s0"],
              "outputs": [a["artifact_id"] for a in arts if a["kind"] == "tool_result"]}]
    a = check(_run(arts, steps), min_reachable=0.8)
    assert 0.795 < a.reachable_share < 0.8               # 40/50
    text = report(a)
    assert "  79%" in text and "Below the 80%" in text   # never "80% ... below 80%"
