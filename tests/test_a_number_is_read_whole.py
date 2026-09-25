"""A number at the boundary of a span is read whole, once, and then asked one question.

Round 8 asked about the sign in front of a number only when the span began on a
digit, so a span that began on the currency or the decimal point was never
asked what stood in front of those: `a loss of -$5.2 million` cited as `$5.2
million` closed the books - the dropped minus the inverted list began with,
with a currency between the sign and the digits. The review of round 8 found
that and its neighbours (`~$3`, `≤$5`, `-.5%`, `$100-$300`, `€ 120`,
`3947 / 7`), and found the same reading refusing the value of compact JSON,
`x=5`, `©2015` - the output of tools an auditor of agent traces reads every
day. Each round had closed the cases it was shown and the next review brought
the neighbour, because the rule never read the number as one thing.

Round 9 reads it as one thing - sign, currency, digits, separators, the
fraction, the power, the share - and asks whether the boundary falls inside it.
The forgeries and the honest cases below are the review's, copied (project
rule 37), with `‸` where the span begins or ends. The last section is the
proposer, which placed spans the gate refuses (the review's section 2.3).
"""
from __future__ import annotations

import pytest

from tallystick import close_books, load_run

B = "‸"   # CARET: where the span boundary goes


def _books(doc: str, start: int, end: int, quoted: str) -> bool:
    trace = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer",
                       "content": "The committee published."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"],
                   "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 24}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#{start}-{end}",
                     "quoted_span": quoted}],
    }
    return close_books(load_run(trace)).books_balance


def _span(template: str):
    """One `‸`: the span runs from it to the end. Two: the span is between them.
    A template beginning with `^` runs from the start to the `‸`."""
    if template.startswith("^"):
        template = template[1:]
        i = template.index(B)
        doc = template[:i] + template[i + 1:]
        return doc, 0, i
    i = template.index(B)
    doc = template[:i] + template[i + 1:]
    if B in doc:
        j = doc.index(B)
        doc = doc[:j] + doc[j + 1:]
        return doc, i, j
    return doc, i, len(doc)


# --------------------------------------------------------------------------- #
# the forgeries
# --------------------------------------------------------------------------- #

FORGED = {
    # the owner's five of round 9
    "a minus in front of a currency": "They reported a loss of -" + B + "$5.2 million this year.",
    "a tilde in front of a currency": "It was about ~" + B + "$3 total.",
    "less than or equal in front of a currency": "Sold at ≤" + B + "$5 each.",
    "a minus in front of a decimal point": "The rate fell -" + B + ".5% today.",
    "a range of two sums of money (14283)": "It costs $100-" + B + "$300 range.",
    # the review's neighbours of the same class
    "a minus sign U+2212 in front of a currency": "a loss of −" + B + "$5.2 million",
    "greater than in front of a currency": "Orders >" + B + "$100 ship free.",
    "a minus a space in front of a decimal point": "Margin moved - " + B + ".5% overnight.",
    "the first of a range of two sums": "^It costs $100" + B + "-$300 range.",
    "a euro sign a space in front": "The fee is € " + B + "120 per person.",
    "a dollar sign a space in front": "The fee is $ " + B + "5 per person.",
    "US$ a space in front": "Revenue was US$ " + B + "1.183 million.",
    "small letters glued to a currency": "It was worth us" + B + "$5 million.",
    "a slash with spaces (Magentic_One__056)": "x = 3947 / " + B + "7 ≈ 563.857",
    "an asterisk with spaces, the span before it": "^Star A: 1.296" + B + " * 10^15 units.",
    "an asterisk with spaces, the span after it": "The area is 5 * " + B + "3 metres.",
    "a degree sign a space after (SmolAgents__035)": "^Density at 25" + B + " °C (g/cm3)",
    "a vulgar fraction a space after": "^It weighs 5" + B + " ½ pounds.",
    "a bracket pair, the span ending inside it": "Sales fell " + B + "(5%" + B + ") in the year.",
}


@pytest.mark.parametrize("template", list(FORGED.values()), ids=list(FORGED))
def test_a_span_cut_out_of_a_number_read_whole_does_not_close_the_books(template):
    doc, start, end = _span(template)
    assert not _books(doc, start, end, doc[start:end])


# --------------------------------------------------------------------------- #
# the honest side
# --------------------------------------------------------------------------- #

HONEST = {
    # the owner's list of round 9
    "the value of compact JSON": 'result {"count":' + B + "42" + B + "} done",
    "the value of an assignment": "set x=" + B + "5 now",
    "a year after a copyright mark": "text ©" + B + "2015 here",
    # the review's section 2.2
    "a field of CSV after a quoted field": 'city,population\n"Paris",' + B + "2161000" + B + '\n"Oslo",709000',
    "the value of a query parameter": "index.php?title=Cuneiform&section=" + B + "1" + B + ")",
    "an equation (SmolAgents__072)": "Charge e = " + B + "1.60 × 10–19 C, mp = 1.67 × 10–27 kg",
    "an arrow with spaces": "Headcount grew 5 → " + B + "7 this year.",
    "an arrow glued": "Version 1→" + B + "2 migration done.",
    "a check mark": "Summary: ✅" + B + "5 tests passed, ❌1 failed.",
    "a bullet between a title and a count": "James Bond 007•" + B + "398K views",
    "a currency a space before the next number": "^We sold 5" + B + " $ 10 tickets.",
    "a bracket with a space inside it": "Group size was (" + B + "5 people" + B + ") in total.",
    # where a line begins (the review's Y02, Y03)
    "a list marker at the start of the text": "- " + B + "8 Orcs and more.",
    "a list marker after a carriage return": "The monsters are:\r- " + B + "8 Orcs and more.",
    "a list marker after a line separator": "The monsters are:\u2028- " + B + "8 Orcs and more.",
}


@pytest.mark.parametrize("template", list(HONEST.values()), ids=list(HONEST))
def test_what_labels_relates_or_frames_a_number_is_not_part_of_it(template):
    doc, start, end = _span(template)
    assert _books(doc, start, end, doc[start:end])


def test_every_value_in_a_compact_api_answer_closes_the_books():
    """The review timed a JSON answer of an API and found every value refused
    at f336415 (its section 8, `closes=False`)."""
    import json
    doc = json.dumps({"series": "CPU", "points": [{"t": 1690000000, "v": 12.5},
                                                   {"t": 1690000060, "v": -3.25}]},
                     separators=(",", ":"))
    for value in ("1690000000", "12.5", "1690000060", "-3.25"):
        start = doc.index(value)
        assert _books(doc, start, start + len(value), value), value


# --------------------------------------------------------------------------- #
# the owner's decisions of round 9, held so that changing one is a visible edit
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("template", [
    "see Report - " + B + "2023 file",
    "3," + B + "4" + B + ",7,8,11,19",
], ids=["Report - 2023: decision 4g of round 8, kept",
        "a member of a list of numbers: a comma between two digits keeps them one"])
def test_what_the_owner_decided_to_leave_refused_stays_refused(template):
    doc, start, end = _span(template)
    assert not _books(doc, start, end, doc[start:end])


# --------------------------------------------------------------------------- #
# the proposer reads what the gate reads
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("doc,cited", [
    ("Operating margin - 5.2 % lower.", "5.2 % lower."),
    ("They reported a loss of -$5.2 million this year.", "$5.2 million this year."),
    ("x = 3947 / 7 ≈ 563.857", "7 ≈ 563.857"),
], ids=["a sign apart (the review's 2.3)", "a sign before a currency", "a slash with spaces"])
def test_the_proposer_places_nothing_the_gate_refuses(doc, cited):
    """The exact hit is refused as a cut, and the fallback on words then placed
    the same words anyway, asking nothing about numbers."""
    from tallystick.propose.pipeline import _locate_tolerant
    from tallystick.tokens import cuts_a_number
    placed = _locate_tolerant(doc, cited, [])
    assert placed is None or not cuts_a_number(doc, *placed), placed


def test_the_proposer_reads_the_view_the_gate_reads():
    """A minus sign after a number is a dash once `normalize` has folded it,
    and a dash after a number joins a compound; the proposer read the raw
    minus as a symbol and placed nothing."""
    from tallystick.propose.pipeline import _locate_tolerant
    doc = "it costs 500− per unit today."
    assert _locate_tolerant(doc, "it costs 500", []) == (0, 12)


@pytest.mark.parametrize("doc,cited", [
    ("They reported a loss of -$5.2 million this year.", "-$5.2 million"),
    ("They reported a loss of -$5.2 million this year.", "-$5.2"),
    ("The fee was -€5 today.", "-€5"),
    ("The rate fell -.5% today.", "-.5%"),
    ("The delta was (-$5) today.", "(-$5)"),
], ids=["the README's -$5.2 million", "-$5.2", "-€5", "-.5%", "(-$5)"])
def test_the_proposer_places_an_honest_quote_that_opens_on_a_minus(doc, cited):
    """The review of round 9 (section 7.2): an honest quote that opens on an
    ASCII minus before a currency, a point or a bracket was placed nowhere.
    The exact hit asked `cuts_a_number` of the hit shrunk to its core, and the
    core drops the minus - a dash is a separator to the word match - so the
    question was asked of `$5.2 million`, which does cut `-$5.2`. The gate asks
    the span as placed, and accepts it."""
    from tallystick.propose.pipeline import _locate_tolerant
    from tallystick.tokens import cuts_a_number
    start = doc.index(cited)
    assert not cuts_a_number(doc, start, start + len(cited))   # the gate's answer
    assert _locate_tolerant(doc, cited, []) == (start, start + len(cited))


_UNSIGNED = "The quarter closed with a loss of $5 million on the books."


@pytest.mark.parametrize("doc,cited", [
    (_UNSIGNED, "-$5 million"),
    (_UNSIGNED, "- $5 million"),
    (_UNSIGNED, "–$5 million"),
    (_UNSIGNED, "a loss of -$5 million"),
    ("The fee was €5 today.", "-€5"),
    ("The margin was 5.2 % lower.", "- 5.2 %"),
    ("Margin fell (5%) overall.", "-(5%)"),
], ids=["the review's -$5 million", "a minus a space away", "an en dash",
        "a minus inside a longer quote", "a minus before a euro", "- 5.2 %",
        "a minus before a bracket"])
def test_the_proposer_places_no_quote_whose_sign_the_source_does_not_state(doc, cited):
    """The last review: the source says `$5 million`, the model quotes
    `-$5 million`, and the match on words - to which a dash is a separator -
    placed the quote on `$5 million` and wrote that slice into the trace as the
    model's quote. The gate then closed the books, because the slice is really
    at its address. The module promises a match that forgives punctuation and
    case "and never a changed character"; a sign is a changed character. The
    right answer is to place nothing."""
    from tallystick.propose.pipeline import _locate_tolerant
    assert _locate_tolerant(doc, cited, []) is None


@pytest.mark.parametrize("doc,cited,placed", [
    (_UNSIGNED, "$5 million.", "$5 million"),
    (_UNSIGNED, "\"$5 million\"", "$5 million"),
    ("Sales fell -5% in May.", "\"-5%.\"", "-5%"),
    ("In all 66 400 people came.", "\"66 400 people.\"", "66 400 people"),
    ("COVID-19 cases rose.", "COVID 19 cases", "COVID-19 cases"),
    ("COVID 19 cases rose.", "COVID-19 cases.", "COVID 19 cases"),
    ("The margin \u2013 5.2 % lower than planned.", "margin - 5.2 % lower.",
     "margin \u2013 5.2 % lower"),
], ids=["a full stop", "quotation marks", "a signed share in quotation marks",
        "a grouped number", "a hyphen left out", "a hyphen added",
        "another kind of dash as the sign"])
def test_the_proposer_still_places_the_drift_it_forgives(doc, cited, placed):
    """The other side of the test above. A full stop or quotation marks the
    model added, a hyphen between a word and a number, and an en dash quoted as
    a hyphen change no sign and no digit: the tolerant locate exists for these
    and must still place them."""
    from tallystick.propose.pipeline import _locate_tolerant
    start = doc.index(placed)
    assert _locate_tolerant(doc, cited, []) == (start, start + len(placed))


@pytest.mark.parametrize("doc,cited", [
    ("Margin fell -(5%) this year.", "\"-(5%) this year\""),
    ("Losses:\n- $5.2 million lost\nGains: none.", "- $5.2 million lost."),
], ids=["a sign before a bracket", "a dash and a space opening a line"])
def test_the_proposer_writes_no_slice_without_the_sign_the_model_quoted(doc, cited):
    """An honest quote with a mark added, whose sign the word match leaves
    outside the span it finds. Placing that span writes `(5%) this year` into
    the trace for a model that quoted `-(5%) this year`. The slice is read
    alone, as it will be written, so the sign outside it is not counted as
    its own; placing nothing is the error that is logged."""
    from tallystick.propose.pipeline import _locate_tolerant
    placed = _locate_tolerant(doc, cited, [])
    assert placed is None or doc[placed[0]:placed[1]].lstrip()[:1] in "-−"


def test_the_raw_reading_asks_where_a_line_begins_too():
    """`tokens.cuts_number` read on raw text asks the raw text whether a sign
    opens its line. Nothing on the verdict path calls it without an answer
    since round 9 - the gate and the proposer both go through
    `cuts_a_number` - so the review's mutant Y11 reaches no tool and is held
    here instead."""
    from tallystick import tokens
    doc = "The monsters are:\n- 8 Orcs and more."
    start = doc.index("8")
    assert not tokens.cuts_number(doc, start, len(doc))
