"""Round 17: the edges of the review-16 fixes that the review's tests do not reach.

The review's tests (test_proverka16_findings.py) say the claim walk must not be
exponential on a clique, an assumption must not hide under "grounded", a Tibetan
note in quotes must warn, and an empty corpus must not print zeros with exit 0.
Each of those passes on a fix that is wrong in a way they do not look at:

* settling a loop by sweeping it until nothing changes is not exponential, and
  still costs the square of the loop when the one document sits at the far end;
  recomputing every claim that cites a closed one costs the square on a clique;
* taking the worst closing member for a wide quote, and the best one for a group
  of entries from one quote, passes the review's example and leaves the group;
* (a long Tibetan chat cut at its own syllables came back `unchecked`; that
  test went with the echo warnings in round 18);
* a folder with trajectories and no label prints a table of 0/0 as well, and
  strict_mode.py divides by zero like echo_coverage.py did.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import tallystick.ledger as ledger
from tallystick import load_run

ROOT = Path(__file__).resolve().parents[1]


# --- 1.1: settling a loop costs what the loop is, whatever the names ------------


def _loop(shape: str, n: int, reverse: bool):
    """n intermediates in a loop of claims; one claim also cites a document, so
    the whole loop closes. `ring`: each claim cites the next. `clique`: each
    cites every other. The document is cited by the claim that sorts LAST
    (`reverse` flips which one that is), the order a sweep handles worst."""
    t = "alpha"
    ids = [f"m{i:04d}" for i in range(n)]
    cids = [f"{'z' if reverse else 'a'}{(n - i) if reverse else i:05d}" for i in range(n)]
    cites = ({i: [(i + 1) % n] for i in range(n)} if shape == "ring"
             else {i: [j for j in range(n) if j != i] for i in range(n)})
    grounded = max(range(n), key=lambda i: cids[i]) if shape == "clique" else n // 2
    entries = [{"entry_id": f"e{i}_{j}", "claim_id": cids[i],
                "account": f"EVIDENCE:{ids[j]}#0-5", "quoted_span": t}
               for i in range(n) for j in cites[i]]
    entries.append({"entry_id": "doc", "claim_id": cids[grounded],
                    "account": "EVIDENCE:doc#0-5", "quoted_span": t})
    entries.append({"entry_id": "ans", "claim_id": "answer",
                    "account": f"EVIDENCE:{ids[0]}#0-5", "quoted_span": t})
    return {
        "artifacts": ([{"artifact_id": "doc", "kind": "document", "content": t}]
                      + [{"artifact_id": m, "kind": "intermediate", "content": t} for m in ids]
                      + [{"artifact_id": "ans", "kind": "final_answer", "content": t}]),
        "steps": ([{"step_id": f"s{i}", "kind": "k",
                    "inputs": sorted({ids[j] for j in cites[i]} | {"doc"}), "outputs": [ids[i]]}
                   for i in range(n)]
                  + [{"step_id": "sa", "kind": "answer", "inputs": [ids[0]], "outputs": ["ans"]}]),
        "claims": ([{"claim_id": cids[i], "artifact_id": ids[i], "start": 0, "end": 5}
                    for i in range(n)]
                   + [{"claim_id": "answer", "artifact_id": "ans", "start": 0, "end": 5}]),
        "entries": entries,
    }


def test_settling_a_loop_recomputes_only_what_can_improve(monkeypatch):
    """Calls to `_resolve`, for a loop that closes through one document. This
    round's code: 2n on both shapes, both name orders. A sweep until nothing
    changes: n*n on the ring when the document sits at the far end (62,502 for
    250 claims, 7 s with 2,000-character texts). Recomputing every claim that
    cites a closed one: n*n on the clique (26 s for 80 claims of 2,000
    characters). The review's clique has no document, so it closes nothing and
    sees neither."""
    calls = [0]
    real = ledger._resolve

    def counted(*a, **kw):
        calls[0] += 1
        return real(*a, **kw)

    monkeypatch.setattr(ledger, "_resolve", counted)
    for shape, n in (("ring", 120), ("clique", 40)):
        for reverse in (False, True):
            calls[0] = 0
            balance = ledger.close_books(load_run(_loop(shape, n, reverse)))
            assert balance.audits["answer"].status is ledger.ClaimStatus.GROUNDED
            assert calls[0] <= 3 * n, f"{shape} of {n}, reverse={reverse}: {calls[0]} calls"


# --- 1.2: a group from one quote closes on its worst member too ------------------


def test_a_group_over_a_grounded_and_an_assumed_sentence_is_assumed():
    """The review's example with the answer's one quote snapped to the two claim
    boundaries, as the proposer posts it: two entries in one group. The all-of
    over a group used to close on the best closing member; the fuzzer of review
    16 still found 2 renamings in 20,000 that swapped grounded and assumed with
    only the wide-quote place fixed."""
    t = {
        "artifacts": [
            {"artifact_id": "doc", "kind": "document", "content": "Revenue rose 14%."},
            {"artifact_id": "sum", "kind": "intermediate",
             "content": "Revenue rose 14%. Next year will be a record."},
            {"artifact_id": "ans", "kind": "final_answer",
             "content": "Revenue rose 14%. Next year will be a record."}],
        "steps": [{"step_id": "s1", "kind": "summarize", "inputs": ["doc"], "outputs": ["sum"]},
                  {"step_id": "s2", "kind": "answer", "inputs": ["sum"], "outputs": ["ans"]}],
        "assumptions": {"guess": "management's optimism"},
        "claims": [{"claim_id": "s_a", "artifact_id": "sum", "start": 0, "end": 17},
                   {"claim_id": "s_b", "artifact_id": "sum", "start": 18, "end": 45},
                   {"claim_id": "ans_1", "artifact_id": "ans", "start": 0, "end": 45}],
        "entries": [
            {"entry_id": "e1", "claim_id": "s_a", "account": "EVIDENCE:doc#0-17",
             "quoted_span": "Revenue rose 14%."},
            {"entry_id": "e2", "claim_id": "s_b", "account": "ASSUMPTION:guess"},
            {"entry_id": "e3", "claim_id": "ans_1", "account": "EVIDENCE:sum#0-17",
             "quoted_span": "Revenue rose 14%.", "group": "q1"},
            {"entry_id": "e4", "claim_id": "ans_1", "account": "EVIDENCE:sum#18-45",
             "quoted_span": "Next year will be a record.", "group": "q1"}],
    }
    balance = ledger.close_books(load_run(t))
    assert balance.audits["ans_1"].status is ledger.ClaimStatus.ASSUMED
    assert balance.books_balance


# --- 4.1, 5.2: nothing found is said, not counted --------------------------------


def test_auditability_script_on_trajectories_with_no_label_does_not_print_zeros(tmp_path):
    """One committed sample trajectory that is not labelled a hallucination, in
    the layout the script expects: it found a trajectory, and there is still
    nothing to count. It used to print a table of 0/0 and exit 0."""
    data = tmp_path / "AgentHallu"
    (data / "Camel").mkdir(parents=True)
    shutil.copy(ROOT / "bench" / "sample-agenthallu" / "Camel" / "078.json", data / "Camel")
    result = subprocess.run([sys.executable, str(ROOT / "bench" / "auditability_agenthallu.py"),
                             "--data", str(data), "--draws", "10"],
                            capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert result.returncode == 2, result.stdout[:300]
    assert "none of them labelled" in result.stderr


def test_strict_mode_script_without_the_corpus_says_so_instead_of_crashing(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "bench" / "strict_mode.py"),
                             str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 2, result.stderr[-300:]
    assert "Traceback" not in result.stderr
    assert "git clone https://github.com/liuxuannan/AgentHallu" in result.stderr
