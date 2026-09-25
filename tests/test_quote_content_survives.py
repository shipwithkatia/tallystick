"""The quote gate forgives the setting and nothing else.

Every failing case here balanced at exit 0 on some shipped version of this gate.

    An edit budget of 2% of the span's length, no ceiling: a 307-character source
    saying "12.4 million euro" cited as "92.4 million euro" balanced, and a
    2,707-character one had its closing sentence replaced and balanced too.

    Then a content signature of letters, digits and separators-between-digits,
    which dropped every other mark: "+5%" cited as "-5%", "EUR 12" as "USD 12",
    ">50" as "50", "mg/kg" as "mg kg", "notable" as "not able" - all balanced.

    Then a sequence of tokens, in which whole Unicode categories were setting:
    "margin - 5.2 %" cited as "margin 5.2 %" balanced, because a dash standing
    one space from its number was neither a word nor part of one.

The rule now names what may differ instead of what may not: four characters, each
under a condition (`tallystick/tokens.py:SETTING`), and every other character -
including the ones nobody here has thought of - is content.
`tallystick/verify.py:says_the_same` states it; the characters are swept in
`tests/test_only_the_listed_setting_may_differ.py`.

The two lengths above are recomputed from this file's own fixtures by
`test_the_lengths_this_file_publishes_are_its_own_fixtures`, because a length
quoted from a review and never recounted is how 308 and 2,645 got into three
documents (project rule 5).
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
    """The reviewer's case: 12.4 million cited as 92.4 million, 307 characters."""
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
    """12.4 and 124 are different numbers, so dropping the point is not drift.

    The document is deliberately longer than 50 characters. At 48 it was shorter
    than the old budget's smallest unit - `int(48 * 0.02) == 0` - so this test
    passed on the unfixed code and guarded nothing (project rule 7).
    """
    doc = ("The audited filing states that revenue reached 12.4 million euro in "
           "the period, and the auditor confirmed it.")
    assert len(doc) > 50
    wrong = doc.replace("12.4", "124")
    balances, _ = _verdict(doc, "Revenue reached 124 million euro in the period.", wrong)
    assert balances is False


@pytest.mark.parametrize("source,quoted", [
    ("Revenue growth of +5% was reported by the board this quarter.",
     "Revenue growth of -5% was reported by the board this quarter."),
    ("The fine imposed on the company was \u20ac12 million in total.",
     "The fine imposed on the company was $12 million in total."),
    ("Values were >50 in all of the cases the laboratory examined.",
     "Values were 50 in all of the cases the laboratory examined."),
    ("The dose is 5 mg/kg daily for the duration of the study.",
     "The dose is 5 mg kg daily for the duration of the study."),
    ("The effect was notable in the trial, the investigators wrote.",
     "The effect was not able in the trial, the investigators wrote."),
    ("The warning is nowhere in the file the regulator published.",
     "The warning is now here in the file the regulator published."),
    ("The trial found that the drug is safe.",
     "The trial found that the drug is safe?"),
])
def test_a_sign_a_unit_or_a_word_boundary_is_content(source, quoted):
    """The second gate, the content signature, dropped every mark that was not
    between two digits. Each of these balanced at exit 0 under it; an external
    review found them. They are what "only the setting may differ" has to mean."""
    balances, reason = _verdict(source, quoted, quoted)
    assert balances is False, f"{quoted!r} balanced ({reason})"


def test_the_lengths_this_file_publishes_are_its_own_fixtures():
    """Rule 5: a number in the prose is recomputed from the thing it describes."""
    assert len(SOURCE) == 307
    long_doc = ("The trial enrolled 1,200 participants across eleven sites. " * 45
                + "The company reported no adverse events in the trial.")
    assert len(long_doc) == 2707


def test_an_honest_quote_still_closes():
    balances, _ = _verdict(SOURCE, "Group revenue reached 12.4 million euro.", SOURCE)
    assert balances is True
