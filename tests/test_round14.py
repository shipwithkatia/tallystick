"""Round 14: the verdict core and the exit codes (review 13, sections 1 and 2).

The reviewer's own tests are in test_proverka13_core.py and
test_proverka13_exit_codes.py. These cover what the fixes added beyond them,
each at a place no other test watches: the exact depth boundary, a claim cycle
too long for the stack, exit 2 for an unchecked chain from the command line,
the `_meta` fields the reviewer's six variants do not name, and a write that
must not remove a file it never opened.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

import tallystick.ledger as ledger
from tallystick import TraceError, audit, close_books, load_run
from tallystick.cli import main
from tallystick.ledger import MAX_DEPTH, ClaimStatus

ROOT = Path(__file__).resolve().parent.parent
BALANCED = json.loads((ROOT / "examples" / "balanced_run.json").read_text(encoding="utf-8"))
TEXT = "Canberra"


def _chain(links: int, *, final_id: str = "c_ans", prefix: str = "c"):
    """doc <- m1 <- ... <- m(links-1) <- ans, every hop quoting the one before.
    The answer's claim is `links - 1` claim-to-claim citations deep."""
    arts = [{"artifact_id": "doc", "kind": "document", "content": TEXT}]
    steps = [{"step_id": "s0", "kind": "retrieve", "inputs": [], "outputs": ["doc"]}]
    claims, entries, prev = [], [], "doc"
    for i in range(1, links + 1):
        last = i == links
        aid = "ans" if last else f"m{i}"
        arts.append({"artifact_id": aid, "kind": "final_answer" if last else "intermediate",
                     "content": TEXT})
        steps.append({"step_id": f"s{i}", "kind": "summarize", "inputs": [prev], "outputs": [aid]})
        cid = final_id if last else f"{prefix}{i:05d}"
        claims.append({"claim_id": cid, "artifact_id": aid, "start": 0, "end": len(TEXT)})
        entries.append({"entry_id": f"e{i}", "claim_id": cid,
                        "account": f"EVIDENCE:{prev}#0-{len(TEXT)}", "quoted_span": TEXT})
        prev = aid
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


# --------------------------------------------------------------------------- #
# 1.2 - depth is a property of the chain
# --------------------------------------------------------------------------- #


def test_the_depth_boundary_is_exact():
    """MAX_DEPTH claim-to-claim citations are walked; one more is not."""
    at = close_books(load_run(_chain(MAX_DEPTH + 1)))
    past = close_books(load_run(_chain(MAX_DEPTH + 2)))
    assert at.audits["c_ans"].status is ClaimStatus.GROUNDED
    assert past.audits["c_ans"].status is ClaimStatus.UNCHECKED


@pytest.mark.parametrize("final_id", ["a_final", "zz_final", "m_final"])
def test_a_chain_too_deep_is_unchecked_not_laundered_under_any_name(final_id):
    """Review 13, 1.2, second defect: running out of depth is "could not check".
    f731bdb: `a_final` came out LAUNDERED, `zz_final` GROUNDED."""
    b = close_books(load_run(_chain(300, final_id=final_id)))
    a = b.audits[final_id]
    assert a.status is ClaimStatus.UNCHECKED
    assert a.break_reason == f"chain deeper than {MAX_DEPTH}"
    assert [x.claim_id for x in b.unchecked()] == [final_id]
    assert b.injection_points() == []


def test_a_real_break_near_the_answer_is_still_a_verdict(tmp_path):
    """A chain too deep on one citation does not excuse a failing one beside it:
    the answer holds a second claim nothing funds, so the run exits 1, not 2."""
    t = _chain(300)
    t["artifacts"][-1]["content"] = TEXT + " Sydney."
    t["claims"].append({"claim_id": "c_bad", "artifact_id": "ans", "start": 9, "end": 16})
    path = tmp_path / "t.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) == 1


def test_a_chain_too_deep_exits_2_from_the_command_line(tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text(json.dumps(_chain(300)), encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) == 2
    assert "chain_too_deep" in capsys.readouterr().err
    out = tmp_path / "o.json"
    assert main(["audit", str(path), "--quiet", "--json", str(out)]) == 2
    gate = json.loads(out.read_text(encoding="utf-8"))["gate"]
    assert gate["exit_code"] == 2 and gate["reasons"] == ["chain_too_deep"]
    assert main(["audit", str(path)]) == 2
    assert "BOOKS NOT CHECKED" in capsys.readouterr().out


def test_a_claim_cycle_longer_than_the_stack_is_unchecked_not_a_crash():
    """1,200 claims citing round in a circle. The walk inside a cycle is as
    deep as the cycle, so the cycle counts as that deep: without it, this
    overflowed Python's stack."""
    n = 1200
    arts = [{"artifact_id": f"m{i}", "kind": "intermediate", "content": TEXT} for i in range(n)]
    arts.append({"artifact_id": "ans", "kind": "final_answer", "content": TEXT})
    steps = [{"step_id": f"s{i}", "kind": "summarize", "inputs": [f"m{(i + 1) % n}"],
              "outputs": [f"m{i}"]} for i in range(n)]
    steps.append({"step_id": "sa", "kind": "answer", "inputs": ["m0"], "outputs": ["ans"]})
    claims = [{"claim_id": f"c{i:04d}", "artifact_id": f"m{i}", "start": 0, "end": len(TEXT)}
              for i in range(n)] + [{"claim_id": "c_ans", "artifact_id": "ans", "start": 0,
                                    "end": len(TEXT)}]
    entries = [{"entry_id": f"e{i}", "claim_id": f"c{i:04d}",
                "account": f"EVIDENCE:m{(i + 1) % n}#0-{len(TEXT)}", "quoted_span": TEXT}
               for i in range(n)] + [{"entry_id": "ea", "claim_id": "c_ans",
                                     "account": f"EVIDENCE:m0#0-{len(TEXT)}", "quoted_span": TEXT}]
    b = close_books(load_run({"artifacts": arts, "steps": steps, "claims": claims,
                              "entries": entries}))
    assert b.audits["c_ans"].status is ClaimStatus.UNCHECKED


def _random_trace(rng: random.Random):
    """A small random run: derived artifacts citing earlier ones or a root,
    with an unsupported or prior-only claim here and there, and every
    artifact holding two claims so that a wide quote covers both."""
    n = rng.randint(3, 9)
    arts = [{"artifact_id": "doc", "kind": "document", "content": "AAAA BBBB"}]
    steps = [{"step_id": "s_doc", "kind": "retrieve", "inputs": [], "outputs": ["doc"]}]
    claims, entries = [], []
    for i in range(n):
        aid = "ans" if i == n - 1 else f"d{i}"
        arts.append({"artifact_id": aid, "kind": "final_answer" if aid == "ans" else "intermediate",
                     "content": "AAAA BBBB"})
        sources = ["doc"] + [f"d{j}" for j in range(i)]
        inputs = sorted(set(rng.sample(sources, k=min(len(sources), rng.randint(1, 3)))))
        steps.append({"step_id": f"s{i}", "kind": "summarize", "inputs": inputs, "outputs": [aid]})
        for part, (lo, hi) in enumerate(((0, 4), (5, 9))):
            cid = f"{aid}_{part}"
            claims.append({"claim_id": cid, "artifact_id": aid, "start": lo, "end": hi})
            roll = rng.random()
            if roll < 0.1:
                continue                                  # nothing funds it
            if roll < 0.15:
                entries.append({"entry_id": f"e_{cid}_p", "claim_id": cid, "account": "PRIOR:model"})
                continue
            for k, src in enumerate(rng.sample(inputs, k=rng.randint(1, len(inputs)))):
                a, b = rng.choice(((0, 4), (5, 9), (0, 9)))
                entries.append({"entry_id": f"e_{cid}_{k}", "claim_id": cid,
                                "account": f"EVIDENCE:{src}#{a}-{b}",
                                "quoted_span": "AAAA BBBB"[a:b]})
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


def _renamed(t, rng):
    ids = [c["claim_id"] for c in t["claims"]]
    new = [f"x{v:03d}" for v in rng.sample(range(1000), len(ids))]
    names = dict(zip(ids, new))
    d = copy.deepcopy(t)
    for c in d["claims"]:
        c["claim_id"] = names[c["claim_id"]]
    for e in d["entries"]:
        e["claim_id"] = names[e["claim_id"]]
    for key in ("artifacts", "steps", "claims", "entries"):
        rng.shuffle(d[key])
    return d, names


@pytest.mark.parametrize("seed", range(40))
def test_verdicts_do_not_depend_on_claim_names_or_array_order(seed, monkeypatch):
    """Random runs, with the depth limit lowered to 2 so that it is met, audited
    as written and again with every claim renamed and every array shuffled.
    Each claim must come out the same. f731bdb fails this on some seeds."""
    monkeypatch.setattr(ledger, "MAX_DEPTH", 2)
    rng = random.Random(seed)
    t = _random_trace(rng)
    d, names = _renamed(t, rng)
    a, b = close_books(load_run(t)), close_books(load_run(d))
    for cid, x in a.audits.items():
        y = b.audits[names[cid]]
        assert (x.status, x.depth) == (y.status, y.depth), (seed, cid)
    assert a.books_balance == b.books_balance


# --------------------------------------------------------------------------- #
# 1.3 / 1.4 - a root made from the citing step's own output
# --------------------------------------------------------------------------- #


def _looped(through: int):
    """The answer step takes t1 and cites it; t1 is made from the answer
    `through` steps further on (0: the answer step outputs t1 itself)."""
    ans = "The capital of Australia is Sydney."
    arts = [{"artifact_id": "doc", "kind": "document", "content": "Canberra is the capital."},
            {"artifact_id": "ans", "kind": "final_answer", "content": ans},
            {"artifact_id": "t1", "kind": "tool_result", "content": ans}]
    steps = [{"step_id": "s1", "kind": "retrieve", "inputs": [], "outputs": ["doc"]},
             {"step_id": "s2", "kind": "answer", "inputs": ["doc", "t1"],
              "outputs": ["ans", "t1"] if through == 0 else ["ans"]}]
    prev = "ans"
    for k in range(1, through + 1):
        out = "t1" if k == through else f"mid{k}"
        if out != "t1":
            arts.append({"artifact_id": out, "kind": "intermediate", "content": ans})
        steps.append({"step_id": f"s_loop{k}", "kind": "tool", "inputs": [prev], "outputs": [out]})
        prev = out
    return {"artifacts": arts, "steps": steps,
            "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": len(ans)}],
            "entries": [{"entry_id": "e1", "claim_id": "c1", "account": f"EVIDENCE:t1#0-{len(ans)}",
                         "quoted_span": ans}]}


@pytest.mark.parametrize("through", [0, 1, 3])
def test_a_root_made_from_the_citing_step_does_not_fund_it(through):
    run = load_run(_looped(through))
    b = close_books(run)
    assert run.entries[0].reason == "self_citation"
    assert not b.books_balance


def test_a_root_the_step_did_not_make_still_funds_it():
    """The control: the same shape with the loop cut funds as before."""
    t = _looped(3)
    t["steps"][-1]["inputs"] = ["doc"]            # t1 is no longer made from the answer
    b = close_books(load_run(t))
    assert b.books_balance


# --------------------------------------------------------------------------- #
# 2.4 - `_meta` beyond the reviewer's six fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("meta", [
    5,
    {"echo_warning_details": 5},
    {"echo_warnings_cleared_by_declaration": "x"},
    {"otel": {"notes": 5}},
    {"unmatched_tool_results": {"a": 1}},
])
@pytest.mark.parametrize("command", ["audit", "check-trace"])
def test_a_meta_field_the_gate_reads_is_refused_when_malformed(tmp_path, meta, command):
    """Skipping a field the echo gate reads, because it had the wrong type,
    would be a gate that opens on malformed input. f731bdb: `_meta: 5` and a
    non-list `echo_warning_details` were passed over and the run exited 0."""
    t = copy.deepcopy(BALANCED)
    t["_meta"] = meta
    path = tmp_path / "t.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    assert main([command, str(path), "--quiet"]) == 2
    with pytest.raises(TraceError):
        audit(t)


def test_a_well_formed_meta_still_reads(tmp_path):
    t = copy.deepcopy(BALANCED)
    t["_meta"] = {"notes": None, "truncated": [], "otel": {"notes": ["x"]}, "source": "hand"}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    assert main(["audit", str(path), "--quiet"]) == 0
    assert main(["check-trace", str(path), "--quiet"]) == 0


# --------------------------------------------------------------------------- #
# 2.3 - a failed write removes only what it started
# --------------------------------------------------------------------------- #


def test_a_write_refused_at_open_leaves_the_existing_file_alone(tmp_path):
    out = tmp_path / "keep.json"
    out.write_text('{"mine": true}', encoding="utf-8")
    out.chmod(0o444)
    try:
        code = main(["convert", str(ROOT / "examples" / "logs" / "clean_run.json"),
                     "-o", str(out), "--quiet"])
        assert code == 2
        assert out.read_text(encoding="utf-8") == '{"mine": true}'
    finally:
        out.chmod(0o644)
