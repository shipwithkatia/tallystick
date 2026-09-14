"""Figures quoted in the commit message and the docstring, pinned to a recount.

Written when every one of them was wrong; all now hold, and they stay so a
later edit cannot quietly drift again. Each fails when the text and the
recount on the corpus disagrees. They check claims, not behaviour - when a
claim is corrected, update the constant here to the recounted value (or delete
the test) in the same commit.

Needs the AgentHallu corpus, which is not in git. Point TALLYSTICK_AGENTHALLU
at it, or keep it at bench/work-agenthallu/AgentHallu; without it these skip.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import openai_roundtrip as rt  # noqa: E402

from tallystick.adapters import agenthallu  # noqa: E402
from tallystick.adapters import openai_chat as oc  # noqa: E402

CORPUS = Path(os.environ.get("TALLYSTICK_AGENTHALLU",
                             ROOT / "bench" / "work-agenthallu" / "AgentHallu"))

pytestmark = pytest.mark.skipif(
    not any(CORPUS.rglob("*.json")) if CORPUS.is_dir() else True,
    reason=f"AgentHallu corpus not found at {CORPUS}")


@pytest.fixture(scope="module")
def files():
    return sorted(CORPUS.rglob("*.json"))


@pytest.fixture(scope="module")
def trajectories(files):
    out = []
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(obj, dict) and "history" in obj:
            out.append((path, obj))
    return out


@pytest.fixture(scope="module")
def rule_calls(trajectories):
    """Every call the reader makes to the echo rule on the headline run, with
    the tool it was made for. Calls line up with the `t*` artifacts in order:
    the rule is asked once per non-empty tool message, and each of those
    becomes exactly one artifact."""
    real = oc._hands_back_what_it_was_given
    out = []
    for path, obj in trajectories:
        seen = []

        def spy(result, args):
            verdict = real(result, args)
            seen.append((result, args, verdict))
            return verdict

        oc._hands_back_what_it_was_given = spy
        try:
            trace = oc.to_trace(rt.render(obj), name=path.name,
                                model_text_tools=agenthallu.ECHO_TOOLS)
        finally:
            oc._hands_back_what_it_was_given = real
        tools = [a["title"] for a in trace["artifacts"] if a["artifact_id"].startswith("t")]
        assert len(tools) == len(seen), path
        out += [(t, r, a, v) for t, (r, a, v) in zip(tools, seen)]
    return out


def _fires(rule_calls):
    return [(t, r, a) for t, r, a, v in rule_calls if v]


def test_committed_results_file_is_what_the_bench_prints(files, tmp_path, capsys):
    out = tmp_path / "openai-roundtrip.txt"
    assert rt.main([str(CORPUS), "--out", str(out)]) == 0
    capsys.readouterr()
    committed = (ROOT / "bench" / "results" / "openai-roundtrip.txt").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed


def test_commit_message_headline_582_of_693_identical(trajectories):
    same = sum(
        rt.shape(agenthallu.to_trace(obj, name=p.name))
        == rt.shape(oc.to_trace(rt.render(obj), name=p.name,
                                model_text_tools=agenthallu.ECHO_TOOLS))
        for p, obj in trajectories)
    # proverka6: 666 -> 641. proverka7: 641 -> 582. The rule now demotes on an
    # exact value match with the answering call - the whole reply, one of its
    # lines, its single JSON value - and warns on anything short of that, so it
    # reaches echoes the native reader's own 8-character guard let through.
    assert (same, len(trajectories)) == (582, 693)


def test_commit_message_ids_stripped_agreement_is_85_percent(files, trajectories):
    same, _flips = rt._without_ids(files)
    assert f"{same / len(trajectories):.0%}" == "85%", f"{same}/{len(trajectories)}"


def test_commit_message_no_flags_agreement_is_61_percent(files, trajectories):
    same = rt._bare(files)
    assert f"{same / len(trajectories):.0%}" == "61%", f"{same}/{len(trajectories)}"


def test_commit_message_rule_fires_313_times(rule_calls):
    fires = _fires(rule_calls)
    declared = sum(t in agenthallu.ECHO_TOOLS for t, _r, _a in fires)
    counts = {"all": len(fires), "on declared echo tools": declared,
              "on other tools": len(fires) - declared}
    assert 313 in counts.values(), counts


def test_commit_message_rule_alone_catches_136_of_460_echo_tool_results(rule_calls):
    echo = [v for t, _r, _a, v in rule_calls if t in agenthallu.ECHO_TOOLS]
    assert (sum(echo), len(echo)) == (136, 460)


def test_docstring_173_fires_are_not_the_whole_reply(rule_calls):
    # openai_chat._reply_values: the demotion asks whether the reply, one of its
    # lines, or its single JSON value IS a value the call carried. This counts
    # the fires where the WHOLE reply was not itself that value - the ones the
    # narrower "the whole reply and nothing else" reading would have left to the
    # warning path. The escaping this file used to check here has its own tests
    # now, in tests/test_openai_chat_proverka6_findings.py, which need no corpus:
    # a corpus test cannot guard a boundary for anyone who clones the repository.
    not_whole = sum(oc._norm(r) not in oc._arg_values(a) for _t, r, a in _fires(rule_calls))
    assert not_whole == 173


def test_docstring_35_short_answers_handed_back(rule_calls):
    # openai_chat._hands_back_what_it_was_given: requiring ECHO_MIN_CHARS "would
    # launder every short answer handed back by an interpreter - 19 of them".
    short = sum(len(oc._matched_line(r)) < oc.ECHO_MIN_CHARS
                for _t, r, _a in _fires(rule_calls))
    assert short == 35
