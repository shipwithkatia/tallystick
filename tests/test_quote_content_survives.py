"""The quote gate forgives typography and nothing else.

Both failing cases here passed before this file existed. The gate used an edit
budget of 2% of the span's length with no ceiling, so a long quote bought edits:

    a 308-character source saying "12.4 million euro" could be cited as
    "92.4 million euro" and the run reported BOOKS BALANCE, exit 0;

    a 2,645-character source had its closing sentence replaced outright -
    "no adverse events in the trial" cited as "safe and effective for all
    patients" - and balanced too.

A cap on the budget would not close this: one edit moves a digit. So the budget
is gone. What a quote says - letters, digits, and separators inside numbers -
must survive character for character; spacing and punctuation need not.
"""
from __future__ import annotations

import pytest

from tallystick import close_books, load_run

SOURCE = (
    "The independent auditor reviewed the consolidated statements for the fiscal "
    "year and confirmed that group revenue reached 12.4 million euro, that the "
    "headcount at year end stood at 310 employees, and that no material weaknesses "
    "in internal controls were identified during the engagement period under review."
)


def _one_hop(doc: str, answer: str, quote: str) -> dict:
    """doc -> answer, one claim over the whole answer, one credit quoting the doc."""
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer", "content": answer}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": len(answer)}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#0-{len(doc)}", "quoted_span": quote}],
    }


def _verdict(doc: str, answer: str, quote: str):
    balance = close_books(load_run(_one_hop(doc, answer, quote)))
    return balance.books_balance, balance.audits["c1"].break_reason


def test_a_digit_changed_in_a_long_quote_does_not_balance():
    """The reviewer's case: 12.4 million cited as 92.4 million, 308 characters."""
    wrong = SOURCE.replace("12.4", "92.4")
    balances, reason = _verdict(SOURCE, "Group revenue reached 92.4 million euro.", wrong)
    assert balances is False
    assert reason == "span_mismatch"


def test_length_buys_no_edits():
    """The same single edit in a source made twenty times longer is still refused.

    Under the old budget this was the whole bug: tolerance grew with the span.
    """
    long_doc = SOURCE + " " + ("The engagement was performed under ISA 700. " * 50)
    wrong = long_doc.replace("310 employees", "810 employees")
    balances, reason = _verdict(long_doc, "Headcount stood at 810 employees.", wrong)
    assert balances is False
    assert reason == "span_mismatch"


def test_a_replaced_sentence_in_a_very_long_quote_does_not_balance():
    doc = ("The trial enrolled 1,200 participants across eleven sites. " * 45
           + "The company reported no adverse events in the trial.")
    wrong = doc.replace("The company reported no adverse events in the trial.",
                        "The drug is safe and effective for all patients.")
    balances, _ = _verdict(doc, "The drug is safe and effective for all patients.", wrong)
    assert balances is False


@pytest.mark.parametrize("drift", [
    lambda s: s + "  ",                       # a trailing edge the recorder trimmed
    lambda s: "  " + s,
    lambda s: s.replace("12.4 million", "12.4  million"),   # doubled space
    lambda s: s.replace("euro,", "euro ,"),                 # punctuation spacing
    lambda s: s.replace("review.", "review"),               # dropped full stop
    lambda s: s.upper(),                                    # case (normalize folds it)
])
def test_typographic_drift_is_still_forgiven(drift):
    """The tolerance exists for these. Removing the budget must not cost them."""
    balances, reason = _verdict(SOURCE, "Group revenue reached 12.4 million euro.",
                                drift(SOURCE))
    assert balances is True, reason


def test_a_separator_inside_a_number_is_content_not_typography():
    """12.4 and 124 are different numbers, so dropping the point is not drift."""
    doc = "Revenue reached 12.4 million euro in the period."
    wrong = doc.replace("12.4", "124")
    balances, _ = _verdict(doc, "Revenue reached 124 million euro in the period.", wrong)
    assert balances is False


def test_an_honest_quote_still_closes():
    balances, _ = _verdict(SOURCE, "Group revenue reached 12.4 million euro.", SOURCE)
    assert balances is True
