"""Behavioural tests for the audit.

Each test states, in its name, a property the library promises. If one of these
fails, a claim in the README has become false.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tallystick import audit, load_run
from tallystick.ledger import ClaimStatus, close_books
from tallystick.verify import NOT_REACHABLE, SPAN_MISMATCH, verify_run

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "laundered_summary.json"


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The headline behaviour
# --------------------------------------------------------------------------- #


def test_a_verbatim_quote_of_an_invented_summary_is_laundered(data):
    """The claim every one-hop checker passes must fail here."""
    balance = audit(data)
    assert balance.audits["ans_3"].status is ClaimStatus.LAUNDERED
    assert not balance.books_balance


def test_the_break_is_attributed_to_the_step_that_invented_the_content(data):
    """Naming the step is the practical output; a score would not locate it."""
    audit_3 = audit(data).audits["ans_3"]
    assert audit_3.break_step_id == "s3"
    assert audit_3.break_claim_id == "sum_4"


def test_the_last_hop_of_a_laundered_claim_verifies_perfectly(data):
    """The quote really is there. That is exactly why one hop is not enough."""
    run = load_run(data)
    verify_run(run)
    je7 = next(e for e in run.entries if e.entry_id == "je_7")
    assert je7.verified is True


def test_genuinely_grounded_claims_reach_a_root(data):
    balance = audit(data)
    for cid in ("ans_1", "ans_2"):
        a = balance.audits[cid]
        assert a.status is ClaimStatus.GROUNDED
        assert a.depth == 2, "answer -> summary -> document"


def test_laundering_rate_counts_only_the_hidden_failures(data):
    assert audit(data).laundering_rate == pytest.approx(1 / 3)


# --------------------------------------------------------------------------- #
# The conservation rule
# --------------------------------------------------------------------------- #


def test_a_step_cannot_cite_an_artifact_it_never_received(data):
    """The novel gate: reachability, checked without reading the text at all."""
    d = copy.deepcopy(data)
    for step in d["steps"]:
        if step["step_id"] == "s4":
            step["inputs"] = []          # the answer step now sees nothing
    run = load_run(d)
    verify_run(run)
    assert all(e.reason == NOT_REACHABLE
               for e in run.entries if e.claim_id.startswith("ans_"))
    assert close_books(run).coverage == 0.0


def test_a_fabricated_quote_is_rejected(data):
    d = copy.deepcopy(data)
    for e in d["entries"]:
        if e["entry_id"] == "je_1":
            e["quoted_span"] = "Revenue grew 40% year over year."
    run = load_run(d)
    verify_run(run)
    je1 = next(e for e in run.entries if e.entry_id == "je_1")
    assert je1.verified is False and je1.reason == SPAN_MISMATCH


def test_typographic_drift_in_a_quote_is_forgiven(data):
    """Curly quotes and an em dash must not break an otherwise honest citation."""
    d = copy.deepcopy(data)
    for e in d["entries"]:
        if e["entry_id"] == "je_2":
            e["quoted_span"] = ("Operating margin was 11.2%, down from 12.9% "
                                "in the prior‑year period.")
    run = load_run(d)
    verify_run(run)
    assert next(e for e in run.entries if e.entry_id == "je_2").verified is True


def test_prior_never_funds_a_claim(data):
    balance = audit(data)
    assert balance.audits["sum_4"].status is ClaimStatus.PRIOR_ONLY


# --------------------------------------------------------------------------- #
# Determinism — the property the whole thesis rests on
# --------------------------------------------------------------------------- #


def test_repeated_audits_are_identical(data):
    runs = [audit(copy.deepcopy(data)) for _ in range(5)]
    signatures = {
        json.dumps({cid: (a.status.value, a.depth, a.break_step_id)
                    for cid, a in b.audits.items()}, sort_keys=True)
        for b in runs
    }
    assert len(signatures) == 1


# --------------------------------------------------------------------------- #
# Adversarial cases found in review — each one was a real hole
# --------------------------------------------------------------------------- #


def test_a_wide_citation_inherits_the_worst_claim_it_covers(data):
    """Citing the whole summary must not launder the one bad sentence in it."""
    d = copy.deepcopy(data)
    summary = next(a for a in d["artifacts"] if a["artifact_id"] == "summary")
    for e in d["entries"]:
        if e["entry_id"] == "je_7":
            e["account"] = f"EVIDENCE:summary#0-{len(summary['content'])}"
            e["quoted_span"] = summary["content"]
    balance = audit(d)
    assert balance.audits["ans_3"].status is ClaimStatus.LAUNDERED
    assert balance.audits["ans_3"].break_claim_id == "sum_4"


def test_a_citation_into_unclaimed_text_does_not_fund(data):
    """Text no claim vouches for cannot be evidence, however real the quote."""
    d = copy.deepcopy(data)
    d["claims"] = [c for c in d["claims"] if c["claim_id"] != "sum_4"]
    d["entries"] = [e for e in d["entries"] if e["claim_id"] != "sum_4"]
    balance = audit(d)
    assert balance.audits["ans_3"].status is ClaimStatus.LAUNDERED
    assert balance.audits["ans_3"].break_step_id == "s3"


@pytest.mark.parametrize("seed", [1, 7, 42, 1234])
def test_verdict_does_not_depend_on_array_order(data, seed):
    """Shuffle every array in the file; the balance must not move."""
    import random as _r  # test-only; the library itself never imports it
    d = copy.deepcopy(data)
    rng = _r.Random(seed)
    for key in ("artifacts", "steps", "claims", "entries"):
        rng.shuffle(d[key])
    a, b = audit(data), audit(d)
    sig = lambda bal: {cid: (x.status.value, x.depth, x.break_step_id)
                       for cid, x in bal.audits.items()}
    assert sig(a) == sig(b)
    assert a.books_balance == b.books_balance


def test_an_empty_trace_does_not_balance():
    balance = audit({"artifacts": [], "steps": [], "claims": [], "entries": []})
    assert balance.books_balance is False


def test_a_trace_with_no_final_answer_does_not_balance(data):
    d = copy.deepcopy(data)
    d["artifacts"] = [a for a in d["artifacts"] if a["kind"] != "final_answer"]
    d["steps"] = [s for s in d["steps"] if s["step_id"] != "s4"]
    d["claims"] = [c for c in d["claims"] if not c["claim_id"].startswith("ans_")]
    d["entries"] = [e for e in d["entries"] if not e["claim_id"].startswith("ans_")]
    assert audit(d).books_balance is False


def test_an_undeclared_assumption_is_not_a_free_pass(data):
    d = copy.deepcopy(data)
    d["entries"].append({"entry_id": "je_x", "claim_id": "sum_4",
                         "account": "ASSUMPTION:guidance_is_fine"})
    assert audit(d).audits["ans_3"].status is ClaimStatus.LAUNDERED

    d["assumptions"] = {"guidance_is_fine": "Treat Q4 guidance as given."}
    assert audit(d).audits["ans_3"].status is ClaimStatus.ASSUMED


def test_short_quotes_get_no_free_edit(data):
    """'14%' cited as '44%' must fail. Tolerance is proportional, with no floor."""
    d = copy.deepcopy(data)
    d["claims"].append({"claim_id": "sum_x", "artifact_id": "summary",
                        "start": 13, "end": 16})            # "14%"
    d["entries"].append({"entry_id": "je_x", "claim_id": "sum_x",
                         "account": "EVIDENCE:doc_filing#95-98",
                         "quoted_span": "44%"})
    run = load_run(d)
    verify_run(run)
    assert next(e for e in run.entries if e.entry_id == "je_x").verified is False


def test_a_cycle_is_reported_not_looped():
    d = {
        "artifacts": [
            {"artifact_id": "a", "kind": "intermediate", "content": "alpha."},
            {"artifact_id": "b", "kind": "intermediate", "content": "beta."},
            {"artifact_id": "f", "kind": "final_answer", "content": "alpha."},
        ],
        "steps": [
            {"step_id": "s1", "kind": "generate", "inputs": ["b"], "outputs": ["a"]},
            {"step_id": "s2", "kind": "generate", "inputs": ["a"], "outputs": ["b"]},
            {"step_id": "s3", "kind": "answer", "inputs": ["a"], "outputs": ["f"]},
        ],
        "claims": [
            {"claim_id": "ca", "artifact_id": "a", "start": 0, "end": 6},
            {"claim_id": "cb", "artifact_id": "b", "start": 0, "end": 5},
            {"claim_id": "cf", "artifact_id": "f", "start": 0, "end": 6},
        ],
        "entries": [
            {"entry_id": "e1", "claim_id": "ca", "account": "EVIDENCE:b#0-5", "quoted_span": "beta."},
            {"entry_id": "e2", "claim_id": "cb", "account": "EVIDENCE:a#0-6", "quoted_span": "alpha."},
            {"entry_id": "e3", "claim_id": "cf", "account": "EVIDENCE:a#0-6", "quoted_span": "alpha."},
        ],
    }
    balance = audit(d)
    assert balance.audits["cf"].status is ClaimStatus.LAUNDERED
    assert balance.books_balance is False


def test_malformed_input_raises_a_located_error(data):
    from tallystick import TraceError
    d = copy.deepcopy(data)
    d["entries"][0]["account"] = "EVIDENCE:doc_filing"
    with pytest.raises(TraceError, match="je_1|malformed"):
        audit(d)
    d = copy.deepcopy(data)
    d["claims"][0]["end"] = 99999
    with pytest.raises(TraceError, match="sum_1"):
        audit(d)


def test_cli_exit_codes(tmp_path):
    """0 = balances, 1 = does not, 2 = cannot read the trace."""
    from tallystick.cli import main
    assert main([str(EXAMPLE), "--quiet"]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([str(bad), "--quiet"]) == 2
    assert main([str(tmp_path / "missing.json"), "--quiet"]) == 2
