"""proverka13, section 1: the verdict core.

Each test states what the core should do and fails on f731bdb. None of these
shapes is produced by the adapters in this repository (checked on the 695
AgentHallu files through `adapters/agenthallu.py`: 0 duplicate step ids, 0
artifacts that are both an input and an output of one step, 0 inputs produced
by a later step). They are reachable by anyone who writes the plain JSON format
by hand or with their own recorder, which is what io.py invites.
"""

from __future__ import annotations

import copy
import json

from tallystick import audit
from tallystick.cli import main

ANSWER = "The capital of Australia is Sydney."
DOC = "Canberra is the capital of Australia."
N = len(ANSWER)


def _base():
    return {
        "artifacts": [
            {"artifact_id": "doc", "kind": "document", "content": DOC},
            {"artifact_id": "ans", "kind": "final_answer", "content": ANSWER},
        ],
        "steps": [
            {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
            {"step_id": "s2", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]},
        ],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": N}],
        "entries": [],
    }


def _cite_t1():
    return [{"entry_id": "e1", "claim_id": "c1",
             "account": f"EVIDENCE:t1#0-{N}", "quoted_span": ANSWER}]


def _exit(tmp_path, trace, name="t.json"):
    path = tmp_path / name
    path.write_text(json.dumps(trace), encoding="utf-8")
    return main(["audit", str(path), "--quiet"])


def test_duplicate_step_id_verdict_does_not_depend_on_step_order(tmp_path):
    """Two steps share the id s2 and both claim `ans`. validate() allows it,
    producing_step() takes the first in file order - so the order of the
    `steps` array decides the verdict. f731bdb: exit 1 in one order, exit 0
    (BOOKS BALANCE on the model's own words) in the other."""
    t = _base()
    t["artifacts"].append({"artifact_id": "t1", "kind": "tool_result", "content": ANSWER})
    t["steps"] = [
        {"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["doc", "t1"]},
        {"step_id": "s2", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]},
        {"step_id": "s2", "kind": "answer", "inputs": ["t1"], "outputs": ["ans"]},
    ]
    t["entries"] = _cite_t1()
    swapped = copy.deepcopy(t)
    swapped["steps"][1], swapped["steps"][2] = swapped["steps"][2], swapped["steps"][1]
    assert _exit(tmp_path, t, "a.json") == _exit(tmp_path, swapped, "b.json")


def _deep_chain(hops: int, final_first: bool):
    arts = [{"artifact_id": "doc", "kind": "document", "content": "Canberra"}]
    steps = [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]}]
    claims, entries, prev = [], [], "doc"
    for i in range(hops):
        last = i == hops - 1
        aid = "ans" if last else f"m{i}"
        arts.append({"artifact_id": aid, "kind": "final_answer" if last else "intermediate",
                     "content": "Canberra"})
        steps.append({"step_id": f"s{i + 1}", "kind": "summarize",
                      "inputs": [prev], "outputs": [aid]})
        if final_first:
            cid = "a_final" if last else f"z{i:04d}"
        else:
            cid = "zz_final" if last else f"c{i:04d}"
        claims.append({"claim_id": cid, "artifact_id": aid, "start": 0, "end": 8})
        entries.append({"entry_id": f"e{i}", "claim_id": cid,
                        "account": f"EVIDENCE:{prev}#0-8", "quoted_span": "Canberra"})
        prev = aid
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


def test_renaming_claim_ids_does_not_change_the_verdict():
    """The same 300-hop honest chain, only the claim ids differ. close_books
    resolves claims in sorted-id order and memoises, so MAX_DEPTH (256) is
    measured from wherever the walk entered. f731bdb: final claim resolved last
    -> grounded, books balance; resolved first -> laundered, books do not."""
    a = audit(_deep_chain(300, final_first=False))
    b = audit(_deep_chain(300, final_first=True))
    assert a.books_balance == b.books_balance


def test_a_tool_result_made_from_the_answer_cannot_ground_the_answer(tmp_path):
    """A cycle through a root: step s3 takes the answer as input and outputs
    t1; the answer step takes t1 as input and cites it. The chain stops at the
    root, so the cycle guard never sees it. f731bdb: audit exit 0, and
    check-trace reports no defect (exit 0)."""
    t = _base()
    t["artifacts"].append({"artifact_id": "t1", "kind": "tool_result", "content": ANSWER})
    t["steps"][1]["inputs"] = ["doc", "t1"]
    t["steps"].append({"step_id": "s3", "kind": "tool", "inputs": ["ans"], "outputs": ["t1"]})
    t["entries"] = _cite_t1()
    assert _exit(tmp_path, t) != 0


def test_a_step_cannot_cite_a_root_it_produced_itself(tmp_path):
    """The answer step lists t1 as its input AND its output, and cites it.
    SELF_CITATION only compares the claim's artifact with the cited one.
    f731bdb: audit exit 0; check-trace exit 0 with only a `mixed_outputs` note."""
    t = _base()
    t["artifacts"].append({"artifact_id": "t1", "kind": "tool_result", "content": ANSWER})
    t["steps"][1] = {"step_id": "s2", "kind": "answer",
                     "inputs": ["doc", "t1"], "outputs": ["ans", "t1"]}
    t["entries"] = _cite_t1()
    assert _exit(tmp_path, t) != 0
