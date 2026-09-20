"""The reason a chain broke is a property of the trace, not of the file's layout.

ledger.py's module docstring promises that results do not depend on the order of
arrays in the input file. The verdict held - 2,000 permutations of the shipped
examples give the same statuses - but the sentence under it did not: the reason
was `entries[0].reason`, the first element of the entries array. One claim with
three rejected entries reported `artifact_unknown`, `span_mismatch` or
`prior_never_funds` depending only on how the file happened to list them, and
that string reaches the terminal, `--chain` and `--json`.

Now the earliest gate that failed is named, in the order verify.REASON_ORDER
gives, whatever the file's order.
"""
from __future__ import annotations

from itertools import permutations

from tallystick import close_books, load_run
from tallystick.verify import REASON_ORDER, worst_reason

DOC = "The filing reports revenue of 14 million euro for the year."
ANSWER = "Revenue was 14 million euro."

#: Three entries under one claim, each rejected by a different gate:
#: a citation into an artifact that is not in the trace, a quote that is not at
#: the address it names, and a model prior, which never funds anything.
ENTRIES = [
    {"entry_id": "e_unknown", "claim_id": "c1", "account": "EVIDENCE:nowhere#0-10",
     "quoted_span": "The filing"},
    {"entry_id": "e_mismatch", "claim_id": "c1", "account": f"EVIDENCE:doc#0-{len(DOC)}",
     "quoted_span": "Revenue was 91 million euro for the year, the filing reports."},
    {"entry_id": "e_prior", "claim_id": "c1", "account": "PRIOR:model",
     "quoted_span": ""},
]


def _trace(entries) -> dict:
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": DOC},
                      {"artifact_id": "ans", "kind": "final_answer", "content": ANSWER}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": len(ANSWER)}],
        "entries": list(entries),
    }


def test_every_order_of_the_same_entries_gives_the_same_reason():
    reasons = set()
    for order in permutations(ENTRIES):
        balance = close_books(load_run(_trace(order)))
        assert balance.books_balance is False
        reasons.add(balance.audits["c1"].break_reason)
    assert len(reasons) == 1, f"the file's order decided the reason: {sorted(reasons)}"


def test_the_reason_named_is_the_earliest_gate_that_failed():
    """Of the three, the citation into an artifact that does not exist is the
    most fundamental: nothing about the quote or the prior can be assessed until
    the cited thing is there."""
    balance = close_books(load_run(_trace(ENTRIES)))
    assert balance.audits["c1"].break_reason == "artifact_unknown"


def test_worst_reason_ranks_by_the_gate_order_and_ignores_ok():
    class _E:
        def __init__(self, reason):
            self.reason = reason

    assert worst_reason([_E("span_mismatch"), _E("artifact_unknown")]) == "artifact_unknown"
    assert worst_reason([_E("ok"), _E("span_mismatch")]) == "span_mismatch"
    assert worst_reason([_E("ok")]) == "no entry posted"
    assert worst_reason([]) == "no entry posted"
    # An unknown string does not crash the report; it simply ranks last.
    assert worst_reason([_E("something_new")]) == "something_new"
    assert worst_reason([_E("something_new"), _E("span_mismatch")]) == "span_mismatch"


def test_the_gate_order_covers_every_reason_the_verifier_can_give():
    """A new rejection reason added to verify.py without a place in REASON_ORDER
    would silently rank last. Fail here instead."""
    import tallystick.verify as verify

    named = {value for key, value in vars(verify).items()
             if key.isupper() and isinstance(value, str)
             and key not in {"OK", "__doc__"} and not key.startswith("_")}
    assert named - {"ok"} <= set(REASON_ORDER)
