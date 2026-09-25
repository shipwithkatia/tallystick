"""The quote gate forgives a list of four characters and refuses everything else.

Four rules shipped before this one, and every one of them was a list of what
counts as *content*: an edit budget, then a signature of letters and digits, then
a sequence of tokens in which whole Unicode categories were setting. Each time an
outside reader found a character the list had not thought of - a digit, then a
sign at the edge of a span, then a sign standing one space away from its number -
and each time the answer was to name one more thing as content.

So the list is inverted, and this file is where that is checked. Two kinds of test
live here:

  * the cases, planted **in the middle of the span and at each of its two
    edges**, because a rule written as a walk over differences treated an end
    differently from a middle and a test that only probed the middle stayed
    green. The cases themselves are copied, not retold (project rule 37).

  * **the sweep** - `test_no_character_outside_the_list_may_be_dropped` and its
    two neighbours - which does not enumerate cases at all. It walks every
    punctuation and symbol character in the first three Unicode planes' worth of
    codepoints this project could meet, and asserts that dropping it is refused
    unless it is one of the three characters named here. A character nobody has
    thought of fails the sweep the moment it is let through, which is what
    "safe by construction" has to mean if it is not to be another list.

The material is tiny and lives in this file, so a fresh clone runs it (project
rule 13); `bench/quote_gate_corpus.py` runs the same kinds over the real corpus.
"""
from __future__ import annotations

import unicodedata

import pytest

from tallystick import close_books, load_run, verify
from tallystick.normalize import normalize

QUOTE = "The committee reviewed the report and agreed to publish the findings"

#: The whole of the tolerance, written here rather than imported, so that
#: widening `tokens.SETTING` fails these tests instead of passing them.
MAY_BE_DROPPED_BESIDE_A_SPACE = {",", '"', "'"}
MAY_BE_DROPPED_AT_AN_EDGE = MAY_BE_DROPPED_BESIDE_A_SPACE | {".", "!", "?", "…"}
MAY_BE_DROPPED_BETWEEN_TWO_LETTERS: set = set()

#: (name, what the source says, what the recorder wrote instead). Every one of
#: these says something else. The first six are the cases the external review of
#: v0.8.1 demonstrated (knowledge base §33); the last three are what the review
#: of v0.8.3 demonstrated, and they are the reason the list was inverted.
SUBSTITUTIONS = [
    ("a digit", "12.4 million euro", "92.4 million euro"),
    ("the sign before a number", "+5% this quarter", "−5% this quarter"),
    ("the currency mark", "€12 per unit", "$12 per unit"),
    ("a comparison sign", ">50 patients", "50 patients"),
    ("a space inside a word", "a notable effect", "a not able effect"),
    ("the mark closing a statement", "the site is safe.", "the site is safe?"),
    ("the separator inside a unit", "8 mg/kg daily", "8 mg kg daily"),
    ("the separator inside a number", "66,300 records", "66300 records"),
    ("a negation", "was not confirmed", "was confirmed"),
    ("a sign standing apart from its number", "margin - 5.2 % lower", "margin 5.2 % lower"),
    ("a bracket that carries the sign", "net income (1.2) bn", "net income 1.2 bn"),
    ("a mark nobody put on the list", "salinity of 5‰ here", "salinity of 5 here"),
]

#: (name, what the source says, what the recorder wrote instead) where the two
#: say the same thing. `propose/pipeline.py` records that models do these and
#: that they already cost this project real credits at the v0.5.3 run.
DRIFT = [
    ("a comma dropped", "first, second and third", "first second and third"),
    ("a comma added", "first second and third", "first, second and third"),
    ("a space next to a mark", "the figure ( 12 ) stands", "the figure (12) stands"),
    ("a space next to a percent sign", "a 5 % rise", "a 5% rise"),
    ("another kind of quotation mark", 'he said "yes" then', "he said “yes” then"),
    ("another kind of dash", "the 2020-2021 season", "the 2020–2021 season"),
    ("another case", "the Annual Report", "the annual report"),
    ("a doubled space", "one two three", "one  two three"),
]


def plant(fragment: str, site: str) -> str:
    """`fragment` put into a span in the middle, or at one of its two ends."""
    if site == "left edge":
        return f"{fragment} {QUOTE}"
    if site == "right edge":
        return f"{QUOTE} {fragment}"
    head, tail = QUOTE[:34], QUOTE[34:]
    return f"{head}{fragment} {tail}"


SITES = ("left edge", "middle", "right edge")


def said_the_same(source: str, written: str) -> bool:
    return verify.says_the_same(normalize(source), normalize(written))


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name,source,written", SUBSTITUTIONS,
                         ids=[s[0] for s in SUBSTITUTIONS])
def test_a_substitution_is_refused_wherever_it_sits(name, source, written, site):
    assert not said_the_same(plant(source, site), plant(written, site)), \
        f"{name} changed at the {site} and the books still balanced"


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name,source,written", DRIFT, ids=[d[0] for d in DRIFT])
def test_the_setting_may_differ_wherever_it_sits(name, source, written, site):
    assert said_the_same(plant(source, site), plant(written, site)), \
        f"{name} at the {site} was called a change of content"


# --------------------------------------------------------------------------- #
# the sweep: the inverted default, checked over the characters rather than over
# the cases somebody thought of
# --------------------------------------------------------------------------- #

def candidate_characters():
    """Every punctuation or symbol character up to U+2FFF, plus a letter and a
    digit from scripts that are not Latin.

    The range covers ASCII, Latin-1, general punctuation, currency, letterlike
    symbols, arrows, mathematical operators and the dingbat block - which is to
    say every character this project has met in a real artifact and several
    thousand it has not. That is the point: the ones it has not met are the ones
    the four earlier lists got wrong.
    """
    out = []
    for cp in range(0x21, 0x3000):
        ch = chr(cp)
        if unicodedata.category(ch)[0] in ("P", "S"):
            out.append(ch)
    out += ["٥", "٫", "一", "ت", "ñ", "ß"]
    return out


CHARACTERS = candidate_characters()


def _folded_away(source: str, written: str) -> bool:
    """`normalize` already made the two strings one string. Those differences -
    case, NFC composition, invisible characters, quotation and dash styles, runs
    of whitespace - are the honest list, stated in `normalize`, and the gate
    never sees them."""
    return normalize(source) == normalize(written)


def _is_on_the_list(ch: str, allowed: set) -> bool:
    """A character counts as listed when what `normalize` leaves of it is on the
    list: `\u00ab` and `\u201c` are the quotation mark this project folds them to, and
    forgiving them is forgiving the quotation mark once, not thirteen times."""
    folded = normalize(ch)
    return bool(folded) and all(c in allowed for c in folded)


def test_no_character_outside_the_list_may_be_dropped_beside_a_space():
    """`alpha X beta` cited as `alpha beta`: only the comma and the two
    quotation marks may go. This is the test the previous three rules would
    have failed - the third one on every dash and every bracket."""
    forgiven = []
    for ch in CHARACTERS:
        source, written = f"alpha {ch} beta gamma", "alpha beta gamma"
        if _folded_away(source, written):
            continue
        if said_the_same(source, written) and not _is_on_the_list(
                ch, MAY_BE_DROPPED_BESIDE_A_SPACE):
            forgiven.append(f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
    assert not forgiven, (
        f"{len(forgiven)} characters may be dropped without the gate noticing, "
        f"and the rule names only {sorted(MAY_BE_DROPPED_BESIDE_A_SPACE)}: {forgiven[:20]}")


def test_no_character_may_be_dropped_from_between_two_letters():
    """`alphaXbeta` cited as `alphabeta`: nothing may go, not even a comma —
    between two word characters it is holding two things apart (`66,300`)."""
    forgiven = []
    for ch in CHARACTERS:
        source, written = f"the alpha{ch}beta word", "the alphabeta word"
        if _folded_away(source, written):
            continue
        if said_the_same(source, written):
            forgiven.append(f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
    assert not forgiven, f"dropped from inside a word and forgiven: {forgiven[:20]}"


def test_only_a_sentence_mark_may_be_dropped_from_the_end_of_a_quote():
    """`alpha beta X` cited as `alpha beta`: the marks that end a sentence may
    go, because that is how a recorder cuts a sentence out of a paragraph, and
    the setting that surrounds them may go with them. Nothing else may."""
    forgiven = []
    for ch in CHARACTERS:
        source, written = f"alpha beta gamma{ch}", "alpha beta gamma"
        if _folded_away(source, written):
            continue
        if said_the_same(source, written) and not _is_on_the_list(
                ch, MAY_BE_DROPPED_AT_AN_EDGE):
            forgiven.append(f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
    assert not forgiven, f"dropped from the end and forgiven: {forgiven[:20]}"


def test_the_sweep_would_notice_a_wider_list():
    """The sweep is only worth having if it fires. A rule that also forgave the
    semicolon has to fail it, or the three tests above are decoration."""
    def forgiving(actual: str, quoted: str) -> bool:
        strip = str.maketrans("", "", ";")
        return verify.says_the_same(actual.translate(strip), quoted.translate(strip))

    source, written = "alpha ; beta gamma", "alpha beta gamma"
    assert not said_the_same(source, written)
    assert forgiving(normalize(source), normalize(written))


# --------------------------------------------------------------------------- #
# the one clause that is about position rather than about a character
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("quoted,verdict", [
    ("the site is safe", True),       # the closing mark dropped
    ("the site is safe.", True),      # kept
    ("the site is safe..", True),     # typed twice: still a full stop
    ("the site is safe…", True),      # the ellipsis character is "..."
    ("the site is safe?", False),     # a different sentence
    ("the site is safe!", False),
    ("the site is safe.?", False),    # kept and a question mark added
])
def test_a_closing_mark_may_go_or_stay_but_not_change(quoted, verdict):
    """The one clause beyond the list, stated in `says_the_same`."""
    assert said_the_same("the site is safe.", quoted) is verdict


def test_the_clause_holds_at_the_start_of_the_quote_too():
    """The mark run that opens a quote, not only the one that closes it. A
    recorder cutting a sentence out of a paragraph writes the ellipsis or drops
    it; what they may not do is open with a different mark.

    Written after a mutation that stopped reading the opening marks altogether
    and left the whole suite green (project rule 7)."""
    assert said_the_same("…the drug failed in phase two", "the drug failed in phase two")
    assert said_the_same("...the drug failed in phase two", "…the drug failed in phase two")
    assert not said_the_same("...the drug failed in phase two", "?the drug failed in phase two")
    assert not said_the_same("!the drug failed in phase two", ".the drug failed in phase two")


def test_a_mark_inside_the_quote_is_content():
    """Only the run that opens or closes the quote is setting. A full stop
    between two words is what makes a statement a statement, and `U.S.` is not
    `US`."""
    assert not said_the_same("it is safe. do not use it", "it is safe do not use it")
    assert not said_the_same("the U.S. delegation", "the US delegation")
    assert not said_the_same("Dr. Smith wrote", "Dr Smith wrote")


def test_a_space_is_setting_unless_it_is_holding_a_sign_off_a_number():
    """The one condition whitespace carries, and the case that decides it both
    ways. Neither is in this repository's corpus, so both are here (rule 13).

    `1946 - 5 July` is a range and `1946 -5 July` is a signed number, so that
    space is content. `March 11, 2011` cited as `March 11,2011` is a lost space
    beside a comma that is still there, holding the same two words apart, so it
    is setting - v0.8.3 refused 408 of the 411 pairs of that shape in the real
    corpus, and this is the false alarm that came with reading it as a number.
    """
    assert not said_the_same("the range 1946 - 5 July stands", "the range 1946 -5 July stands")
    assert not said_the_same("a change of - 5 points", "a change of 5 points")
    assert said_the_same("on March 11, 2011 it hit", "on March 11,2011 it hit")
    assert said_the_same("the right side: 10^3 = 1000 exactly",
                         "the right side: 10^3= 1000 exactly")


def test_a_full_stop_may_follow_the_mark_that_closes_the_quote():
    """A recorder quoting a question inside their own sentence writes
    `... is it safe?".` The question mark still closes the quoted sentence and
    the full stop is the recorder's own; a question mark added after a full stop
    is not, because it asks something the source does not."""
    assert said_the_same('he asked "is it safe?"', 'he asked "is it safe?".')
    assert said_the_same("there are 10!.", "there are 10!")
    assert not said_the_same("the site is safe.", "the site is safe.?")
    assert not said_the_same("the site is safe.", "the site is safe?")


def test_a_quote_with_no_content_vouches_for_nothing():
    """A span of dashes and a row of full stops are not the same thing said
    twice; they are two spans that say nothing. v0.8.3 called them equal,
    because both read as an empty sequence."""
    assert not said_the_same("---", "...")
    assert not said_the_same("( )", "[ ]")
    assert not said_the_same("— —", ".")
    assert said_the_same("...", "...")


# --------------------------------------------------------------------------- #
# the relation to the model side, stated as narrowly as it is true
# --------------------------------------------------------------------------- #

def test_the_gate_refuses_every_content_change_the_locate_refuses():
    """`propose/` refuses to place a span that cuts a number, and has since
    v0.7.0. The verifier did not know what a number was, so the barrier was
    weaker than the thing it guards. Whatever reads as different words to the
    proposer, and is a change of content, must not verify.

    The converse is **not** true and is not claimed: the gate forgives setting
    that the locate refuses to place a span through - see the test below. The
    two answer different questions, and only this direction is the promise.
    """
    from tallystick.propose.pipeline import _words

    weaker = []
    for _, before, after in SUBSTITUTIONS:
        for site in SITES:
            source, written = plant(before, site), plant(after, site)
            reads_the_same = ([w for _, _, w in _words(source)]
                              == [w for _, _, w in _words(written)])
            if not reads_the_same and said_the_same(source, written):
                weaker.append((source[:40], written[:40]))
    assert not weaker, (
        "the verifier accepted what the proposer reads as different words: "
        f"{weaker}")


def test_the_gate_is_more_forgiving_than_the_locate_on_setting_and_that_is_by_design():
    """Pinned by a test rather than left to prose, because three documents once
    said the gate could never be more forgiving than the locate, and it always
    could: the verifier audits hand-written traces, where nobody ran a locate.

    `a 5 % rise` is the same claim as `a 5% rise`; the locate will not place a
    span for it, and the gate accepts it."""
    from tallystick.propose.pipeline import _locate_tolerant

    source = "The report noted a 5% rise in revenue over the year."
    assert _locate_tolerant(source, "a 5 % rise", []) is None
    assert said_the_same("a 5 % rise", "a 5% rise")


# --------------------------------------------------------------------------- #
# the same thing through the real path
# --------------------------------------------------------------------------- #

def _trace(doc: str, quote: str) -> dict:
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer",
                       "content": "The committee published."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"],
                   "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 24}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#0-{len(doc)}", "quoted_span": quote}],
    }


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("before,after", [
    (">50 patients", "50 patients"),
    ("margin - 5.2 % lower", "margin 5.2 % lower"),
])
def test_the_books_do_not_balance_on_a_substitution(before, after, site):
    """The same thing once more through the real path, so that a rule that is
    right on its own and never reached would still be caught."""
    run = load_run(_trace(plant(before, site), plant(after, site)))
    assert not close_books(run).books_balance


def test_an_honest_quote_still_closes_through_the_real_path():
    doc = plant("the figure ( 12 ) stands", "middle")
    run = load_run(_trace(doc, doc.replace("( 12 )", "(12)")))
    assert close_books(run).books_balance


# --------------------------------------------------------------------------- #
# what the external review of v0.8.4 demonstrated
# --------------------------------------------------------------------------- #

def test_a_space_that_is_the_only_thing_between_two_numbers_is_content():
    """A space the list forgives, holding two numbers apart.

    The condition whitespace carried was about a *sign* arriving (`1946 - 5`
    against `1946 -5`). It says nothing about a space that is itself the
    boundary between two numbers, and the review found three shapes of that:
    a mark with a digit behind it and a digit in front, where closing the gap
    leaves one number by the reading `tokens.cuts_number` has used since
    v0.7.0 - a decimal point, a grouping comma of exactly three digits, a
    colon between digits.

    The other half of this test is the half that decides the shape of the fix.
    `March 11, 2011` cited as `March 11,2011` stays accepted, because
    `11,2011` is no number anybody writes; v0.8.3 refused 408 of the 411 real
    pairs of that shape, and buying this fix with those refusals back would be
    the trade the instrument exists to price.
    """
    assert not said_the_same("since 1998, 183 people were expelled",
                             "since 1998,183 people were expelled")
    assert not said_the_same("see Note 3. 5 percent of assets were impaired",
                             "see Note 3.5 percent of assets were impaired")
    assert not said_the_same("the total divided by 1000: 16 remained",
                             "the total divided by 1000:16 remained")
    assert not said_the_same("rooms 66 300 were counted", "rooms 66300 were counted")
    # The comma dropped with the space left behind was asserted here as the
    # same merge by another road, the space reading as the group mark of one
    # number. Round 7 turned that: a space groups only behind one to three
    # digits (the owner's decision on the `2011 300` convention), so
    # `1998 183` is a year and a count, the source states the same two numbers,
    # and refusing it was the false alarm the review of rounds 5-6 found. The
    # merge that is still a merge is the one above: `1998,183`.
    assert said_the_same("since 1998, 183 people were expelled",
                         "since 1998 183 people were expelled")

    assert said_the_same("on March 11, 2011 it hit", "on March 11,2011 it hit")
    assert said_the_same("and 0 gives 2, 5/3, and more", "and 0 gives 2,5/3, and more")
    assert said_the_same("Union of India (1996) 1 SCC 3",
                         "Union of India (1996)1 SCC 3")


def test_a_closing_mark_may_not_be_added_where_the_span_carries_none():
    """The clause says a mark may be added or dropped but never exchanged. It
    was read only from sources that already carried a mark, and every test of
    it above starts from `the site is safe.` - so the exchange it refuses cost
    one character of span to get: quote `the site is safe` and write
    `the site is safe?`.

    A full stop is still the recorder's own and may still be added, and a mark
    the source carries may still be dropped - that is a recorder cutting a
    sentence out of a paragraph, and 21 pairs of the real corpus are exactly
    that."""
    assert not said_the_same("the site is safe", "the site is safe?")
    assert not said_the_same("the site is safe", "the site is safe!")
    assert not said_the_same("the site is safe,", "the site is safe,?")
    assert not said_the_same('the site is safe"', 'the site is safe"?')
    assert not said_the_same("the drug failed", "?the drug failed")

    assert said_the_same("the site is safe", "the site is safe.")
    assert said_the_same("is the invoice paid?", "is the invoice paid")
    assert said_the_same("do not sign!", "do not sign")


def test_a_mark_opening_the_span_in_front_of_a_number_is_content():
    """`.5% of assets` cited as `5% of assets` is a tenfold difference, and the
    opening run is where the list forgives a sentence mark. The justification
    for that entry - this is how a recorder cuts a sentence out of a paragraph -
    is about a mark that ends a sentence; a mark standing against a digit is a
    decimal point, and the two are told apart by what follows it.

    This paragraph used to say that only the full stop belonged here, because
    `,500 people` cited as `500 people` is a span cut through `1,500` and a cut
    span is the locate's business (`cuts_number`) rather than this gate's. That
    argument was wrong, and the test that carries the case now is
    `test_a_mark_against_a_digit_at_the_head_of_the_span_is_content`:
    `verify.verify_entry` slices the artifact with the boundaries out of the
    recorded account and never asks `cuts_number` about them, so a trace is free
    to declare its span one character to the right. The comma is on
    `_NUMBER_MARKS` beside the full stop."""
    assert not said_the_same(".5% of assets was booked", "5% of assets was booked")

    assert said_the_same("…the drug failed", "the drug failed")
    assert said_the_same("... 5 percent of assets", "5 percent of assets")
    assert said_the_same("the rate was .5% of assets", "the rate was .5% of assets.")


# --------------------------------------------------------------------------- #
# what the external review of round 3 demonstrated
# --------------------------------------------------------------------------- #

def test_a_mark_against_a_digit_at_the_head_of_the_span_is_content():
    """The whole row of marks that may differ, asked where round 3 asked about
    one of them.

    Round 3 closed `.5% of assets` cited as `5% of assets` by stopping the
    opening run at a full stop with a digit against it, and the character
    beside it on the same list was left open: `,500 people affected` cited as
    `500 people affected` is a citation three times smaller than its source,
    and it closed the books at exit 0. The argument that took that assertion
    out of this file was that a span cut through `1,500` is the locate's
    business - `tokens.cuts_number` - and not this gate's. It is not:
    `verify.verify_entry` takes the boundaries out of the recorded account and
    slices the artifact with them (`verify.py`, `account.start`/`account.end`),
    and never asks `cuts_number` about the span it was handed. Nothing stops a
    trace declaring its span one character to the right, which is what
    `test_a_span_moved_onto_the_comma_no_longer_closes_the_books` below does.

    The reason the entry exists is what decides its edge. A recorder cuts a
    sentence out of a paragraph and keeps or drops the mark that ended the
    previous one - and a mark that ended a sentence is followed by a space and
    a letter, never by a digit with nothing between. So a mark standing against
    a digit at the head of a span is not that mark, and it is content.

    The two quotation marks keep their place on the list, and that is measured
    rather than assumed: 2 of the 5615 real quotes open with a quotation mark
    against a digit and 0 open with any other mark of the list, while 277 end
    on a digit followed by a full stop and 2 on a digit followed by a comma.
    The head and the tail are not the same question.
    """
    assert not said_the_same(",500 people affected", "500 people affected")
    assert not said_the_same("500 people affected", ",500 people affected")
    assert not said_the_same(".5% of assets was booked", "5% of assets was booked")
    assert not said_the_same("5% of assets was booked", ".5% of assets was booked")
    assert not said_the_same(',500 people affected', '"500 people affected')

    # the two quotation marks, and an ellipsis marking what was left out
    assert said_the_same('"5% of assets was booked', "5% of assets was booked")
    assert said_the_same("'5% of assets was booked", "5% of assets was booked")
    assert said_the_same("…5% of assets was booked", "5% of assets was booked")
    assert said_the_same("…500 people affected", "500 people affected")

    # the tail of a span is the writer's own punctuation, not a number's
    assert said_the_same("the people affected were 500.", "the people affected were 500")
    assert said_the_same("the people affected were 500,", "the people affected were 500")
    assert said_the_same("the people affected were 500", "the people affected were 500.")

    # and a comma that has a word in front of it is still the clause comma the
    # list is about
    assert said_the_same("affected, 500 people were counted",
                         "affected 500 people were counted")


def test_a_span_moved_onto_the_comma_no_longer_closes_the_books():
    """The forgery the review built, through the real path and the exit code.

    The trace is honest about where it read: the span it declares really does
    slice `,500 people affected` out of the document. What it says that slice
    says is `500 people affected`. Nothing outside `says_the_same` can catch
    that, because the locate that would refuse to cut a number here runs on the
    other side of the library, in `propose/`, and the audit never sees it."""
    doc = "The report found that 1,500 people affected had been counted twice."
    honest = doc.index("1,500")
    end = doc.index(" had")
    trace = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer",
                       "content": "The committee published."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"],
                   "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 24}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#{honest}-{end}",
                     "quoted_span": "1,500 people affected"}],
    }
    assert close_books(load_run(trace)).books_balance

    forged = dict(trace)
    forged["entries"] = [dict(trace["entries"][0],
                              account=f"EVIDENCE:doc#{honest + 1}-{end}",
                              quoted_span="500 people affected")]
    assert doc[honest + 1:end] == ",500 people affected"
    assert not close_books(load_run(forged)).books_balance


def test_a_grouping_comma_does_not_follow_the_fraction_of_a_decimal():
    """A number groups its whole part in threes and nothing groups a fraction,
    so `0.16355140186915887,763.6387850467289` is two numbers in a tuple and
    not one number with a separator in it.

    The rule read it as one, because the pattern it reads a number with is
    shown a single digit of context and cannot see that those digits are
    already behind a decimal point. Dropping the space after the comma of a
    tuple is what a recorder does, and the gate refused it - one real pair of
    the 118 of that shape in the corpus, and the first finding of this project
    that came from the instrument reading a number by a road of its own rather
    than by a copy of this pattern.

    The line the other way is the one this must not cross: a grouping comma
    with three digits behind a whole number still groups.
    """
    source = "output: (700, 0.16355140186915887, 763.6387850467289, True)"
    assert said_the_same(source, source.replace(", 763", ",763"))
    assert said_the_same("the ratio 2.5, 300 and the rest",
                         "the ratio 2.5,300 and the rest")

    assert not said_the_same("rooms 66,300 were counted", "rooms 66300 were counted")
    assert not said_the_same("since 1998, 183 people were expelled",
                             "since 1998,183 people were expelled")
    assert not said_the_same("the count 66 300 was taken", "the count 66300 was taken")


# --------------------------------------------------------------------------- #
# what the external review of round 4 demonstrated: the boundary, not the mark
# --------------------------------------------------------------------------- #

def _books(doc: str, start: int, end: int, quoted: str) -> bool:
    """Do the books balance for one entry citing `doc[start:end]` as `quoted`?

    The trace is honest about everything a trace can be honest about: the
    document is there, the step had it in hand, and the span it declares really
    is a slice of that document. What it says that slice says is `quoted`. This
    is the whole of the forgery the round-4 review built, and it goes through
    `close_books` rather than through the gate function, because the defect it
    carries is one `says_the_same` is never asked about.
    """
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


def test_a_span_cut_through_a_number_does_not_close_the_books():
    """The forgery that needs no mark at all, through the real path.

    `,500 people affected` cited as `500 people affected` was closed in round 4
    inside `says_the_same`, and the review's answer was that a forger who is
    not obliged to carry the comma will not carry it. Declare the span one
    character further right and the citation is the span **word for word**: the
    equality fast path in `verify_entry` answers before any rule is consulted,
    and no comparison of two strings, at any width, can tell this from an
    honest quote. The signal is not in the pair of strings. It is in where the
    span was cut, and that is a question about the source and two offsets.
    """
    doc = "The report found that 66 300 people affected had been counted twice."
    cut = doc.index("300 people")
    end = doc.index(" had")
    assert doc[cut:end] == "300 people affected"
    assert not _books(doc, cut, end, "300 people affected")

    # the same document quoted honestly: the boundary is outside the number
    whole = doc.index("66 300")
    assert _books(doc, whole, end, "66 300 people affected")
    assert _books(doc, doc.index("had been"), len(doc) - 1, "had been counted twice")


def test_every_setting_of_one_number_is_cut_the_same_way():
    """The class, listed by its members rather than shown a typical one (§44).

    Each separator this project reads inside a number, at each edge of the
    span, by both roads a forger has: carrying the mark and leaving it outside.
    `66'300` is not here because an apostrophe is not a separator this project
    has declared - it is content by not being on the allow-list, and
    `test_a_mark_against_a_digit_at_the_head_of_the_span_is_content` prices
    that decision.
    """
    for separator in (" ", ",", ".", ":", ""):
        number = f"66{separator}300"
        doc = f"The report found that {number} people affected were counted."
        head = doc.index(number)
        end = doc.index(" were")
        # the span begins at the last group, and the citation is word for word
        assert not _books(doc, head + len(number) - 3, end, "300 people affected"), separator
        # the span carries the separator and the citation drops it
        if separator:
            assert not _books(doc, head + 2, end, "300 people affected"), separator

        tail = f"The report counted {number} people."
        stop = tail.index(number) + 2
        # the span ends inside the number, with and without the separator
        assert not _books(tail, 0, stop, "The report counted 66"), separator
        if separator:
            assert not _books(tail, 0, stop + 1, "The report counted 66"), separator


def test_a_boundary_that_cuts_no_number_is_left_alone():
    """The price of the line drawn above, asserted rather than hoped for.

    Two numbers standing beside each other are two, and a span may begin or end
    at either of them. The mark that divides them is the same mark that groups
    one number - that is the whole difficulty - and what tells them apart is
    what the edit leaves: `1,500` is one number and `1, 500` is two, by the
    convention this branch declared and `bench/quote_gate_corpus.py` measures.
    """
    doc = "Since 1998, 183 people were expelled from 66,300 homes in March 11, 2011."
    for quote in ("183 people were expelled", "66,300 homes", "2011.",
                  "Since 1998,", "183 people were expelled from 66,300 homes"):
        at = doc.index(quote)
        assert _books(doc, at, at + len(quote), quote), quote

    # a number the writer's own sentence or clause punctuation follows: 277 of
    # the 5615 real quotes end on a digit and a full stop, 2 on a digit and a
    # comma, and none of them is a cut
    ends = "The ratio fell to 5. The next year it rose. It reached 12, then fell."
    for quote in ("The ratio fell to 5", "It reached 12"):
        at = ends.index(quote)
        assert _books(ends, at, at + len(quote), quote), quote


def test_the_verdict_path_names_the_cut_rather_than_the_text():
    """A reader of a report is told which gate failed, and `span_mismatch` says
    the text at the address is not what was quoted. Here the text at the
    address **is** what was quoted, character for character, and the defect is
    the address. A reader given `span_mismatch` for this entry would look at
    the span, find the citation identical to it, and have nowhere to go.

    The string is written out here rather than imported, so that renaming the
    reason in `verify.py` fails this test rather than passing it quietly.
    """
    doc = "The report found that 1,500 people affected had been counted twice."
    cut = doc.index("500 people")
    end = doc.index(" had")
    trace = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer",
                       "content": "The committee published."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"],
                   "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 24}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#{cut}-{end}",
                     "quoted_span": "500 people affected"}],
    }
    run = load_run(trace)
    entry = verify.verify_run(run)[0]
    assert entry.verified is False
    assert entry.reason == "span_cuts_a_number"


# --------------------------------------------------------------------------- #
# round 6: the boundary of a span, read as a word of the source
# --------------------------------------------------------------------------- #

def test_a_span_cut_through_a_word_does_not_close_the_books():
    """The forgery round 5 named and left open, through the real path.

    `unsafe` cited as `safe` is the same event as `1,500 people` cited as
    `500 people`, with letters where that one has digits, and it is worse: the
    sense of the sentence is the opposite of the source's while the citation
    matches its span character for character. The equality fast path in
    `verify_entry` answers it, so no rule over two strings is ever asked.
    """
    doc = "The trial found the drug is unsafe for children under twelve."
    cut = doc.index("safe for")
    assert not _books(doc, cut, len(doc), "safe for children under twelve.")
    # and the other road to the same citation: the span carries the prefix and
    # the citation drops it. This one `says_the_same` was already refusing.
    assert not _books(doc, doc.index("unsafe"), len(doc),
                      "safe for children under twelve.")


@pytest.mark.parametrize("source,keep,cited", [
    ("The report called the plan unsafe for the town.", "safe for the town.", "un-"),
    ("The report called the site insecure for the town.", "secure for the town.", "in-"),
    ("The report called it impossible for the town.", "possible for the town.", "im-"),
    ("The report called the gas nonlethal in the town.", "lethal in the town.", "non-"),
    ("The report called the gas non-lethal in the town.", "lethal in the town.", "non- hyphen"),
    ("The report called the claim disproved by the town.", "proved by the town.", "dis-"),
    ("It was a notable effect on the outcome of the vote.", "able effect on the outcome "
     "of the vote.", "round 5's own pair"),
    ("Unsafe conditions were reported at the plant.", "safe conditions were reported at "
     "the plant.", "the word opens the sentence"),
    ("THE REPORT CALLED THE PLAN UNSAFE FOR THE TOWN.", "SAFE FOR THE TOWN.", "all capitals"),
])
def test_a_prefix_that_reverses_the_sentence_may_not_be_left_outside(source, keep, cited):
    """The class listed by its members rather than shown a typical one (§44).

    Each of these is a citation whose sense is the opposite of its source's and
    which matches its span word for word. Nothing in `tokens.py` names `un-`,
    `in-` or `non-`: what is refused is a boundary falling inside a word, and
    these are the members of that class the round was opened for.
    """
    at = source.index(keep)
    assert not _books(source, at, len(source), keep), cited


@pytest.mark.parametrize("source,stop,cited", [
    ("The blast was reported harmless by the office.", "The blast was reported harm",
     "-less"),
    ("The office said the report wasn't ready.", "The office said the report was", "-n't"),
    ("The office said the report doesn't hold.", "The office said the report does",
     "-n't after a plural"),
    # The three above cut between two letters and never reach the apostrophe.
    # These do: the cut falls directly in front of it, and only the clause that
    # holds a word together across an apostrophe refuses them. Written after a
    # mutant that dropped the apostrophe from `_WORD_JOINERS` survived the
    # three above (project rule 7).
    ("The plant can't run at full load.", "The plant can", "the cut lands on the apostrophe"),
    ("The motion won't carry in the house.", "The motion won", "the same, and `won` is a word"),
    ("O'Brien was named to the committee.", "O", "a name held together by an apostrophe"),
])
def test_a_suffix_that_reverses_the_sentence_may_not_be_left_outside(source, stop, cited):
    """The same at the other edge, including the apostrophe that holds `wasn't`
    together: `was` is not what a source saying `wasn't` says."""
    assert not _books(source, 0, len(stop), stop), cited


def test_a_boundary_that_cuts_no_word_is_left_alone():
    """The price of the line drawn above, asserted rather than hoped for.

    A boundary between two letters is a cut unless it is a seam a scrape left,
    and that exception is why 118 real spans round 5 counted are not refused:
    92 of them are the letter of a literal `\n` and a capital, 26 a word run
    into a capitalised word. The five below are copied out of the two
    published runs rather than described (§37). What the check does refuse on
    real data since round 7 - 4 of the 5615 spans - is priced in `tokens.py`.
    """
    for before, after in (
            ("un poblado de Sora.\\n\\n", "El poblado fue fundado el doce."),
            ("the page - Wikipedia", "It became full in the year."),
            ("\\nMonte Carlo results", "The Venezuelan Declaration was read."),
            ("and Kashmir National Conference", "Deputy chief minister was named."),
            ("the Minister of Bihar", "In office two years he stayed.")):
        doc = before + after
        assert _books(doc, len(before), len(doc), after), doc
        assert _books(doc, 0, len(before), before), doc

    # and the ordinary boundaries a recorder actually cuts on
    plain = "That was agreed. The result was safe and the plant went on. It held."
    for quote in ("The result was safe", "safe and the plant went on.",
                  "That was agreed.", "and the plant went on"):
        at = plain.index(quote)
        assert _books(plain, at, at + len(quote), quote), quote

    # a hyphen with no letter after it does not reach across the boundary. A
    # dash between two letters does, whichever dash it is: round 7, D1, and
    # `test_a_dash_of_any_kind_holds_a_word_together` below
    for doc, quote in (("The report said half- and then it stopped.", "The report said half"),):
        at = doc.index(quote)
        assert _books(doc, at, at + len(quote), quote), quote


def test_the_verdict_path_names_the_word_rather_than_the_text():
    """A reader is told which gate failed. The text at the address here **is**
    what was quoted, character for character, so `span_mismatch` would send a
    reader to look at a span identical to its citation. The string is written
    out rather than imported, so renaming the reason fails this test."""
    doc = "The trial found the drug is unsafe for children under twelve."
    cut = doc.index("safe for")
    trace = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer",
                       "content": "The committee published."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"],
                   "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 24}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#{cut}-{len(doc)}",
                     "quoted_span": "safe for children under twelve."}],
    }
    entry = verify.verify_run(load_run(trace))[0]
    assert entry.verified is False
    assert entry.reason == "span_cuts_a_word"


def test_the_seam_is_the_only_exception_and_it_is_one_literal():
    """The exception is a pair of Unicode categories in one module-level
    literal, so that widening it is one visible edit. A capital after a capital
    and a small letter after a capital are not seams, and stay cuts.

    Round 7 narrowed what may stand on either side of the pair (C3, the
    owner's decision) and made every dash hold a word as the hyphen does (D1):
    `London–Paris` was asserted here as two words, and the owner decided it is
    one, because `non–lethal` typed with an en dash is one word cited as
    `lethal`. The assertion is turned rather than deleted (project §26.4)."""
    from tallystick.tokens import _A_SEAM, _inside_one_word
    assert _A_SEAM == ("Ll", "Lu")
    assert not _inside_one_word("resultsThe", len("results"))   # the seam
    assert _inside_one_word("UNSAFE", 2)                        # capital, capital
    assert _inside_one_word("Unsafe", 2)                        # capital then small
    assert _inside_one_word("unsafe", 2)                        # small, small
    assert _inside_one_word("wasn't", 3)                        # across an apostrophe
    assert not _inside_one_word("half- ", 4)                    # a hyphen with no letter
    assert _inside_one_word("London–Paris", 7)                  # an en dash holds, D1
    assert _inside_one_word("NoSQL", 2)                         # not a seam, C3


def test_a_word_and_a_number_are_two_classes_and_neither_reads_the_other():
    """`cuts_a_word` says nothing about a boundary between two digits and
    `cuts_a_number` says nothing about one between two letters. A boundary
    where a letter meets a digit belonged to neither until round 7; it is the
    word check's now (`$5M` cited as `$5`, `B12` as `12`), and the number check
    still says nothing about it."""
    from tallystick.tokens import cuts_a_number, cuts_a_word
    assert cuts_a_word("the drug is unsafe", 14, 18)
    assert not cuts_a_number("the drug is unsafe", 14, 18)
    assert cuts_a_number("the count 66 300 people", 13, 23)
    assert not cuts_a_word("the count 66 300 people", 13, 23)
    assert cuts_a_word("it cost $5M today", 8, 10)
    assert not cuts_a_number("it cost $5M today", 8, 10)
    # a word ending in a small letter, then a digit: the seam of a table,
    # copied from Magentic_One__040 rather than made up
    seam = "graph; \nemotions1 Introduction\nThe semantic"
    at = seam.index("1 Introduction")
    assert not cuts_a_word(seam, at, len(seam))
    assert not cuts_a_number(seam, at, len(seam))


# --------------------------------------------------------------------------- #
# round 7: the boundary reads the text the comparison reads
# --------------------------------------------------------------------------- #
#
# The external review of rounds 5 and 6: the comparison reads a quote after
# `normalize`, the boundary read the source raw, and a word or a number the
# comparison saw whole could be cut at the boundary. Each test below failed on
# 7b0946e by the answer it asserts, not by an import.

@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u2007", "\u3000"],
                         ids=["no-break", "narrow no-break", "thin", "figure", "ideographic"])
def test_any_space_groups_a_number_at_the_boundary_as_it_does_in_the_comparison(space):
    """`66 300` with a no-break space is one number to the comparison, which
    folds every whitespace to a space before it reads anything; the boundary
    read it raw and saw two. Cited from its `300`, word for word, it closed the
    books."""
    doc = f"The report found that 66{space}300 people affected were counted."
    head, end = doc.index("66"), doc.index(" were")
    assert not _books(doc, head + 3, end, "300 people affected")
    assert not _books(doc, head + 2, end, "300 people affected")
    assert said_the_same(f"66{space}300 people", "66,300 people")


@pytest.mark.parametrize("doc,quote", [
    ("About 3/4 cup of flour was used.", "4 cup of flour was used."),
    # the real span round 7's Step 0 found, copied from Magentic_One__009
    ("each kid will eat about 1/2 a potato of mashed potatoes,", "2 a potato of mashed "
     "potatoes,"),
    ("The census found 66٬300 people.", "300 people."),
    ("The recipe asks for 1½ cups of flour.", "½ cups of flour."),
    ("The chapter numbered ⅧⅡ was left out.", "Ⅱ was left out."),
], ids=["a slash", "1/2 a potato", "the Arabic thousands separator", "a vulgar fraction",
        "a Roman numeral"])
def test_a_mark_between_two_digits_holds_one_number_whatever_mark_it_is(doc, quote):
    """Any punctuation between two numerals, and any numeral: `cuts_number`'s
    docstring promised `3/4` and its code asked a list that did not have the
    slash."""
    at = doc.index(quote)
    assert not _books(doc, at, at + len(quote), quote)


@pytest.mark.parametrize("mark", ["\u00ad", "\u200b", "\u200e", "\u2060"],
                         ids=["soft hyphen", "zero-width space", "left-to-right mark",
                              "word joiner"])
def test_an_invisible_character_holds_a_word_and_a_number_together(mark):
    """A format character is nothing a reader can see, so nothing a word or a
    number can end at. `normalize` drops three of these before the comparison;
    the boundary drops every character of the category, because a
    left-to-right mark inside `unsafe` is `unsafe` to a reader too."""
    doc = f"The trial found the drug is un{mark}safe for children."
    at = doc.index("safe")
    assert not _books(doc, at, len(doc), doc[at:])
    assert not _books(doc, at - 1, len(doc), doc[at:])
    num = f"The census found 66{mark}300 people."
    at = num.index("300")
    assert not _books(num, at, len(num), num[at:])


def test_a_decomposed_accent_belongs_to_its_letter():
    """`déloyal` set as `e` and a combining acute (NFD): the comparison
    composes it, the boundary saw a mark that is not a letter and let `loyal`
    through. A boundary between the letter and its accent cuts the letter."""
    doc = "Le rapport dit que le ministre est de\u0301loyal envers le pays."
    at = doc.index("loyal")
    assert not _books(doc, at, len(doc), doc[at:])
    assert not _books(doc, at - 1, len(doc), doc[at:])
    # The same boundary at the first letter of a word. Inside a word, reading
    # a boundary in a cluster as the cluster's start still lands inside the
    # word; at its first letter it lands after the space, and the cut is
    # gone. A mutant of `view` doing exactly that survived every test until
    # this line (round 7).
    doc = "Le maire a e\u0301te\u0301 e\u0301lu hier."
    at = doc.index("e\u0301lu") + 1
    assert not _books(doc, at, len(doc), doc[at:])


@pytest.mark.parametrize("doc,keep", [
    ("Отчёт назвал зав"
     "од небезопасным.",
     "безопасным."),
    ("Η ουσία είναι ακί"
     "νδυνη.", "κίνδυνη."),
    ("\u0909\u0928\u094d\u0939\u0947\u0902 \u092f\u0939 \u0928\u093e\u092a\u0938\u0902\u0926 "
     "\u0939\u0948\u0964", "\u092a\u0938\u0902\u0926 \u0939\u0948\u0964"),
    ("委员会认为这种药不安全。",
     "安全。"),
    ("الجهاز لاسلكي.",
     "سلكي."),
], ids=["Cyrillic", "Greek", "Devanagari after a vowel sign", "Chinese", "Arabic"])
def test_a_word_of_any_script_is_cut_the_same_way(doc, keep):
    """A letter is `L*` and a mark on it is `M*`, in every script. The first
    four of these were refused at 7b0946e already, by `str.isalpha`; they are
    here as the guard the round-7 brief asks for - a rule that takes only
    Latin letters for letters has to fail a test, and none did. The Devanagari
    one was not refused: its prefix ends on a vowel sign, a mark, and
    `isalpha` says no to a mark."""
    at = doc.index(keep)
    assert not _books(doc, at, len(doc), keep)


@pytest.mark.parametrize("doc,keep", [
    ("The report called the gas non–lethal in the town.", "lethal in the town."),
    ("The report called the gas non—lethal in the town.", "lethal in the town."),
    ("The London–Paris service was cut.", "Paris service was cut."),
    ("הדוח אמר אי־אפשר.",
     "אפשר."),
], ids=["en dash", "em dash", "London-Paris, the price", "the Hebrew maqaf"])
def test_a_dash_of_any_kind_holds_a_word_together(doc, keep):
    """The owner's decision D1, round 7: any dash between two letters holds
    them, as the hyphen always did. `normalize` folds seven dashes into one
    for the comparison, and the boundary could not tell `non–lethal` from
    `non-lethal` without telling `London–Paris` from `London-Paris` too. The
    price is two real spans of 5615, both `structure—there` in one file."""
    at = doc.index(keep)
    assert not _books(doc, at, len(doc), keep)


@pytest.mark.parametrize("doc,quote", [
    ("The fund raised $5M today.", "The fund raised $5"),
    ("The fund raised $5M today.", "M today."),
    ("The fund raised $5Million today.", "The fund raised $5"),
    ("Debt reached 5bn in March.", "Debt reached 5"),
    ("The road runs 10km north.", "The road runs 10"),
], ids=["$5M cut to $5", "$5M cut to M", "$5Million cut to $5", "5bn", "10km"])
def test_a_number_and_the_letter_after_it_are_one_amount(doc, quote):
    """The owner's decision E1: a digit followed by a letter is one token, with
    no seam. `$5M` cited as `$5` changes the amount a millionfold, and a seam
    for a capitalised word would have left `$5Million` cited as `$5` open."""
    at = doc.index(quote)
    assert not _books(doc, at, at + len(quote), quote)


@pytest.mark.parametrize("doc,quote,closes", [
    ("Take vitamin B12 daily.", "12 daily.", False),
    ("The strain H5N1 spread.", "5N1 spread.", False),
    ("The COVID19 wave passed.", "19 wave passed.", False),
    # copied from Magentic_One__005 and Magentic_One__040: a table's seam
    ("State of Jammu and Kashmir1 \nBakshi Ghulam Mohammad", "1 \nBakshi Ghulam Mohammad",
     True),
    ("graph; \nemotions1 Introduction\nThe semantic", "1 Introduction\nThe semantic", True),
], ids=["B12", "H5N1", "COVID19", "Kashmir1, a seam", "emotions1, a seam"])
def test_a_letter_before_a_digit_is_a_cut_unless_a_word_ends_there(doc, quote, closes):
    """The owner's decision F2: a letter directly before a digit is one token,
    unless the letter ends a word of two or more letters in a small letter -
    the seam a scraped table leaves. The strict line costs 21 of the 5615 real
    spans, this one 0. What it lets through is `Windows10` cited as `10`; that
    is printed by `bench/quote_gate_corpus.py` under `NAMED_NOT_SCORED` and not
    asserted here either way, so that closing it later fails nothing."""
    at = doc.index(quote)
    assert _books(doc, at, at + len(quote), quote) is closes


@pytest.mark.parametrize("doc,quote,closes", [
    ("The team moved to NoSQL storage.", "SQL storage.", False),
    ("It used 5 kWh a day.", "Wh a day.", False),
    ("They set disableSSL for tests.", "SSL for tests.", False),
    ("Buy the new iPhone now.", "Phone now.", False),
    ("It sold on eBay last year.", "Bay last year.", False),
    ("They met at McDonald's at noon.", "Donald's at noon.", False),
    ("They landed at LaGuardia late.", "Guardia late.", False),
    # copied from the sources: `\n` and a capital, and the scrape seam
    ("cm^3) for each option:\\nOption a: 5.50 g/cm^3", "Option a: 5.50 g/cm^3", True),
    ("\\nMonte Carlo resultsThe Venezuelan Declaration", "The Venezuelan Declaration",
     True),
    ("the graph; \\nemotions and more", "emotions and more", True),
], ids=["NoSQL", "kWh", "disableSSL", "iPhone", "eBay", "McDonald's", "LaGuardia",
        "an escape and a capital", "a scrape seam", "an escape and a small letter"])
def test_the_seam_is_a_word_run_into_a_capitalised_word_or_an_escape(doc, quote, closes):
    """The owner's decision C3. A small letter followed by a capital is a seam
    only where three letters or more with no capital after the first stand on
    the left and a capital and small letters only on the right - or where the
    small letter is the `n` of a literal `\\n`, which 92 of the 118 spans round
    5 counted turned out to be. `NoSQL`, `kWh` and `McDonald's` were seams at
    7b0946e."""
    at = doc.index(quote)
    assert _books(doc, at, at + len(quote), quote) is closes


def test_a_year_and_a_count_are_two_numbers_both_ways():
    """A space groups a number only behind one to three digits. The review of
    rounds 5-6 showed the old reading wrong both ways, and both are here,
    copied rather than retold."""
    # the forgery: a year and a count cited as one grouped number
    assert not said_the_same("in 2011 300 people died", "in 2011,300 people died")
    # the honest quote: the clause comma between a year and a count dropped
    assert said_the_same("In 1998, 183 people died", "In 1998 183 people died")
    # and the boundary: a span may begin at the count
    doc = "The flood: in 2011 300 people died."
    at = doc.index("300")
    assert _books(doc, at, len(doc), doc[at:])
    # behind one to three digits the space still groups, both ways
    assert said_the_same("found 66 300 people", "found 66,300 people")
    assert not said_the_same("found 66 300 people", "found 66300 people")
