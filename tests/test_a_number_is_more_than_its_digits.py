"""A number in text is its digits together with what changes their value.

Round 7 taught the boundary check to ask Unicode what a character is, and asked
only about punctuation (`P*`). Everything Unicode files under mathematics,
currency, superscripts and modifiers stayed outside the number, so a span cut
between a number and its sign, its currency, its power or its fraction slash
stated another number and the books balanced. The owner checked seven of them
by hand on 8659212; each is copied below from the source it was found in
(project rule 37), with `‸` where the span begins or ends.

The honest side is here too, and it is a price list: a list marker, a table
cell, a bracket, a quotation mark, a compound hyphen, the end of a range. A
reading of a number that is too wide fails on those.
"""
from __future__ import annotations

import pytest

from tallystick import close_books, load_run, tokens
from tallystick.propose.pipeline import _locate_tolerant

B = "\u2038"   # CARET: where the span boundary goes


def _books(doc: str, start: int, end: int, quoted: str) -> bool:
    """Do the books balance for one entry citing `doc[start:end]` as `quoted`?
    The trace is honest about everything but the boundary."""
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


def _from(template: str):
    """The span from `‸` to the end, word for word."""
    i = template.index(B)
    doc = template[:i] + template[i + 1:]
    return doc, i, len(doc)


def _up_to(template: str):
    """The span from the start to `‸`, word for word."""
    i = template.index(B)
    doc = template[:i] + template[i + 1:]
    return doc, 0, i


# --------------------------------------------------------------------------- #
# the forgeries: the owner's seven, and the class each stands for
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("template,cut", [
    # SmolAgents__074: DIVISION SLASH, the class of `1/2 a potato` (round 7, decision 2)
    ("So Ising spins correspond to\n⟨Sz⟩ = ±1∕" + B + "2. It follows.", _from),
    ("It is 1⁄" + B + "2 a potato each.", _from),                       # FRACTION SLASH
    # OpenManus__000: a size 10^15 times smaller
    ("- Star A: 1.296000" + B + "×10¹⁵ (arbitrary units)", _up_to),
    # SmolAgents__022
    ("  + For children with ≤" + B + "5% dehydration, replace deficit", _from),
    ("  + For children with >" + B + "5% dehydration, replace deficit", _from),
    # SmolAgents__006
    ("X-ray monitoring of SGR J1935+2154 from ~" + B + "3 days prior to ~3 weeks", _from),
    # Magentic_One__014
    ("interaction (≈" + B + "9400) has been performed by Jolma", _from),
    # Magentic_One__004: the letters are the currency's
    ("The committee reported a total cost of \nUS" + B + "$1.183 million with receipts", _from),
    # Magentic_One__078: a superscript minus
    ("- p ≈ 3.07×10⁻" + B + "31 kg·m/s", _from),
    # Magentic_One__088
    ("side length 2 is 4√" + B + "3, and assuming that", _from),
    ("a power written flat: x^" + B + "2 + 1", _from),
    ("a power written flat: 10" + B + "^-34 J", _up_to),
    ("It was 5" + B + "° at noon.", _up_to),
    ("It cost ¥" + B + "100 at the time.", _from),
    ("It cost ₹" + B + "500 at the time.", _from),
    ("A rise of 5" + B + "‰ in the rate.", _up_to),
], ids=["DIVISION SLASH (SmolAgents__074)", "FRACTION SLASH", "a power (OpenManus__000)",
        "less than or equal (SmolAgents__022)", "greater than", "a tilde (SmolAgents__006)",
        "almost equal (Magentic_One__014)", "US$ (Magentic_One__004)",
        "a superscript minus (Magentic_One__078)", "a root (Magentic_One__088)",
        "a caret before", "a caret after", "a degree", "yen", "rupee", "per mille"])
def test_a_span_cut_between_a_number_and_what_changes_it_does_not_close_the_books(template, cut):
    doc, start, end = cut(template)
    assert not _books(doc, start, end, doc[start:end])


@pytest.mark.parametrize("template", [
    "It grew by ." + B + "5% of assets.",
    "It grew by ," + B + "500 people.",
], ids=["a decimal point", "a grouping comma"])
def test_a_span_begun_after_the_head_of_a_number_does_not_close_the_books(template):
    """Rounds 3 and 4 closed `.5% of assets` cited as `5% of assets` inside the
    quote. With the span declared one character on, the citation is word for
    word and the fast path answers: the head is outside the span.

    Round 8 had a third case here, `It began at :30 past.` cited from `30`,
    and round 9 took it out: a colon in front of a number labels it
    (`arXiv:1804.01620`, `"count":42`) and no reading of the characters tells
    ` :30` from the value of compact JSON, which the owner decided is honest
    (`tests/test_a_number_is_read_whole.py`). The price is named: a time
    written without its hour, cited from its minutes, closes the books."""
    doc, start, end = _from(template)
    assert not _books(doc, start, end, doc[start:end])


@pytest.mark.parametrize("template,cut", [
    # the example `tokens.py` opens with, where the inverted list began
    ("Operating margin - " + B + "5.2 % lower than last year.", _from),
    ("The limit is ≤ " + B + "5 per day.", _from),
    ("The value is 1.296 × " + B + "10 units.", _from),
    ("The value is 1.296" + B + " × 10 units.", _up_to),
    ("Operating margin fell 5.2" + B + " % last year.", _up_to),
    ("It cost 5" + B + " € each.", _up_to),
], ids=["a minus apart from its number", "less than or equal apart",
        "a product apart, the span after it", "a product apart, the span before it",
        "a percent sign after a space", "a euro sign after a space"])
def test_a_sign_a_space_away_from_its_number_is_still_its_sign(template, cut):
    """The owner's decisions of round 8: a sign or an operator standing apart in
    the middle of a line, a share or currency sign after a space, an operator
    with a space on each side between two numbers."""
    doc, start, end = cut(template)
    assert not _books(doc, start, end, doc[start:end])


@pytest.mark.parametrize("gap", [" \u200b ", " \u200b \u200b "], ids=["one", "two"])
def test_an_invisible_character_in_the_space_that_groups_a_number_is_read_away(gap):
    """`66 \\u200b 300` is `66 300` to the comparison. The boundary read a
    stretch that began at the invisible character and never saw the `66`; with
    two of them, a stretch two words wide still stopped short of it."""
    doc, start, end = _from("about 66" + gap + B + "300 people were counted.")
    assert not _books(doc, start, end, doc[start:end])


# --------------------------------------------------------------------------- #
# the honest side: what stands against a number and is not part of it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("template,cut", [
    ("The monsters are:\n- " + B + "8 Orcs\n- 6 Goblins", _from),   # Octotools__039
    ("  - 6 Goblins\n  - " + B + "4 Chaos Warriors", _from),
    ("The monsters are:\n> " + B + "8 Orcs and more.", _from),
    ("| Year | 2011 | " + B + "5 | people |", _from),
    ("| Year | 2011" + B + " | 5 | people |", _up_to),
    ("It was founded in 1946" + B + " - 5 July is the date.", _up_to),
    ("a 5" + B + "-year plan was agreed", _up_to),
    ("mother and father (" + B + "2 adults)", _from),                # Magentic_One__009
    ('{"content": "' + B + '536", "failed": false}', _from),         # Camel__002
    ("in Blue Devil #" + B + "6 (November 19", _from),
    ("- **" + B + "2 aromatic hydrogens", _from),                    # Magentic_One__011
    ("see `" + B + "5` here", _from),
    ("a 5" + B + ". And more was written.", _up_to),
    ("path/" + B + "600/issues", _from),
    ("We sold 5" + B + " $10 tickets at the door.", _up_to),
], ids=["a list marker (Octotools__039)", "an indented list marker", "a blockquote",
        "a table cell after a rule", "a table cell before a rule", "the end of a range",
        "a compound hyphen", "a bracket (Magentic_One__009)", "a quotation mark (Camel__002)",
        "a number sign", "markdown bold (Magentic_One__011)", "a code span",
        "a full stop", "a path", "a currency after a space that is the next number's"])
def test_what_frames_a_number_is_not_part_of_it(template, cut):
    doc, start, end = cut(template)
    assert _books(doc, start, end, doc[start:end])


# --------------------------------------------------------------------------- #
# one reading for both sides of the library
# --------------------------------------------------------------------------- #

def test_the_proposer_does_not_place_a_citation_inside_a_number_either():
    """`tokens.cuts_number` is the proposer's reading too: an exact hit of `2`
    inside `±1∕2` is not the text the model was shown."""
    doc = "So Ising spins correspond to ±1∕2 and nothing else."
    assert _locate_tolerant(doc, "2", []) is None
    assert tokens.cuts_number(doc, doc.index("∕") + 1, doc.index("∕") + 2)
