"""An instrument for the quote gate: can a candidate rule tell drift from tampering?

Why this exists. The gate was changed twice and measured once, and the measurement
had no power. Every quote in the published runs is an exact slice of its source, so
`verify_entry`'s equality fast path answered before the rule was ever consulted: the
run reported "0 verdicts changed" and would have reported the same 0 for a rule that
refuses everything. Project rule 35: an instrument without a positive control
measures nothing, and its zero means nothing.

What this measures instead.

  * Material is REAL: the `quoted_span` of every EVIDENCE entry in the posted files
    of the two published runs (`bench/work`, `bench/work-agenthallu`, not in git).
    Without them on disk it falls back to text the repository itself carries, so a
    stranger can run it.
  * The fast path is bypassed **by construction**: the instrument never calls
    `verify_entry`. It hands a rule two strings and reads its answer, and it drops
    every pair whose sides are equal after `normalize` - those are the pairs the
    fast path would have answered, and counting them would be counting free passes.
  * **Tampering** changes what the quote says and must be refused. Each kind is
    planted **in the middle of the span and at each of its two edges** (project
    §38 / round brief): the v0.8.2 rule closed the middle and left the ends open
    while a test that only probed the middle stayed green.
  * **Drift** is what a recorder or a model does without changing what is said, and
    must be accepted: a comma dropped or added, a space too many or too few, another
    kind of quotation mark or dash, another case, a closing full stop added or
    dropped. Each is applied at the first, the middle and the last eligible place.
  * Every candidate runs beside two controls that must fail: `always accept` must
    fail the tampering side, `always refuse` must fail the drift side. **The three
    have to come out different.** If they do not, the script says the instrument is
    broken and refuses to report the candidate's number as a measurement.

What the external review of v0.8.4 added, and why each one was missing.

  * **A space whose loss makes one number out of two** - `66 300` -> `66300`,
    `Note 3. 5` -> `Note 3.5`, `since 1998, 183` -> `since 1998,183`,
    `1000: 16` -> `1000:16`. The drift side skips a space between two word
    characters by construction and the tampering side only looked beside a
    *sign*, so the first shape had 24 places in the corpus and not one pair
    built from it. The second shape was worse than missing: it was asserted to
    be drift, on the argument that every pair of its shape is a date. Counted,
    that argument is 297 dates and 114 others. The side is now decided by
    `numbers_stated` - what comes out of the edit - and not by what the sentence
    turns out to be about.
  * **A sentence mark added where the span has none** - `safe` -> `safe?`. The
    rule forbids exchanging one mark for another, and every pair that tested it
    started from a source that already carried a mark; a span ending one
    character earlier was never asked about, in 253,663 pairs.
  * **A mark that opens the span standing in front of a number** - `.5% of
    assets` -> `5% of assets`. The sweep plants each character beside a space
    and at the right-hand end, so a decimal point at the left-hand end, where
    the allow-list forgives a sentence mark, was out of its reach.

What the external review of round 3 added, and why each one was missing.

  * **The reading of a number was the rule's own.** `_ONE_NUMBER` was a copy of
    the regular expression in `tokens.py`, so the class it decided was measured
    against itself: break the convention in both files - a grouping comma of two
    digits instead of three - and the instrument still printed 100% / 100% / 0
    pass while the gate swapped its answers on `since 1998, 183`. The reading is
    now `HOW_A_NUMBER_IS_WRITTEN` and `numbers_stated`, arrived at by their own
    road, and `CONTROL the number convention widened` runs that very mutant as a
    fifth rule on every run: if it passes, the instrument is reported broken.
  * **The row, not the neighbouring mark.** v0.8.4 forgave a full stop at the
    head of a quote in front of a digit (`.5% of assets` -> `5% of assets`) and
    the round-3 fix closed that one character. The comma beside it in the same
    allow-list was not asked about, and it carries the same hole three times
    over: `,500 people affected` cited as `500 people affected` closes the books
    at exit 0. `marks_against_a_digit` now asks the whole allow-list - the
    space, the comma, the two quotation marks and the four sentence marks - at
    both edges, dropped and added, and classifies each answer by what the
    numbers do rather than by which character it was.
  * **The exit code said only whether the instrument worked.** `main` returned
    `0 if powered`, so a candidate that failed every threshold still left a zero
    behind it. It now returns 1 when the candidate's verdict is FAIL.

What the external review of round 4 added, and why it was missing.

  * **A span boundary that cuts a number in the source.** Rounds 3, 4 and 5 of
    this branch each closed one mark at the head of a span - the full stop,
    then the comma - and every one of those forgeries is the same event: the
    boundary of the span falls inside a number of the source, so the text the
    span carries states a number the source does not. `66 300 people affected`
    cited from character 3 is `300 people affected`, **word for word**, and no
    comparison of two strings can see it at any width: the citation is exactly
    what the span says. `span_pairs` builds the class as a source, two offsets
    and a citation - for every separator this project reads inside a number
    (the space, the comma, the full stop, the colon, and no separator at all),
    at both edges, with the span carrying the mark and without it - and asks
    `verify.verify_entry` rather than a rule, because the equality fast path
    inside `verify_entry` is the thing that answers.
  * **Nothing is thrown away.** 44,920 pairs of the row - the space, both
    edges, both directions - were dropped in `build` as "equal after
    normalize", and with them four of the 28 shapes went missing from the
    printed table. They are now held apart with the side each asserts, printed
    with the count the fast path answered, and the question `normalize` eats is
    asked of the tool in the boundary class instead.

What the external review of rounds 5-6 added, and why it was missing.

  * **The boundary read the source raw, and the comparison read it after
    `normalize`.** A word or a number that the comparison sees whole could be
    cut at the boundary: `66 300 people` with a no-break space, `unsafe` with
    a soft hyphen after its `un`, `déloyal` decomposed. The span classes now carry every
    whitespace, punctuation, invisible and non-digit numeral character inside
    a number (`number_sweep`) and every invisible character, dash and
    apostrophe inside a word (`word_sweep`), each property asked of Unicode at
    the head of this file and none copied from the rule.
  * **Every word in the material was Latin.** A rule that took only Latin
    letters for letters passed. The word class now carries Cyrillic, Greek,
    Devanagari, Chinese, Arabic and Hebrew, forged and honest.
  * **A capital inside a word, a number against a letter.** `NoSQL` cited as
    `SQL`, `kWh` as `Wh`, `$5M` as `$5` and as `M today`, `B12` as `12`.
  * **The `2011 300` convention was wrong both ways**, and the instrument
    asserted the wrong way: `in 2011 300 people died` cited as `2011,300` was
    accepted, `In 1998, 183` cited as `In 1998 183` refused. A space groups
    only behind one to three digits now, in `HOW_A_NUMBER_IS_WRITTEN` and in
    the walk, and both sides are planted.
  * **What the owner's decisions cost is on the record, not in a threshold.**
    `NAMED_NOT_SCORED` prints the tool's answer on the forgeries a seam lets
    through (`Windows10` -> `10`, `MacArthur` -> `Arthur`) and the honest spans
    a strict line refuses (`structure—there`), every run.

What the last review added, and why it was missing.

  * **A quote its source does not hold.** Every class asked whether a
    boundary around a citation that is in the source is drawn right. None
    asked what the proposer does when the model quotes a number the source
    does not state: `-$5 million` against `a loss of $5 million` was placed
    on `$5 million` and written into the trace as the model's quote, since
    v0.6.0. `QUOTES_THE_SOURCE_DOES_NOT_HOLD` builds a sign added, a sign
    left out, a sign turned over and a digit changed, each of which must be
    placed nowhere; `QUOTES_THE_SOURCE_HOLDS` the drift the tolerant locate
    exists for, which must be placed where it stands.

Usage:

    python bench/quote_gate_corpus.py
    python bench/quote_gate_corpus.py --posted bench/work bench/work-agenthallu

The numbers published in `bench/HISTORY.md` and `bench/results/quote_gate_corpus.json`
are the whole corpus rather than the sample `--limit` defaults to, so the line that
reproduces them is:

    python bench/quote_gate_corpus.py --limit 100000 --json bench/results/quote_gate_corpus.json

Thresholds are in THRESHOLDS below, in both directions, and they are named before a
candidate is measured. A one-sided threshold is passed trivially by a control.
It calls no model, reads no network, and needs no key.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import sys
import unicodedata
from typing import Callable, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tallystick.normalize import normalize  # noqa: E402
# `words` below is used by nothing but the archived v0.8.3 rule, which is a
# copy of a rule being measured rather than part of this instrument's reading.
from tallystick.tokens import words  # noqa: E402

#: Named before any candidate was measured, in every direction. The first two are
#: 1.00 because neither admits a trade: a rule that lets one tampered pair through
#: is not a barrier, and one that refuses one typographic pair is not usable. The
#: third is the one this round added, and it is the only one that does not depend
#: on anybody having thought of a case: **outside the characters a rule names as
#: setting, no character may be droppable.** Four rules in a row were lists of
#: what counts as content, and each was broken by a character the list had not
#: thought of; a rule that names what may differ can be swept instead of guessed
#: at, and the sweep is `characters_forgiven` below. These are conditions, not a
#: score to be improved on.
THRESHOLDS = {
    "tampering_refused": 1.00,   # every meaning-changing pair, at every site
    "drift_accepted": 1.00,      # every typographic pair the rule is asked about
    "characters_forgiven": 0,    # outside the list the rule itself names
    # The two this round adds, named here before the rule is touched. They are
    # about the **tool's** answer and not the rule's: the pairs they score are
    # a source, two offsets and a citation, and they go through
    # `verify.verify_entry`, fast path and all. A threshold on `says_the_same`
    # cannot see a forgery that `says_the_same` is never asked about.
    "spans_through_a_number_refused": 1.00,   # every span boundary cutting a number
    "spans_between_numbers_accepted": 1.00,   # every boundary that cuts none
    # The class this round adds, named before the rule is touched and at the
    # same 100 / 100 as the one before it. Letters where that one has digits,
    # and the more dangerous of the two: `unsafe` cited as `safe` inverts the
    # sense of the sentence while the citation stays word for word.
    "spans_through_a_word_refused": 1.00,     # every span boundary cutting a word
    "spans_between_words_accepted": 1.00,     # every boundary that cuts none
    # Round 9, named before the rule is touched. The other side of the
    # boundary, on the material it meets every day: a value in the output of a
    # real tool, cited as it stands, must close the books - every one outside
    # the kind the owner named (`a_member_of_a_list_of_numbers`). And the
    # proposer, asked the classes built by hand, must place nothing the gate
    # refuses, place every honest citation where it stands and no forged one
    # where it was forged.
    "tool_output_values_accepted": 1.00,
    "proposer_agrees_with_the_gate": 1.00,
    # The round of the search, named here before the rule is touched. Every
    # class above asks about a citation its source holds. These two ask what
    # the proposer does with one it does not: a quote whose sign or digit the
    # source does not state must be placed nowhere, every one of them - a span
    # placed for it is a quote rewritten into the trace. And the drift the
    # tolerant locate exists for, a full stop or quotation marks the model
    # added, must still be placed where it stands, every one: a rule that
    # refuses every word match would pass the first alone.
    "proposer_places_no_quote_the_source_lacks": 1.00,
    "proposer_places_honest_drift_where_it_stands": 1.00,
}

Pair = Tuple[str, str, str, str]        # (kind, site, source, as written)


# --------------------------------------------------------------------------- #
# what a character is, asked of Unicode and of nothing in the rule
# --------------------------------------------------------------------------- #

# Round 7's brief: the instrument takes no list of characters from the rule and
# keeps no copy of one. Every property a class below needs is asked here of the
# character's Unicode category or name, and each is one line so that what the
# instrument believes about a character can be read in one place. None of these
# functions exists in `tallystick/`, and none of them imports anything from it.
#
# Two lists stay written out further down, and they are not readings of a
# character: `MAY_DIFFER` / `MAY_BE_DROPPED_*` are the allow-list under test -
# the claim the sweep holds the rule to, a decision and not a property - and
# `_INSIDE_A_NUMBER` is the project's declared convention for a number
# (docs/design.md). A category cannot say which marks a project has chosen to
# forgive.

def _letter(ch: str) -> bool:
    """A letter of any script, and a mark that sits on one: `L*` and `M*`. The
    accent of a decomposed `é` and the vowel sign of a Devanagari syllable
    are part of the letter they belong to, and `str.isalpha` says no to both."""
    return unicodedata.category(ch)[0] in "LM"


def _numeral(ch: str) -> bool:
    """A digit or any other character that writes a number: `N*`. `½`, `²` and
    `Ⅻ` are numbers to a reader, and `1½` cut after the `1` is `1`."""
    return unicodedata.category(ch)[0] == "N"


def _small(ch: str) -> bool:
    return unicodedata.category(ch) == "Ll"


def _capital(ch: str) -> bool:
    return unicodedata.category(ch) == "Lu"


def _invisible(ch: str) -> bool:
    """A format character, `Cf`: the soft hyphen, the zero-width space and
    joiners, the bidirectional marks. Nothing a reader sees, so nothing that
    can end a word or a number."""
    return unicodedata.category(ch) == "Cf"


def _punctuation(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "P"


def _dash(ch: str) -> bool:
    """Every dash Unicode has, `Pd`: the hyphen, the en and em dash, the Hebrew
    maqaf, the fullwidth hyphen-minus."""
    return unicodedata.category(ch) == "Pd"


def _sign(ch: str) -> bool:
    """A mark that turns the number after it into another number: a dash, or a
    character Unicode names a plus or a minus sign."""
    return _dash(ch) or unicodedata.name(ch, "") in ("PLUS SIGN", "MINUS SIGN")


#: Unicode's apostrophes, by name, and the right single quotation mark that
#: English typesetting uses as one (`wasn’t`). Enumerated from the character
#: database rather than written out, so a list in the rule cannot be copied here.
_APOSTROPHES = frozenset(
    [chr(cp) for cp in range(0x10000)
     if "APOSTROPHE" in unicodedata.name(chr(cp), "") and not _letter(chr(cp))]
    + ["’"])


def _word_character(ch: str) -> bool:
    """What a word or a number is made of: a letter, a mark or a numeral."""
    return _letter(ch) or _numeral(ch)


# --------------------------------------------------------------------------- #
# how this instrument reads a number, and why it is written here twice
# --------------------------------------------------------------------------- #

# The external review of round 3 found the measurement of one whole class to be
# a tautology: the instrument decided "one number or two" with `_ONE_NUMBER`, a
# character-for-character copy of the regular expression the rule decides it
# with. It proved it by breaking the convention in both files at once - a
# grouping comma of two digits instead of three - and watching the instrument
# report 100% / 100% / 0 pass for a rule that now reads
# `since 1998, 18 people` as one number and `since 1998, 183 people` as two.
# A copy is not an independent reading: it agrees with the rule about anything
# the rule is wrong about.
#
# So the reading below is arrived at by its own road, and it is written twice on
# purpose:
#
#   * `HOW_A_NUMBER_IS_WRITTEN` - a list of forms written out by hand, one line
#     per form, each with the reason it reads the way it does. This is the part
#     that cannot be quietly moved: changing it means changing a literal
#     `1,500` into a literal something else, with the reason beside it.
#   * `numbers_stated` - a walk over the characters that generalises the list.
#     It shares no code, no regular expression and no import with the rule.
#
# `_check_the_reading` runs the list against the walk at every start-up and
# stops the run if they disagree. A walk edited to match a broken rule fails the
# list; a list edited to match a broken walk is a visible change to a literal.

#: (the text, the numbers it states, why). The numbers are written in one form,
#: so that two settings of the same number come out the same string: a grouping
#: separator is written as a comma whichever of the two it was, and a number
#: whose head is outside the text keeps the mark that says so.
HOW_A_NUMBER_IS_WRITTEN: List[Tuple[str, List[str], str]] = [
    ("1500",        ["1500"],        "digits and nothing else"),
    ("1,500",       ["1,500"],       "a comma with exactly three digits behind it groups one number"),
    ("1 500",       ["1,500"],       "a space in the same place does the same job; which separator "
                                     "it is, is setting (docs/design.md), that there is one is not"),
    ("1, 500",      ["1", "500"],    "a comma with a space after it ends a clause or an item: two numbers"),
    ("1 , 500",     ["1", "500"],    "the same, however it is spaced"),
    ("1,5000",      ["1", "5000"],   "four digits behind the comma: no grouping anybody writes"),
    ("1,50",        ["1", "50"],     "two digits behind the comma: no grouping anybody writes"),
    ("11,2011",     ["11", "2011"],  "why `March 11, 2011` cited as `March 11,2011` is the same "
                                     "quote: four digits, so nothing is grouped"),
    ("1,500,000",   ["1,500,000"],   "groups repeat"),
    ("1998 183",    ["1998", "183"], "a space groups a number only behind a group of one to "
                                     "three digits, because nobody writes `1998 183` for "
                                     "1,998,183 and everybody writes `in 2011 300 people died`. "
                                     "So `since 1998, 183 people` cited as `since 1998 183 "
                                     "people` states the same two numbers (round 7)"),
    ("66 300",      ["66,300"],      "behind two digits the space groups, as it always has"),
    ("2011 300 400", ["2011", "300,400"], "the question is asked of the digits directly "
                                     "behind each space, not of the head of a chain: `300 400` "
                                     "is one number whatever stands before it"),
    ("1234,567",    ["1234,567"],    "a comma groups whatever the length of the group behind "
                                     "it: a comma with no space after it divides no words, and "
                                     "`2011,300` is one number written wrongly, not two"),
    ("66300",       ["66300"],       "the separator has to be there: `66,300` cited as `66300` is "
                                     "a citation the source did not write"),
    ("3.5",         ["3.5"],         "a decimal point between two digits"),
    ("3. 5",        ["3", "5"],      "a full stop with a space after it ends a sentence: two numbers"),
    ("3.50",        ["3.50"],        "the digits after the point are kept as written"),
    ("10:30",       ["10:30"],       "the colon of a time"),
    ("10: 30",      ["10", "30"],    "with a space after it, two numbers"),
    ("1000:16",     ["1000:16"],     "why `1000: 16` may not be cited as `1000:16`"),
    (".5",          [".5"],          "nothing in front of the point: the head of the number is "
                                     "outside this text, and `.5% of assets` is not `5% of assets`"),
    (",500",        [",500"],        "the same at the head with a comma: `,500 people` is the tail "
                                     "of `1,500 people` and not `500 people`"),
    (":30",         [":30"],         "the same at the head with a colon"),
    ("500.",        ["500"],         "a mark with no digit after it is not inside a number: at the "
                                     "end of a quote it is the sentence's own full stop, and 277 of "
                                     "the 5615 real quotes end on a digit and one"),
    ("500,",        ["500"],         "the same for a clause comma at the end of a quote"),
    ("'90s",        ["90"],          "an apostrophe is not a separator this project has declared: "
                                     "`66'300` and `'90s` are read as written, and the two "
                                     "quotation marks keep their place on the allow-list"),
    ("2,5/3",       ["2", "5", "3"], "no number is spelled `2,5` under this convention, so the "
                                     "citation `0 gives 2,5/3` says what `0 gives 2, 5/3` says"),
    ("-5",          ["5"],           "a sign is not read here: it is content for the rule by being "
                                     "outside the allow-list, and this reading is about numbers "
                                     "only, so it must not be asked to carry the sign as well"),
    ("3.117947442816187e-07", ["3.117947442816187", "07"],
                                     "an exponent is two runs to this reading. It is named here "
                                     "rather than handled: the pair it appears in is tampering for "
                                     "the reason above it, not for this one"),
]

#: The marks that can stand **inside** one number, and the whole of them. Each
#: is here because a number is written with it, not because a rule says so:
#: the decimal point of `3.5`, the grouping comma of `1,500`, the colon of
#: `10:30`. The grouping space is the fourth, and it is not a mark. An
#: apostrophe is deliberately not here - see the entry for `'90s` above.
_INSIDE_A_NUMBER = ".,:"


def numbers_and_where(text: str) -> List[Tuple[int, int, str]]:
    """Every number `text` states, in order: (where it starts, where it ends,
    how it is written).

    Walks the characters. A number is a run of digits, extended by a grouping
    separator that carries exactly three digits (`,` or a space, as often as it
    repeats) and then at most one decimal point or time colon with its digits.
    A space groups only behind one to three digits: `in 2011 300 people died` is
    a year and a count, and no one writes a number with a head of four digits
    and a space.
    A run that begins right after one of `_INSIDE_A_NUMBER` standing at the head
    of the text keeps that mark, because there is no reading of it there except
    the inside of a number whose head this quote does not carry: nothing in
    English begins with one.

    The same at the other end deliberately does not hold. A mark with no digit
    after it is not inside a number, and at the end of a quote it is the
    writer's own sentence or clause punctuation - `... expelled in 2011.` is
    277 of the 5615 real quotes and `... in 2011,` is 2 more, against 0 quotes
    that begin with an allow-listed mark against a digit. The asymmetry is
    orthography, and it is measured rather than assumed.

    The two positions are what the round-4 review's finding needs and what a
    reading of one string alone cannot give: whether a **span boundary** falls
    inside a number is a question about the source and two offsets, and
    `normalize` has folded the answer away long before two strings are
    compared. `span_cuts_a_number` below is the whole use of them.
    """
    out: List[Tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit():
            i += 1
            continue
        j = i
        while j < n and text[j].isdigit():
            j += 1
        value = text[i:j]
        group = j - i                        # the digits directly behind the separator
        while (j + 4 <= n and text[j] in ", " and text[j + 1:j + 4].isdigit()
               and not (j + 4 < n and text[j + 4].isdigit())
               and (text[j] == "," or group <= 3)):
            value += "," + text[j + 1:j + 4]
            j += 4
            group = 3
        if j + 1 < n and text[j] in ".:" and text[j + 1].isdigit():
            k = j + 1
            while k < n and text[k].isdigit():
                k += 1
            value = value + text[j] + text[j + 1:k]
            j = k
        start = i
        if i == 1 and text[0] in _INSIDE_A_NUMBER:
            value = text[0] + value
            start = 0
        out.append((start, j, value))
        i = j
    return out


def numbers_stated(text: str) -> List[str]:
    """Every number `text` states, in order, each written in one form."""
    return [value for _start, _end, value in numbers_and_where(text)]


#: Where a span may not begin or end, in the numbers of a source: (the text, the
#: numerals a boundary may not fall inside, why). A second reading beside
#: `HOW_A_NUMBER_IS_WRITTEN` and not the same one, because the two questions are
#: not the same. That list says which numbers a string **states**, and `2,5/3`
#: states three of them. This one says where a **boundary** may be drawn, and a
#: boundary drawn between `2` and `/3` leaves the source's `5/3` behind it, which
#: is the event of `1/2 a potato` cited as `2 a potato` (round 7).
HOW_A_NUMERAL_IS_CUT: List[Tuple[str, List[str], str]] = [
    ("66,300",        ["66,300"],        "a comma between two digits"),
    ("66 300",        ["66 300"],        "the grouping space, behind one to three digits"),
    ("66\u00a0300",   ["66\u00a0300"],   "any whitespace groups as the space does: a no-break "
                                         "space is how a typesetter keeps a number on one "
                                         "line"),
    ("66\u202f300",   ["66\u202f300"],   "the narrow no-break space, which SI prescribes for it"),
    ("2011 300",      ["2011", "300"],   "behind four digits a space groups nothing: a year "
                                         "and a count (round 7)"),
    ("3/4",           ["3/4"],           "a slash between two digits: a fraction, and "
                                         "`3/4 cup` cited as `4 cup` states another amount"),
    ("1/2",           ["1/2"],           "the real span of round 7's Step 0: `1/2 a potato` "
                                         "cut after the slash"),
    ("66\u066c300",   ["66\u066c300"],   "the Arabic thousands separator is punctuation (Po) "
                                         "between two digits like any other"),
    ("2020-2021",     ["2020-2021"],     "a dash between two digits"),
    ("10:30",         ["10:30"],         "a colon between two digits"),
    ("3. 5",          ["3", "5"],        "a mark with a space after it ends a sentence"),
    ("1, 500",        ["1", "500"],      "a comma with a space after it ends a clause"),
    ("66\u200b300",   ["66\u200b300"],   "an invisible character inside a number is not a "
                                         "place a reader can see a number end"),
    ("1\u00bd",       ["1\u00bd"],       "a vulgar fraction is a numeral (No): `1\u00bd` cut after "
                                         "the `1` is `1`"),
    ("0.163 763",     ["0.163", "763"],  "nothing groups a fraction: a space behind the digits "
                                         "of a decimal part groups nothing"),
    ("007\u2022398",  ["007", "398"],    "a bullet separates a title and a count "
                                         "(`James Bond 007\u2022398K views`, round 9)"),
]


def numerals_and_where(text: str) -> List[Tuple[int, int, str]]:
    """Every numeral of `text` a span boundary may not fall inside: (start,
    end, as written).

    A run of numerals, held together across any punctuation mark standing
    alone between two of them (`3/4`, `66,300`, `2020-2021`, `66٬300`), across
    any invisible character, and across one run of whitespace when the digits
    directly behind it are one to three and not the fraction of a decimal, and
    exactly three stand after it. Every property is asked of Unicode above.
    """
    out: List[Tuple[int, int, str]] = []
    i, n = 0, len(text)

    def skip_invisible(k: int) -> int:
        while k < n and _invisible(text[k]):
            k += 1
        return k

    while i < n:
        if not _numeral(text[i]):
            i += 1
            continue
        start, j = i, i
        group_at = i                   # where the digits behind the next separator begin
        after_a_point = False
        while True:
            k = j
            while k < n and _numeral(text[k]):
                k += 1
            j = k
            nxt = skip_invisible(j)
            if nxt < n and nxt > j and _numeral(text[nxt]):
                j = nxt                  # an invisible character inside the run
                continue
            # A mark the table decides by meaning to stand apart even between
            # two digits (the bullet of `007•398K views`, round 9) holds
            # nothing together; every other punctuation mark does.
            if (j + 1 < n and _punctuation(text[j])
                    and _decision(text[j], "between") != APART
                    and skip_invisible(j + 1) < n
                    and _numeral(text[skip_invisible(j + 1)])):
                after_a_point = after_a_point or text[j] in ".:"
                j = skip_invisible(j + 1)
                group_at = j
                continue
            if j < n and text[j].isspace():
                w = j
                while w < n and text[w].isspace():
                    w += 1
                three = w + 3 <= n and all(_numeral(c) for c in text[w:w + 3]) \
                    and not (w + 3 < n and _numeral(text[w + 3]))
                if three and 1 <= j - group_at <= 3 and not after_a_point:
                    j = w
                    group_at = w
                    continue
            break
        out.append((start, j, text[start:j]))
        i = j
    return out


def span_cuts_a_number(source: str, start: int, end: int) -> bool:
    """Does the span `[start, end)` of `source` begin or end inside a number?

    A number of the source either lies wholly inside the span or wholly outside
    it; anything else is a boundary drawn through a number, and the text the
    span carries states a number the source does not. `66 300 people` cited
    from character 3 is `300 people`; `1,500 people` cited from character 2 is
    `500 people`; `1/2 a potato` cited from character 2 is `2 a potato`; and
    the citation can be word for word, which is why no comparison of two
    strings - at any width, with or without `normalize` - can see it. The
    question needs the source and the two offsets.

    Read by `numerals_and_where`, this instrument's own walk, which shares no
    code with the rule (project rule 14, and the round-3 review's finding that
    a copied predicate measures nothing).
    """
    return any(s < end and start < e and not (start <= s and e <= end)
               for s, e, _value in numerals_and_where(source))


def _check_the_reading() -> None:
    """The hand-written list against the walk. Stops the run if they differ."""
    wrong = [(text, want, numbers_stated(text))
             for text, want, _why in HOW_A_NUMBER_IS_WRITTEN
             if numbers_stated(text) != want]
    if _changes_the_numbers(*_A_YEAR_AND_A_COUNT):
        wrong.append((_A_YEAR_AND_A_COUNT[1], numbers_stated(_A_YEAR_AND_A_COUNT[0]),
                      numbers_stated(_A_YEAR_AND_A_COUNT[1])))
    wrong += [(text, want, [v for _s, _e, v in numerals_and_where(text)])
              for text, want, _why in HOW_A_NUMERAL_IS_CUT
              if [v for _s, _e, v in numerals_and_where(text)] != want]
    if wrong:
        for text, want, got in wrong:
            print(f"the reading disagrees with its own list: {text!r} "
                  f"is written {want} and read {got}", file=sys.stderr)
        raise SystemExit(2)


def _changes_the_numbers(source: str, cited: str) -> bool:
    """Does `cited` state numbers `source` does not?"""
    return numbers_stated(source) != numbers_stated(cited)


def _form_of_the_result(source: str, cited: str) -> str:
    """What the citation did to the numbers, in the words the round-4 brief
    asks for: one number, two numbers, or another number."""
    here, there = numbers_stated(source), numbers_stated(cited)
    if here == there:
        return "the same numbers"
    if len(here) > len(there):
        return "two numbers read as one"
    if len(here) < len(there):
        return "one number read as two"
    for a, b in zip(here, there):
        if a != b and (a.lstrip(_INSIDE_A_NUMBER) == b or b.lstrip(_INSIDE_A_NUMBER) == a):
            return "the head of a number"
    return "another number"


# --------------------------------------------------------------------------- #
# where a change goes: the middle of the span, and each of its two edges
# --------------------------------------------------------------------------- #

def _word_boundary_near(text: str, i: int) -> int:
    """The start of the word nearest `i`, so planted material never lands inside
    a word (that would be a different mutation than the one named)."""
    for step in range(len(text)):
        for j in (i - step, i + step):
            if 0 < j < len(text) and text[j - 1].isspace() and not text[j].isspace():
                return j
    return len(text) // 2


def _plant(quote: str, fragment: str, site: str) -> str:
    """`fragment` put into `quote` at one of the three sites, with the spacing a
    real span would have."""
    if site == "left edge":
        return f"{fragment} {quote}"
    if site == "right edge":
        return f"{quote} {fragment}"
    i = _word_boundary_near(quote, len(quote) // 2)
    return f"{quote[:i]}{fragment} {quote[i:]}"


#: (name, what the source says, what the recorder wrote instead). Every one of
#: these changes the meaning; §33 and the external review of v0.8.1 are where the
#: first six come from, copied rather than retold (project rule 37).
SUBSTITUTIONS: List[Tuple[str, str, str]] = [
    ("a digit changed",                  "12.4 million euro", "92.4 million euro"),
    ("the sign before a number",         "+5% this quarter",  "−5% this quarter"),
    ("the currency mark",                "€12 per unit", "$12 per unit"),
    ("a comparison sign removed",        ">50 patients",      "50 patients"),
    ("a space inside a word",            "a notable effect",  "a not able effect"),
    ("the closing mark of a statement",  "the site is safe.", "the site is safe?"),
    ("the separator inside a unit",      "8 mg/kg daily",     "8 mg kg daily"),
    ("the separator inside a number",    "66,300 records",    "66300 records"),
    ("a negation dropped",               "was not confirmed", "was confirmed"),
    # what the external review of v0.8.3 demonstrated, copied rather than retold
    ("a sign standing apart from its number", "margin - 5.2 % lower", "margin 5.2 % lower"),
    ("a bracket that carries the sign",   "net income (1.2) bn", "net income 1.2 bn"),
    ("a mark nobody put on the list",     "salinity of 5\u2030 here", "salinity of 5 here"),
    # what the external review of v0.8.4 demonstrated, copied rather than retold
    ("a mark opening the span, in front of a number", ".5% of assets", "5% of assets"),
    # what the external review of rounds 5-6 demonstrated, copied rather than
    # retold: a year and a count, cited as one number with a grouping comma
    ("a year and a count cited as one number", "in 2011 300 people died",
     "in 2011,300 people died"),
]


def tampering_pairs(quote: str) -> List[Pair]:
    """Every substitution, at every one of the three sites, plus the one kind the
    real text can carry on its own: a digit already in the quote, changed in place
    at its first, middle and last occurrence."""
    out: List[Pair] = []
    for kind, before, after in SUBSTITUTIONS:
        for site in ("left edge", "middle", "right edge"):
            out.append((kind, site, _plant(quote, before, site),
                        _plant(quote, after, site)))
    inner = [m.start() for m in re.finditer(r",", quote)
             if _between_word_characters(quote, m.start())]
    if inner:
        chosen = {"left edge": inner[0], "middle": inner[len(inner) // 2],
                  "right edge": inner[-1]}
        for site, i in chosen.items():
            out.append(("a comma inside a word dropped", site,
                        quote, quote[:i] + quote[i + 1:]))
    joins = _spaces_that_join_a_number(quote)
    if joins:
        chosen = {"left edge": joins[0], "middle": joins[len(joins) // 2],
                  "right edge": joins[-1]}
        for site, i in chosen.items():
            out.append(("a space dropped so a sign joins its number", site,
                        quote, quote[:i] + quote[i + 1:]))
    # The same event without a sign in it: the space is the only thing keeping
    # two numbers from reading as one. `66 300` -> `66300` and
    # `since 1998, 183` -> `since 1998,183`. Nothing in the instrument asked
    # about this shape before: the drift side skips a space between two word
    # characters by construction, the tampering side only looked next to a
    # sign, and 24 places of the first shape sat in the corpus with no pair
    # built from them either way.
    for site, i in _pick3(_spaces_that_join_two_numbers(quote)).items():
        out.append(("a space dropped so two numbers read as one", site,
                    quote, quote[:i] + quote[i + 1:]))
    # The same merge by the other road: the comma goes and the space left
    # behind is not a boundary any more, it is the group mark of one number.
    # The kind is named by what the reading saw happen, not by the road taken
    # to it, so a comma at the head of the quote is not filed under a merge.
    for site, i in _pick3(_commas_that_change_the_numbers(quote)).items():
        cited = quote[:i] + quote[i + 1:]
        out.append((f"a comma dropped and {_form_of_the_result(quote, cited)}",
                    site, quote, cited))
    # A mark that ends a sentence, put where the span has none. The rule may
    # let a recorder drop such a mark (the sentence was cut out of a paragraph)
    # and may let them add a full stop (their own, closing their own sentence);
    # a question the source does not ask is neither. One site, not three: an
    # opening run and a closing run is all there is.
    tail = quote.rstrip()
    if tail and not _marks_in_the_closing_run(tail):
        for mark in ("?", "!"):
            out.append(("a closing mark added where the span ended without one",
                        "right edge", quote, tail + mark))
    lead = quote.lstrip()
    if lead and not _marks_in_the_opening_run(lead):
        for mark in ("?", "!"):
            out.append(("an opening mark added where the span began without one",
                        "left edge", quote, mark + lead))
    digits = [m.start() for m in re.finditer(r"\d", quote)]
    if digits:
        chosen = {"left edge": digits[0], "middle": digits[len(digits) // 2],
                  "right edge": digits[-1]}
        for site, i in chosen.items():
            other = "8" if quote[i] != "8" else "3"
            out.append(("a digit of the real quote changed", site,
                        quote, quote[:i] + other + quote[i + 1:]))
    return out


# --------------------------------------------------------------------------- #
# drift: the same thing said, set differently
# --------------------------------------------------------------------------- #

def _pick3(places: List[int]) -> Dict[str, int]:
    if not places:
        return {}
    return {"left edge": places[0], "middle": places[len(places) // 2],
            "right edge": places[-1]}


def _commas_between_words(s: str) -> List[int]:
    """Commas a recorder can drop without saying anything else: the ordinary
    comma that ends a clause or an item, the one with a space after it.

    A comma standing between two word characters is not that comma. It is
    holding two things apart inside one word - `66,300` is a number,
    `\u03b1,\u03b2-unsaturated` is a chemical name - and taking it out merges them,
    which is the `not able` against `notable` event with a comma instead of a
    space. Those are in the tampering list, under `a comma inside a word
    dropped`. A comma before a closing quotation mark (`act," he said`) is an
    ordinary comma and stays here: the mark, not the comma, is what divides
    the words.

    Nor is it the comma of `since 1998, 183 people`. That one has a space after
    it and looks like every other clause comma, and this function said so until
    the sentence above was applied to what the edit **produces** rather than to
    what stands beside the comma: take it out and the two numbers are left side
    by side, where the grouping convention reads them as the single number
    1,998,183. `_commas_that_change_the_numbers` takes those, and they are
    tampering under their own name.
    """
    joins = set(_commas_that_change_the_numbers(s))
    return [m.start() for m in re.finditer(r",", s)
            if not _between_word_characters(s, m.start()) and m.start() not in joins]


def _commas_that_change_the_numbers(s: str) -> List[int]:
    """Commas whose loss changes what numbers the text states: `since 1998, 183`
    -> `since 1998 183`, `43, 67, 163` -> `43, 67 163`, `(360, 573,` ->
    `(360 573,`, and the comma at the head of `,500 people`.

    Decided by `numbers_stated`, which is this instrument's own reading and
    shares nothing with the rule's. The date case falls out of it rather than
    being excluded by hand: `March 11, 2011` states 11 and 2011, and so does
    `March 11,2011`, because four digits behind a comma group nothing.
    """
    out = []
    for m in re.finditer(r",", s):
        i = m.start()
        if _between_word_characters(s, i):
            # `250,000` cited as `250000` is one number mangled, not two made
            # into one, and it is already asserted under `a comma inside a word
            # dropped`. A kind has to be what its name says.
            continue
        if _changes_the_numbers(s, s[:i] + s[i + 1:]):
            out.append(i)
    return out


def _between_word_characters(s: str, i: int) -> bool:
    """Whether the mark at `i` has a word character on both sides - a letter, a
    mark or a numeral (`_word_character`) - so that removing the mark would run
    the two together.

    Until round 7 this asked a copy of `tokens.is_separator`'s two lists. A copy
    agrees with the rule about whatever the rule is wrong about, and the brief
    of round 7 is that the instrument keeps none; the question is now asked of
    Unicode categories at the head of this file."""
    before = s[i - 1] if i else " "
    after = s[i + 1] if i + 1 < len(s) else " "
    return _word_character(before) and _word_character(after)


def _spaces_touching_a_mark(s: str) -> List[int]:
    """Spaces that can go without changing what is said: those next to
    punctuation, excluding two shapes that are not spacing at all.

      * A space between two word characters is the word boundary itself -
        `not able` against `notable`, `66 300` against `66300`.
      * A space whose removal makes one number out of two. In front of a sign
        or dash followed by a digit, `1946 - 5 July` becomes `1946 -5 July`, a
        signed number where the source had a range (`_spaces_that_join_a_number`);
        and where the two sides come together into a single number under the
        project's own reading of one, `Note 3. 5` becoming `Note 3.5`
        (`_spaces_that_join_two_numbers`). `propose/pipeline.py` has read
        numbers this way since v0.7.0 (`_cuts_number`: "a dash, a sign or a
        currency mark before it ... a mark between two digits"), so calling
        these a recorder's spacing habit would contradict the project's own
        reading of a number.

    Both shapes are **tampering**, and the two helpers build them into the
    tampering set rather than holding them out of the drift set with a
    footnote. At v0.8.3 they were held out and the candidate's 100% was quoted
    beside a 99.0% that counted them as failures; a pair that must be refused
    belongs on the side where that is asserted, not in an asterisk.
    """
    joins = set(_spaces_that_join_a_number(s)) | set(_spaces_that_join_two_numbers(s))
    return [i for i in _space_positions(s) if i not in joins]


def _space_positions(s: str) -> List[int]:
    """Every space whose neighbours are not both word characters."""
    out = []
    for m in re.finditer(r" ", s):
        i = m.start()
        before = s[i - 1] if i else ""
        after = s[i + 1] if i + 1 < len(s) else ""
        if before.isalnum() and after.isalnum():
            continue
        out.append(i)
    return out


def _spaces_that_join_a_number(s: str) -> List[int]:
    """Spaces whose removal leaves a sign against a digit: `1946 - 5 July`
    becomes `1946 -5 July`, a signed number where the source had a range.
    Dropping one of these is a change of content, so they are built into the
    tampering set."""
    out = []
    for i in _space_positions(s):
        before = s[i - 1] if i else ""
        after = s[i + 1] if i + 1 < len(s) else ""
        if (i + 2 < len(s) and _sign(s[i + 1]) and s[i + 2].isdigit()) or (
                before and _sign(before) and after.isdigit()):
            out.append(i)
    return out


def _joins_two_numbers(s: str, i: int) -> bool:
    """Would dropping the space at `i` change what numbers the text states?

    Two shapes, and the second is the one four rules in a row walked past:

      * a digit on each side - `66 300` becomes `66300`, which is the same
        number set without the separator the source wrote;
      * a digit, a mark, the space, a digit - `Note 3. 5` becomes `Note 3.5`,
        `since 1998, 183` becomes `since 1998,183`, `1000: 16` becomes
        `1000:16`.

    Both are asked of `numbers_stated`, this instrument's own reading, rather
    than of the rule's. Where the answer is the same on both sides - the 297
    dates of `March 11, 2011` among them - the two numbers are still two and
    the pair is drift.
    """
    if i + 1 >= len(s) or not s[i + 1].isdigit():
        return False
    return _changes_the_numbers(s, s[:i] + s[i + 1:])


#: Marks that end a sentence, and the setting a recorder can leave outside one
#: (`he said "yes."`). Written out here rather than imported from the rule being
#: measured, for the same reason as `MAY_BE_DROPPED_BESIDE_A_SPACE` below: a rule
#: that widens its own reading of an edge must fail this instrument, not redefine
#: it.
_SENTENCE_MARKS = ".!?\u2026"
_EDGE_SETTING = " \"',"


def _marks_in_the_closing_run(s: str) -> str:
    """The sentence marks in the run that closes `s`, found through the spacing,
    commas and quotation marks a recorder puts outside them.

    Reading only the last character is what this function replaces: a quote
    ending `staying."` ends with a mark, and calling it markless built 108 pairs
    that were an exchange of marks under a name that said a mark was added.
    """
    j = len(s)
    while j and s[j - 1] in _SENTENCE_MARKS + _EDGE_SETTING:
        j -= 1
    return "".join(c for c in s[j:] if c in _SENTENCE_MARKS)


def _marks_in_the_opening_run(s: str) -> str:
    """The same at the other end."""
    i = 0
    while i < len(s) and s[i] in _SENTENCE_MARKS + _EDGE_SETTING:
        i += 1
    return "".join(c for c in s[:i] if c in _SENTENCE_MARKS)


def _spaces_that_join_two_numbers(s: str) -> List[int]:
    """Every space whose loss makes one number out of two. Read over the whole
    string rather than over `_space_positions`, because that helper drops a
    space standing between two word characters - which is exactly where
    `66 300` lives."""
    return [m.start() for m in re.finditer(" ", s) if _joins_two_numbers(s, m.start())]


def _spaces_after_a_mark_between_numbers(s: str) -> List[int]:
    """Spaces with a mark and a digit behind them and a digit in front, **whose
    loss still leaves two numbers**: `January 25, 1991`, `0 gives 2, 5/3`,
    `Union of India (1996) 1 SCC`.

    History of this docstring, because the mistake it records is the point.
    The v0.8.3 instrument held this shape out of the drift set, saying that
    dropping the space makes one number in five figures out of two. At v0.8.4
    it was moved wholesale into drift on the opposite argument: that all of the
    pairs are dates and citations whose words are still held apart by the mark.

    Both statements were made about a **class that had been read**, and applied
    to a **rule that cannot read**. Counted, the class is 297 dates and 114
    others, and among the others are `since 1998, 183 people` and
    `Note 3. 5 percent`, where the space is the only thing keeping two numbers
    from becoming one. So the side of a pair is decided here by what comes out
    of the edit, not by what the sentence turns out to be about:
    `_spaces_that_join_two_numbers` takes the ones that make a number and
    asserts them as tampering, and what is left is asserted as drift.

    The split costs nothing on the dates: none of the 297 changes what
    `numbers_stated` reads, so all of them stay here, on the side where the
    v0.8.3 rule raised 408 false alarms.
    """
    joins = set(_spaces_that_join_two_numbers(s))
    out = []
    for i in _space_positions(s):
        before = s[i - 1] if i else ""
        after = s[i + 1] if i + 1 < len(s) else ""
        if (i > 1 and before and not before.isspace() and not before.isalnum()
                and s[i - 2].isdigit() and after.isdigit() and i not in joins):
            out.append(i)
    return out


#: The honest side of the `2011 300` convention, copied from the external
#: review of rounds 5-6 rather than retold.
_A_YEAR_AND_A_COUNT = ("In 1998, 183 people died", "In 1998 183 people died")

#: The same letters composed and decomposed (NFC and NFD). `normalize` makes
#: them one text, so with a working `normalize` every pair of this kind is
#: answered by the fast path and held apart below rather than scored; a
#: `normalize` that stops composing sends them to the rule, which refuses
#: them, and the drift threshold fails. Added in round 7 after a mutant of
#: `normalize.view` that dropped NFC passed this instrument.
_COMPOSED_AND_NOT = ("the caf\u00e9 in S\u00e3o Paulo", "the cafe\u0301 in Sa\u0303o Paulo")


def drift_pairs(quote: str) -> List[Pair]:
    out: List[Pair] = []

    def at(kind: str, places: List[int], change: Callable[[str, int], str]) -> None:
        for site, i in _pick3(places).items():
            out.append((kind, site, quote, change(quote, i)))

    at("a comma dropped", _commas_between_words(quote),
       lambda s, i: s[:i] + s[i + 1:])
    at("a comma added", [m.start() for m in re.finditer(r"(?<=[A-Za-z]) (?=[A-Za-z])", quote)],
       lambda s, i: s[:i] + "," + s[i:])
    at("a space too many", [m.start() for m in re.finditer(r" ", quote)],
       lambda s, i: s[:i] + " " + s[i:])
    at("a space too few",
       [i for i in _spaces_touching_a_mark(quote)
        if i not in set(_spaces_after_a_mark_between_numbers(quote))],
       lambda s, i: s[:i] + s[i + 1:])
    at("a space dropped after a mark standing between two numbers",
       _spaces_after_a_mark_between_numbers(quote),
       lambda s, i: s[:i] + s[i + 1:])
    at("another kind of quotation mark", [m.start() for m in re.finditer(r"[\"']", quote)],
       lambda s, i: s[:i] + ("“" if s[i] == '"' else "’") + s[i + 1:])
    at("another kind of dash", [m.start() for m in re.finditer(r"-", quote)],
       lambda s, i: s[:i] + "–" + s[i + 1:])
    at("another case", [m.start() for m in re.finditer(r"[a-z]", quote)],
       lambda s, i: s[:i] + s[i].upper() + s[i + 1:])
    # The other side of the `2011 300` convention, from the same review: the
    # clause comma between a year and a count dropped. The source states two
    # numbers and so does the citation, because a space behind four digits
    # groups nothing - and a rule that reads that space as a grouping comma
    # refuses an honest quote. Planted, because the real quotes carry the
    # shape 0 times; the side is the reading's (`numbers_stated`), checked in
    # `_check_the_reading`.
    for site in ("left edge", "middle", "right edge"):
        out.append(("a clause comma dropped between a year and a count", site,
                    _plant(quote, _A_YEAR_AND_A_COUNT[0], site),
                    _plant(quote, _A_YEAR_AND_A_COUNT[1], site)))
        out.append(("the same letters composed another way", site,
                    _plant(quote, _COMPOSED_AND_NOT[0], site),
                    _plant(quote, _COMPOSED_AND_NOT[1], site)))

    # A closing mark is terminal by nature: there is one site, not three. The
    # kind has to be what its name says (project §37): adding "." to a quote
    # that already ends in "." is a doubled mark, not a full stop added, and it
    # is listed under its own name below.
    tail = quote.rstrip()
    if tail and tail[-1] not in ".!?\u2026":
        out.append(("the closing full stop added", "right edge", quote, quote + "."))
    elif tail:
        out.append(("the closing mark doubled", "right edge", quote, tail + tail[-1]))
        out.append(("the closing full stop dropped", "right edge", quote, tail[:-1]))
    return out


# --------------------------------------------------------------------------- #
# the whole row: every mark that may differ, at both edges, against a digit
# --------------------------------------------------------------------------- #

#: What the rule under test says may differ, written out here and not imported
#: (same reason as `MAY_BE_DROPPED_BESIDE_A_SPACE`): the space, the comma, the
#: two quotation marks, and the four marks that end a sentence, which are
#: setting only in the run that opens or closes the quote.
MAY_DIFFER = [
    (" ", "a space"),
    (",", "a comma"),
    ('"', "a double quotation mark"),
    ("'", "a single quotation mark"),
    (".", "a full stop"),
    ("!", "an exclamation mark"),
    ("?", "a question mark"),
    ("\u2026", "an ellipsis"),
]

#: The numbers planted at the edge. Two of them, because the reading answers
#: differently about the same mark depending on what follows it: a comma in
#: front of three digits is a grouping comma with its head cut off, a comma in
#: front of one digit is a mark with no reading at all in that position.
_PLANTED_NUMBERS = ["500", "5"]


def marks_against_a_digit(quote: str) -> List[Pair]:
    """The question the round-3 review's hole was found in, asked of every mark
    on the allow-list instead of the one that was caught.

    For each mark, each edge and each direction - taken away, or put there -
    the pair is built with the mark standing directly against a digit, and
    `_form_of_the_result` says what happened to the numbers. A pair whose
    numbers change is tampering under a name that says which form it took. A
    pair whose numbers do not change is drift, with one exception named in the
    code below and nothing held out of either count.
    """
    out: List[Pair] = []
    for mark, mark_name in MAY_DIFFER:
        for number in _PLANTED_NUMBERS:
            for edge in ("left edge", "right edge"):
                if edge == "left edge":
                    without, with_mark = f"{number} {quote}", f"{mark}{number} {quote}"
                else:
                    without, with_mark = f"{quote} {number}", f"{quote} {number}{mark}"
                for direction, source, cited in (
                        ("dropped", with_mark, without),
                        ("added", without, with_mark)):
                    form = _form_of_the_result(source, cited)
                    if form != "the same numbers":
                        out.append((f"{mark_name} against a digit {direction}"
                                    f" at the {edge.split()[0]} edge"
                                    f", and {form}", edge, source, cited))
                    elif direction == "added" and mark in "!?":
                        # The numbers are the same and the citation still asks
                        # something the source does not. This instrument
                        # already asserts that under `an opening mark added
                        # where the span began without one`; the sweep does not
                        # contradict its own assertion, and does not count the
                        # pair twice either.
                        continue
                    else:
                        out.append((f"{mark_name} against a digit {direction}"
                                    f" at the {edge.split()[0]} edge"
                                    f", and the numbers are the same",
                                    edge, source, cited))
    return out


def the_row(quote: str) -> List[Tuple[str, str, str, str, str]]:
    """The same sweep, as a census: (mark, edge, direction, form, side). One row
    per shape, so the table can be printed whatever the rule answers."""
    rows = []
    for kind, _site, source, cited in marks_against_a_digit(quote):
        head, _, form = kind.partition(", and ")
        side = "tampering" if form != "the numbers are the same" else "drift"
        rows.append((head, form, side, source, cited))
    return rows


# --------------------------------------------------------------------------- #
# the boundary of a span, read in the source it was cut from
# --------------------------------------------------------------------------- #

#: (the separator, what to call it). Every way this project has ever said one
#: number can be written with a mark inside it, plus the case where there is no
#: mark at all - a boundary drawn between two digits. The space is here because
#: `docs/design.md` and `tokens._groups_a_number` both call it the same
#: separator as the comma; the colon because `10:30` is read as one number by
#: `cuts_number` and by the walk above; nothing because `66300` cited from its
#: fourth character is the same forgery with no mark to notice.
SPAN_SEPARATORS = [
    (" ", "a space"),
    (",", "a comma"),
    (".", "a full stop"),
    (":", "a colon"),
    ("", "nothing at all"),
    # Round 7: the separators the rule's reading did not know and a reader
    # does. A no-break and a narrow no-break space group a number exactly as
    # a space does, and `66 300 people` with either one, cited from its
    # `300`, closed the books at fee1dd7. A slash writes a fraction, which the
    # docstring of `tokens.cuts_number` promised and its code did not keep;
    # the Arabic thousands separator is punctuation like the comma. Every
    # other whitespace and punctuation character is asked once by
    # `number_sweep`.
    ("\u00a0", "a no-break space"),
    ("\u202f", "a narrow no-break space"),
    ("/", "a slash"),
    ("\u066c", "the Arabic thousands separator"),
]

#: The answer the span pairs are built around, written out rather than
#: computed, so that changing it is a visible change to a literal: a number of
#: six figures, set five ways, with the span declared to begin or end inside it.
_A_NUMBER_IN_THE_SOURCE = ("66", "300")

SpanPair = Tuple[str, str, str, int, int, str]   # kind, side, source, start, end, cited


def span_shapes(quote: str) -> List[Tuple[str, str, int, int, str]]:
    """(kind, source, start, end, what the recorder wrote) for every shape of
    span boundary this round is about, forged and honest together.

    Nothing here says which side a shape is on. `span_pairs` asks
    `span_cuts_a_number`, and a shape whose boundary turns out to fall between
    numbers rather than through one is asserted as drift - which is what makes
    the honest shapes below a price list rather than a decoration: an
    over-broad boundary check fails on them.

    The forged shapes are two per separator and edge, because a forger has two
    roads to the same citation and only one of them carries a mark:

      * **the span carries the separator and the citation drops it** -
        `,300 people affected` cited as `300 people affected`. This is what
        rounds 3 and 4 closed, for the full stop and then the comma, inside
        `says_the_same`.
      * **the span begins after the separator and the citation is word for
        word** - the same `300 people affected`, with the span declared one
        character to the right. `verify_entry` answers this one with its
        equality fast path, before any rule is consulted, and the review of
        round 4 named it: a forger who does not need to carry a mark will not
        carry one.
    """
    head, tail = _A_NUMBER_IN_THE_SOURCE
    out: List[Tuple[str, str, int, int, str]] = []
    for sep, sep_name in SPAN_SEPARATORS:
        number = f"{head}{sep}{tail}"
        before = f"{number} {quote}"
        after = f"{quote} {number}"
        cut = len(head)                      # where the separator stands
        if sep:
            out.append((f"the head of the span is inside a number set with {sep_name},"
                        f" the span carrying the separator and the citation dropping it",
                        before, cut, len(before), before[cut + len(sep):]))
        out.append((f"the head of the span is inside a number set with {sep_name},"
                    f" the citation word for word",
                    before, cut + len(sep), len(before), before[cut + len(sep):]))
        keeps = len(after) - len(tail)        # the span stops before the last group
        if sep:
            out.append((f"the tail of the span is inside a number set with {sep_name},"
                        f" the span carrying the separator and the citation dropping it",
                        after, 0, keeps, after[:keeps - len(sep)]))
        out.append((f"the tail of the span is inside a number set with {sep_name},"
                    f" the citation word for word",
                    after, 0, keeps - len(sep), after[:keeps - len(sep)]))

    # The honest shapes. Each one puts a boundary where a careless check would
    # fire and a correct one must not, and the citation is the span inside
    # quotation marks so that the rule is asked rather than the fast path: a
    # recorder quoting a sentence is the reason the two marks are on the
    # allow-list at all.
    for kind, source, start, end in (
            ("the span begins after a grouped number",
             f"66,300 {quote}", 7, None),
            ("the span begins at a grouped number whole",
             f"over 66,300 {quote}", 5, None),
            ("the span ends before a grouped number",
             f"{quote} 66,300", 0, len(quote)),
            ("the span begins at the second of two numbers a clause comma divides",
             f"since 1998, 300 {quote}", len("since 1998, "), None),
            ("the span begins at the year of a date",
             f"March 11, 2011 {quote}", len("March 11, "), None),
            ("the span begins at a number a sentence begins with",
             f"That was agreed. 300 {quote}", len("That was agreed. "), None),
            ("the span ends at a number its own sentence ends after",
             f"{quote} 300. And more was written.", 0, len(quote) + 4),
            ("the span ends at a number a clause comma follows",
             f"{quote} 300, and more was written.", 0, len(quote) + 4),
            ("the span ends at a number the next sentence begins with a number after",
             f"{quote} 300. 500 more were counted.", 0, len(quote) + 4),
            # Round 7: the convention both ways. A space behind four digits
            # groups nothing, so a count after a year is its own number, and
            # a span beginning at it or ending at the year cuts none.
            ("the span begins at a count a year stands before",
             f"in 2011 300 {quote}", len("in 2011 "), None),
            ("the span begins at a count a year stands before, a no-break space",
             f"in 2011\u00a0300 {quote}", len("in 2011\u00a0"), None),
            ("the span ends at a year a count stands after",
             f"{quote} in 2011 300 people", 0, len(quote) + len(" in 2011"))):
        if end is None:
            end = len(source)
        out.append((kind, source, start, end, '"' + source[start:end] + '"'))
    return out


def number_sweep() -> List[Tuple[str, str, int, int, str]]:
    """Every character that can stand inside a numeral, asked once each.

    `SPAN_SEPARATORS` names nine. This walks the classes the round-7 brief asks
    for by property: every whitespace character, every punctuation character
    (`P*`), every invisible one (`Cf`), and every numeral that is not a decimal
    digit (`N*` without `Nd`: `½`, `²`, `Ⅻ`). For each, the span is declared
    just inside the number and the citation is word for word; for whitespace
    and punctuation also the span carrying the character; and on the honest
    side the same character where it cuts nothing - a whitespace behind four
    digits, and a punctuation mark with a space after it.
    """
    out: List[Tuple[str, str, int, int, str]] = []
    tail = " of the people were counted."
    for cp in range(0x110000):
        ch = chr(cp)
        if ch.isspace():
            source = f"about 66{ch}300{tail}"
            out.append(("the sweep: a whitespace grouping a number, the span on it",
                        source, 8, len(source), source[9:]))
            out.append(("the sweep: a whitespace grouping a number, the span after it",
                        source, 9, len(source), source[9:]))
            honest = f"in 2011{ch}300{tail}"
            out.append(("the sweep: a whitespace behind a year, the span after it",
                        honest, 8, len(honest), '"' + honest[8:] + '"'))
        elif _punctuation(ch):
            source = f"about 3{ch}4{tail}"
            out.append(("the sweep: punctuation between two digits, the span on it",
                        source, 7, len(source), source[8:]))
            out.append(("the sweep: punctuation between two digits, the span after it",
                        source, 8, len(source), source[8:]))
            honest = f"about 3{ch} 4{tail}"
            out.append(("the sweep: punctuation and a space between two digits, the span "
                        "after them", honest, 9, len(honest), '"' + honest[9:] + '"'))
        elif _invisible(ch):
            source = f"about 66{ch}300{tail}"
            out.append(("the sweep: an invisible character inside a number, the span after it",
                        source, 9, len(source), source[9:]))
        elif _numeral(ch) and unicodedata.category(ch) != "Nd":
            source = f"about 1{ch}{tail}"
            out.append(("the sweep: a numeral that is not a digit, the span on it",
                        source, 7, len(source), source[7:]))
    return out


# --------------------------------------------------------------------------- #
# what stands against a numeral in real text, and whether it is the number's
# --------------------------------------------------------------------------- #

# The external review of round 7 found the classes above independent of the
# rule only in their code. Asked whether a character against a digit belongs
# to the number, they asked Unicode - `_punctuation`, `_numeral` - exactly the
# question the rule asks, and got the same answer. So every forgery the rule
# let through was an honest span to this instrument too: `1∕2` cited from its
# `2` (DIVISION SLASH is `Sm`, not `P*`), `1.296000×10¹⁵` cited as `1.296000`,
# `≤5%` as `5%`, `US$1.183` as `$1.183`. The instrument could not fail a rule
# for a mistake it shared.
#
# The truth below is taken from somewhere else: from the texts. `real_sources`
# reads every source of the corpus, and `marks_in_the_corpus` lists every
# character that stands directly against a numeral there - before one, between
# two, after one. Each of those characters is decided here **by hand**, one line
# per group with the reason beside it, and not by its category: `^` and `` ` ``
# are the same category and only one of them is part of `x^2`; `%` is the same
# category as `#` and only one of them changes what `5` says. A character the
# corpus puts against a numeral that this table has not decided stops the run
# (`_check_the_marks_are_decided`): the instrument does not get to skip a
# character real text contains.
#
# The reading of the table is one sentence: **a mark against a number that
# changes what the number states is part of it** - a sign, a currency, an
# operator of comparison or approximation, a power, a share, a unit glued to it
# the way `km` is glued to `10km` (round 7, E1) - **and a mark that frames it,
# ends a sentence after it or is markup around it is not.** A span whose
# boundary falls between a number and a mark that is part of it states another
# number, and it is tampering; a span whose boundary falls between a number and
# a mark that is not is honest, and a rule that refuses it is over-broad. Both
# directions are scored, so a rule that reads a number too widely fails here as
# surely as one that reads it too narrowly.
#
# One limit is the view's, not the table's. `normalize` folds `′` into `'`, `″`
# into `"` and `−` into `-` before the comparison and the boundary read
# anything, so after a number a prime is a quotation mark and a minus is a
# hyphen to the gate. The table decides them as the gate can see them, and
# `NAMED_NOT_SCORED` prints what that costs (`4″` cited as `4`).

PART, APART = "part", "apart"

#: Where a span boundary goes in the templates below.
_BOUNDARY = "‸"

#: (characters, before a numeral, between two, after one, why). "before" is the
#: mark with a space in front of it and a numeral after (`costs ~500`),
#: "between" is a numeral on both sides (`3∕4`), "after" is a numeral in front
#: and a space after (`500% more`). Written by hand; nothing here is computed
#: from a category.
MARKS_BESIDE_A_NUMERAL: List[Tuple[str, str, str, str, str]] = [
    (".,", PART, PART, APART,
     "the decimal point and the grouping comma. With a space in front and a digit "
     "after (` .5% of assets`, ` ,500 people`) they are the head of a number the "
     "span does not carry; between two digits they are inside one (`13.7`, "
     "`7,000` - and `3,4,7`, where the comma cannot be told from a group, the "
     "owner's decision of round 9); after the last digit they end a sentence or "
     "a clause (`April 2018.`). The corpus examples in front of a digit have a "
     "letter before the mark (`Physics.11`); a head with a space before it is "
     "the template's shape and has no instance there"),
    (":", APART, PART, APART,
     "a colon in front of a number labels it and leaves it whole "
     "(`arXiv:1804.01620`, `167(1):7-41`, and the value of compact JSON, "
     "`\"count\":42`, round 9); between two digits it is a time (`2:41`); after "
     "one it introduces what follows (`Step 1:`)"),
    ("-‐‑‒–—―−\uff0d\ufe63", PART, PART, APART,
     "a dash in front of a number is its sign (`· –57.1`), and a minus sign is a "
     "dash to the gate once `normalize` has folded it, and the fullwidth and "
     "small hyphen-minus are dashes it does not fold (the review's mutant Y10); "
     "between two digits it "
     "joins a range or a date into one token (`2021-12-16`, `2022–2032`); after "
     "one it joins a compound (`500-entry`, `2003–February`) and leaves the "
     "number whole"),
    ("+±∓＋", PART, PART, PART,
     "a plus or plus-minus sign: the sign in front (`±1`), `or more` after "
     "(`200+ programs`), arithmetic between (`38+19+3`)"),
    ("$€£¥₹¢", PART, PART, PART,
     "a currency says what the number counts, in front or after (`€120,000`, "
     "`£1 million`). `$` is also TeX's delimiter in the corpus (`$000$ to "
     "$999$`), and nothing in the text tells the two apart, so it is read as "
     "the currency (`The answer is $42$` is named, not scored)"),
    ("%‰‱٪％﹪", APART, PART, PART,
     "a share sign after a number is what it measures (`40% of languages`); in "
     "front of a digit it is URL encoding (`Celebrate%20linguistic`) and states "
     "nothing about the number"),
    ("~≈≤≥<>≠∼≡", PART, PART, PART,
     "an operator of comparison or approximation in front of a number changes "
     "the claim (`≤5% dehydration`, `~3 days`, `≈9400`, `>145 mmol/L`, `B≠0`); "
     "between two it makes one token (`WAYO-024~5`); glued after one it is read "
     "with it, as the owner's decision of round 8 reads it a space away "
     "(`1.296 > 10`). The one instance after a number in the corpus is a ket, "
     "`|00>+ 2i|01>`, which no reading of the characters tells from `5>3`: it "
     "is named, not scored"),
    ("=→", APART, APART, APART,
     "an equals sign and an arrow relate two values and change neither: the "
     "value of a parameter (`id=506170199519639`, `section=1`), an equation "
     "(`10^7=3.78`), a transition (`J=0→1`). Round 8 read them as part of the "
     "number and refused 502 of 535 real `key=value` values of tool output "
     "(round 9)"),
    ("×÷√∑∘⁻⁺∕⁄^", PART, PART, PART,
     "a power, a root, a product, a sum, a fraction written with a slash of "
     "mathematics (`1.296000×10¹⁵`, `10⁻⁹`, `mv^2`, `4√3`, `±1∕2`, `+∑1`, "
     "`Ba²⁺`, `90∘`): what the number is depends on it"),
    ("°◦", APART, PART, PART,
     "a degree after a number is its unit (`30°`, `60◦` a scrape set for it); "
     "between two digits it is inside a coordinate (`38°53`); the corpus puts "
     "none in front of a number, and one there would label it rather than "
     "change it"),
    ("©", APART, PART, PART,
     "a copyright mark labels a year and leaves it whole (`©2015 The "
     "Hollywood`, round 9). Glued between or after digits it has no instance in "
     "the corpus and is read as any symbol glued to a number is"),
    ("′″", APART, PART, APART,
     "a prime: between two digits inside a coordinate (`38°53′42″N`); after one "
     "it is a quotation mark to the gate (see above)"),
    ("·", APART, PART, APART,
     "a middle dot between two digits is a product (`2.5·107 kWh`); anywhere "
     "else a separator"),
    ("•", APART, APART, APART,
     "a bullet separates two things, never two parts of one number: `James "
     "Bond 007•398K views` is a title and a count (round 9; round 8 read it as "
     "one token)"),
    ("()[]{}⟨⟩⌈⌉", APART, PART, APART,
     "a bracket encloses a number and leaves it whole (`(2018)`, `(25%)`); "
     "between two digits the whole run is one token (`18(1)`, `13-14(4)8`)"),
    ("\"'“”‘’«»", APART, PART, APART,
     "a quotation mark frames a number (`\"2019 game`, `“1965”`), an "
     "apostrophe elides a century (`'79`, `SODA’06`); between two digits it "
     "groups or marks a coordinate (`38°53'42.4`)"),
    ("*_#\\&@", APART, PART, APART,
     "markup and escapes: bold (`**2022**`), an identifier (`CHEM_114`), a "
     "number sign (`#14`), an escape, a query string (`=26&profile`), an "
     "address (`legallock36@gmail`). Between two digits they make one token "
     "(`4*3`, `1_5sBJ`)"),
    ("/;?!…§‖", APART, PART, APART,
     "a path, a clause, a sentence, a section (`uploads/2020/10`, `§1`, `6!`): "
     "around a number they end or introduce something else; between two digits "
     "they are one token (`2020/10`, the owner's decision 2 of round 7)"),
    ("|", APART, APART, APART,
     "a table rule (the owner, round 8): `| 5 | 6 |` holds two values, never a "
     "number with the rule in it"),
    ("`", APART, PART, APART,
     "a code span marks a number off (`` `115` ``) and leaves it whole; between "
     "two digits it is one token, as the rule has read it since v0.7.0"),
]


#: One cell of the table above, as the corpus writes it: the first place in
#: the posted sources where the character stands in that position against a
#: numeral, copied out (at most 18 characters either side, within its line).
#: A cell without an entry has no instance in the corpus, and its decision is
#: the group's reason alone. `_check_the_examples_are_real` stops the run if
#: an example is not in the sources the instrument reads.
REAL_EXAMPLES: Dict[Tuple[str, str], str] = {
    ('!', 'after'): 't Festival in 2018! A unique ... Prod',
    ('"', 'after'): 'C. are: 38\xb053\'42.4"N, 77\xb02\'10.93"W.',
    ('"', 'before'): 'est refers to the "2019 game that won',
    ('#', 'after'): 'EntityPage/Q465702#sitelinks-wikipedi',
    ('#', 'before'): '1. [2025 AIME I #14](https://www.yo',
    ('$', 'after'): 'ntagon with $AB=14$, $BC=7$, $CD=24 .',
    ('$', 'before'): 'to the problem is $38+19+3=\\boxed{060',
    ('$', 'between'): '1917$157,901,466',
    ('%', 'after'): 'ns that roughly 40% of languages are ',
    ('%', 'before'): 'earch?q=linguistic%20diversity&journa',
    ('%', 'between'): 'ry_(LibreTexts)/02%3A_Measurement_and',
    ('&', 'after'): 'id=506170199519639&ev=PageView&noscri',
    ("'", 'after'): 'olumbia is: 38\xb0 54\' 25" N / 77\xb0 2\' 12',
    ("'", 'before'): "ohnson, Morehouse '79, Named Uber Saf",
    ("'", 'between'): 'n, D.C. are: 38\xb053\'42.4"N, 77\xb02\'10.93',
    ('(', 'after'): 'peratures of H2SO4(aq) and NaOH(aq), ',
    ('(', 'before'): ' itertools.product(02, repeat=length-',
    ('(', 'between'): '15H28/c1-6-9-11-15(5,12-10-7-2)13-14(',
    (')', 'after'): '2, repeat=length-2):',
    (')', 'before'): ' blocked: (R_B/R_A)\xb2 = 0.25 (25%)',
    (')', 'between'): '-11-15(5,12-10-7-2)13-14(4)8- ...',
    ('*', 'after'): 'ar is **April 2018**, as listed at th',
    ('*', 'before'): '**1. GIVEN OR VERIFI',
    ('*', 'between'): ' by {rel_err_beta1*100:.3f}% (beta = ',
    ('+', 'after'): 'e+February+29+2004+edit+history+joke+',
    ('+', 'before'): 'agon+page+February+29+2004+edit+histo',
    ('+', 'between'): 'the problem is $38+19+3=\\boxed{060} .',
    (',', 'after'): 'ertools.product(02, repeat=length-2):',
    (',', 'before'): 'this figure \u03a9 \u2261 \u03a9m,0).',
    (',', 'between'): 'ch as \u201cMore than 7,000 languages have',
    ('-', 'after'): 'ng on multiple 500-entry history page',
    ('-', 'before'): '(02, repeat=length-2):',
    ('-', 'between'): 'Date range to 2021-12-16 to 2021-12-1',
    ('.', 'after'): '**1. GIVEN OR VERIFIED',
    ('.', 'before'): 'oi/10.1103/Physics.11.118)',
    ('.', 'between'): '+ + OH\u2013 \u2192 H2O + 13.7 kcal**',
    ('/', 'after'): 'nt/uploads/2020/10/Enthalpy-of-Neutra',
    ('/', 'before'): 'wp-content/uploads/2020/10/Enthalpy-o',
    ('/', 'between'): 'ntent/uploads/2020/10/Enthalpy-of-Neu',
    (':', 'after'): 'bash: line 17: warning: here-doc',
    (':', 'before'): '4. [[PDF] arXiv:1804.01620v1 [stat',
    (':', 'between'): '0:00 Intro ; 2:41 3D',
    (';', 'after'): '= 8 , CD=6 C D = 6; \u2220B=\u2220D=90\u2218 \u2220 B ...',
    (';', 'before'): 'White;5876',
    ('<', 'before'): '    <135 mmol/L or >145',
    ('=', 'after'): 'roblem is $38+19+3=\\boxed{060} ...',
    ('=', 'before'): '=nature&date_range=2021-12-16',
    ('=', 'between'): 'mes 6.3\\times 10^7=3.78\\times 10^7\\,m',
    ('>', 'after'): 'e have H|\u03a8n1,n2,n3> = (nx + ny + nz',
    ('>', 'before'): 'For children with >5% dehydration, re',
    ('?', 'after'): 'org/pdf/2508.13167?)',
    ('?', 'before'): "mbl.cc/favicon.ico?1577284053', 'subp",
    ('@', 'after'): '* [legallock36@gmail.com](http://',
    ('[', 'after'): '{n} ... Problem 14[edit]. Let $ABCDE$',
    ('[', 'before'): '0. [2.9: Density - Che',
    ('[', 'between'): 'sity Press. p.\\xa0[247](https://books',
    ('\\', 'after'): 't\\_{2}) \\times 4.2\\end{array} \\)',
    ('\\', 'before'): '94; |\\n| CSS | \\\\\\\\1242A |\\n| Javascr',
    (']', 'after'): 'roblems/Problem 14](https://artofprob',
    ('^', 'after'): 'times 1.67\\times10^{-27} \\times10^{7}',
    ('^', 'before'): 'y}{l}F\\_c=\\frac{mv^2}{r}\\end{array} \\',
    ('^', 'between'): 'times 6.3\\times 10^7=3.78\\times 10^7\\',
    ('_', 'after'): 'ibreText_Gob/Ch100_FundmGoB_LibreText',
    ('_', 'before'): '_Anza_College/CHEM_10%3A_Introduction',
    ('_', 'between'): 'tube.com/watch?v=1_5sBJDVi1I)',
    ('`', 'after'): 'produced each 1e-5` in the atmosphere',
    ('`', 'before'): ": `AGG` - Output: `115`  Let's break ",
    ('{', 'before'): 'mes W) \\times (t\\_{1}-t\\_{2}) \\times ',
    ('|', 'after'): '}+\\binom{22+735235|\\lfloor x \\rfloor ',
    ('|', 'before'): 'r example, |W\u27e9 = (|001\u27e9+|010\u27e9+|100\u27e9)/',
    ('|', 'between'): '* (|00>+ 2i|01>\u2212 3|10>\u2212 4i|11>)',
    ('}', 'after'): 's W) \\times (t\\_{1}-t\\_{2}) \\times 4.',
    ('~', 'before'): 'widths, which are ~11% of the central',
    ('~', 'between'): 'c Suite | WAYO-024~5](https://vgmdb.n',
    ('\xa3', 'before'): ' fund totalled at \xa31 million, with ev',
    ('\xa7', 'before'): ' "Pub. L. 93\u2013595, \xa71, Jan. 2, 1975, 8',
    ('\xa9', 'before'): "'ve lost in 2015. \xa92015 The Hollywood",
    ('\xb0', 'after'): 'inclination i = 30\xb0',
    ('\xb0', 'between'): 'nited States at 38\xb053\u203242\u2033N 77\xb002\u203211\u2033W',
    ('\xb1', 'before'): 'y variables xi \u2208 {\xb11}.',
    ('\xb7', 'between'): ' mass is about 2.5\xb7107 kWh.',
    ('\xd7', 'between'): '- Star A: 1.296000\xd710\xb9\u2075 (arbitrary un',
    ('\u2010', 'before'): 't is that the DEPT\u2010135 spectrum of Pr',
    ('\u2013', 'after'): ' (January 24, 2003\u2013February 1, 2005) ',
    ('\u2013', 'before'): ' NaCl(aq) + H2O \xb7 \u201357.1 ; 2. KOH(aq) ',
    ('\u2013', 'between'): 'us Languages (2022\u20132032) and mentions',
    ('\u2014', 'after'): 'nt edit as of 2022\u2014confirmation that ',
    ('\u2014', 'before'): 'Committee on Rules\u20141987 Amendment" [r',
    ('\u2016', 'after'): 'nces, J=n\u2211i=1n\u2211j=1\u2016pi\u2212pj\u2016a,. is maxim',
    ('\u2018', 'before'): 'th M, et al 2015, \u2018140 mmol/L of sodi',
    ('\u2019', 'after'): 'outside Article 18\u2019s prohibition. The',
    ('\u2019', 'before'): ' preliminary\\nSODA\u201906 version). They ',
    ('\u201c', 'before'): 'CK-12 license and \u201c14.1 Bronsted Lowr',
    ('\u201d', 'after'): ' Artist Award 2022\u201d and open relevant',
    ('\u2022', 'between'): 're. James Bond 007\u2022398K views \xb7 26:09',
    ('\u2026', 'after'): ' Boltstone and 2,0\u2026Found',
    ('\u2032', 'after'): 'Coordinates: 72\xb000\u2032N 40\xb000\u2032WFrom Wiki',
    ('\u2032', 'between'): 'ed States at 38\xb053\u203242\u2033N 77\xb002\u203211\u2033W / ',
    ('\u2033', 'after'): 'States at 38\xb053\u203242\u2033N 77\xb002\u203211\u2033W / 38.',
    ('\u207a', 'after'): 'ssociates into Ba\xb2\u207a and 2 OH\u207b ions.',
    ('\u207b', 'before'): 'is \u0394E\u0303 \u2248 4.6358 cm\u207b\xb9, and the require',
    ('\u207b', 'between'): 'th lifetimes of 10\u207b\u2079 sec and 10\u207b\u2078 sec',
    ('\u20ac', 'before'): 'rdered him to pay \u20ac120,000 in compens',
    ('\u2192', 'between'): '2 \xc5; thus \u0394E_rot(0\u21921) ~ 2B\u0303 ~ few cm^',
    ('\u2211', 'before'): '| +\u22111   +\u22111      +\u22111 Z',
    ('\u2212', 'after'): '2\u2212',
    ('\u2212', 'before'): '1] as g(n,p)= (n+p\u22121)! n!(p\u22121)! The g',
    ('\u2212', 'between'): 'length region 3600\u221210 500 \xc5 at a spec',
    ('\u2215', 'before'): '(lecture1827x.png)\u22152 where ![\u20d7\u03c3](lect',
    ('\u2215', 'between'): '\u27e8Sz\u27e9 = \xb11\u22152. ![\u20d7S](lecture18',
    ('\u2218', 'after'): ' C D = 6; \u2220B=\u2220D=90\u2218 \u2220 B ...',
    ('\u221a', 'before'): 'e domain R = Z+Z. \u221a5 is not integrall',
    ('\u221a', 'between'): 'side length 2 is 4\u221a3, and assuming th',
    ('\u2248', 'before'): '-DNA interaction (\u22489400) has been per',
    ('\u2260', 'before'): 'eneral case with B\u22600. Notice that \u03bb-\u2215',
    ('\u2264', 'before'): 'For children with \u22645% dehydration, re',
    ('\u2265', 'before'): '  | \u226560 | 100 | 67 |',
    ('\u2308', 'before'): ' \u03bb+ +  lim   --ln \u23081 +  ---   \u2309',
    ('\u25e6', 'after'): ', and \u2220B = \u2220E = 60\u25e6 . For each point ',
    ('\u27e9', 'after'): 'ample, |W\u27e9 = (|001\u27e9+|010\u27e9+|100\u27e9)/ \u221a 3',
}


def _decision(ch: str, where: str) -> str:
    for chars, before, between, after, _why in MARKS_BESIDE_A_NUMERAL:
        if ch in chars:
            return {"before": before, "between": between, "after": after}[where]
    return ""


def _binary(text: str) -> bool:
    """A text a scrape read out of a binary file: the replacement character, or
    control characters other than the three a text has. Seven of the 2325
    sources of the corpus; what stands against a digit there is not writing."""
    return "\ufffd" in text or any(
        unicodedata.category(c) in ("Cc", "Co", "Cn", "Cs") and c not in "\t\n\r"
        for c in text)


def real_sources(posted_dirs) -> List[str]:
    """Every distinct source text in the posted files - the artifacts the
    quotes were cut out of, not the quotes."""
    out = set()
    for d in posted_dirs:
        for path in sorted(glob.glob(os.path.join(d, "posted", "*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            for art in doc.get("artifacts", []):
                if isinstance(art.get("content"), str):
                    out.add(art["content"])
    return sorted(out)


def marks_in_the_corpus(texts: List[str]) -> Dict[Tuple[str, str], int]:
    """(character, where) -> how many places, for every character that is not
    a letter, a numeral, whitespace or invisible and stands directly against a
    numeral in a text that is not binary."""
    found: Dict[Tuple[str, str], int] = {}
    for text in texts:
        if _binary(text):
            continue
        n = len(text)
        for i, ch in enumerate(text):
            if _numeral(ch) or _letter(ch) or ch.isspace() or _invisible(ch):
                continue
            left = i > 0 and _numeral(text[i - 1])
            right = i + 1 < n and _numeral(text[i + 1])
            where = ("between" if left and right else "before" if right
                     else "after" if left else "")
            if where:
                found[(ch, where)] = found.get((ch, where), 0) + 1
    return found


def _check_the_marks_are_decided(found: Dict[Tuple[str, str], int]) -> None:
    """Stops the run if the corpus puts a character against a numeral that the
    table has not decided."""
    missing = sorted((ch, where, n) for (ch, where), n in found.items()
                     if not _decision(ch, where))
    if missing:
        for ch, where, n in missing:
            print(f"the corpus has {ch!r} U+{ord(ch):04X} {where} a numeral at {n} "
                  f"places and MARKS_BESIDE_A_NUMERAL does not decide it",
                  file=sys.stderr)
        raise SystemExit(2)


def _check_the_examples_are_real(texts: List[str]) -> int:
    """Stops the run if an example in `REAL_EXAMPLES` is not in the sources, or
    does not show its character in its place against a numeral. Returns how
    many were checked; 0 without the corpus on disk."""
    if not texts:
        return 0
    wrong = []
    for (ch, where), example in REAL_EXAMPLES.items():
        if not any(example in t for t in texts):
            wrong.append((ch, where, "not in any source"))
            continue
        shown = False
        for i, c in enumerate(example):
            if c != ch:
                continue
            left = i > 0 and _numeral(example[i - 1])
            right = i + 1 < len(example) and _numeral(example[i + 1])
            if where == ("between" if left and right else "before" if right
                         else "after" if left else ""):
                shown = True
        if not shown:
            wrong.append((ch, where, "does not show it there"))
    for ch, where, why in wrong:
        print(f"REAL_EXAMPLES[{ch!r}, {where!r}] {why}", file=sys.stderr)
    if wrong:
        raise SystemExit(2)
    return len(REAL_EXAMPLES)


def _at(template: str) -> Tuple[str, int]:
    """A source with `‸` (CARET) where the span boundary goes, and the
    boundary. Not `|`: a table row and the vertical line itself are material."""
    i = template.index(_BOUNDARY)
    return template[:i] + template[i + 1:], i


def mark_shapes() -> List[Tuple[str, str, str, int, int, str]]:
    """(kind, side, source, start, end, citation) for every mark the table
    decides, in each of its three places, the citation word for word. The side
    is the table's; nothing else is asked."""
    out: List[Tuple[str, str, str, int, int, str]] = []
    for chars, before, between, after, _why in MARKS_BESIDE_A_NUMERAL:
        for ch in chars:
            name = f"{ch!r} U+{ord(ch):04X}"
            side = {PART: "tampering", APART: "drift"}
            src, a = _at(f"it costs {ch}{_BOUNDARY}500 per unit today.")
            out.append((f"a mark against a numeral: {name} before it, the span "
                        f"after the mark", side[before], src, a, len(src), src[a:]))
            src, b = _at(f"it costs 500{_BOUNDARY}{ch} per unit today.")
            out.append((f"a mark against a numeral: {name} after it, the span "
                        f"before the mark", side[after], src, 0, b, src[:b]))
            src, a = _at(f"it costs 3{ch}{_BOUNDARY}4 per unit today.")
            out.append((f"a mark against a numeral: {name} between two, the span "
                        f"after the mark", side[between], src, a, len(src), src[a:]))
            src, b = _at(f"it costs 3{_BOUNDARY}{ch}4 per unit today.")
            out.append((f"a mark against a numeral: {name} between two, the span "
                        f"before the mark", side[between], src, 0, b, src[:b]))
    for kind, side, template in MARKS_APART_FROM_A_NUMERAL:
        if template.count(_BOUNDARY) == 2:
            # both boundaries given: the span is what stands between them
            src, i = _at(template)
            src, j = _at(src)
            out.append((kind, side, src, i, j, src[i:j]))
            continue
        src, i = _at(template)
        if kind.startswith("the tail"):
            out.append((kind, side, src, 0, i, src[:i]))
        else:
            out.append((kind, side, src, i, len(src), src[i:]))
    return out


#: The same question where a space stands between the number and its mark, and
#: the few shapes a mark against a numeral cannot show. Every line is a decision
#: with its source: the owner's of round 8 (a sign or an operator standing apart
#: in the middle of a line, a share or currency sign after a space, an operator
#: with a space on both sides between two numbers, and `|` in none of them),
#: the review of round 7 (`US$`, the bracket pair, the cluster), and one the
#: view makes. `‸` is the boundary. Kinds that begin "the tail" are spans that
#: end at `|`; every other span begins there, and two of them mark both ends.
MARKS_APART_FROM_A_NUMERAL: List[Tuple[str, str, str]] = [
    *[(f"a sign {s!r} standing apart in the middle of a line, the span after it",
       "tampering", f"Operating margin {s} ‸5.2 % lower than last year.")
      for s in "-+\u00b1≤≥<>≈~×\u2212\u2013\u2014"],
    *[(f"a sign {s!r} opening a line: a list marker, not a sign", "drift",
       f"The monsters are:\n{s} ‸8 Orcs and more.")
      for s in "-+*"],
    ("a blockquote marker opening a line, not a sign", "drift",
     "The monsters are:\n> ‸8 Orcs and more."),
    ("a vertical line standing apart before a number: a table cell", "drift",
     "| Year | 2011 | ‸5 | people |"),
    *[(f"the tail of the span is a number a share or currency sign {s!r} follows "
       f"after a space", "tampering", f"Operating margin fell 5.2‸ {s} last year.")
      for s in "%$€£‰"],
    *[(f"the tail of the span is a number an operator {s!r} with spaces follows, "
       f"a number after it", "tampering", f"The value is 1.296‸ {s} 10 units.")
      for s in "×+≈~<>"],
    *[(f"an operator {s!r} with spaces between two numbers, the span after it",
       "tampering", f"The value is 1.296 {s} ‸10 units.")
      for s in "×+≈~<>"],
    ("the tail of the span is a number a vertical line with spaces follows: a table "
     "cell", "drift", "| Year | 2011‸ | 5 | people |"),
    ("the tail of the span is a year a dash with spaces follows: a range", "drift",
     "It was founded in 1946‸ - 5 July is the date."),
    ("a currency with letters in front of it (US$), the span after the letters",
     "tampering", "The deal was worth US‸$1.183 million in total."),
    ("a currency with letters in front of it (C$), the span after the letters",
     "tampering", "The deal was worth C‸$5 million in total."),
    ("a currency with letters in front of it (HK$), the span after the letters",
     "tampering", "The deal was worth HK‸$100 million in total."),
    ("a bracket pair hugging a number, the span inside it", "tampering",
     "Sales rose (‸5%‸) in the year."),
    ("a boundary inside the cluster of a numeral and its combining mark", "tampering",
     "It costs 5‸\u0301 per unit today."),
    ("an invisible character inside the whitespace that groups a number, the span "
     "after it", "tampering", "about 66 \u200b ‸300 people were counted."),
    # Two of them, each with a space on either side: a stretch that reads two
    # words each way and takes an invisible character for a word still stops
    # short of the `66` (mutant R08 of round 8).
    ("two invisible characters inside the whitespace that groups a number, the span "
     "after them", "tampering", "about 66 \u200b \u200b ‸300 people were counted."),
    # A currency a space after a number, with a number of its own glued to it,
    # is that number's and not the first one's (mutant R10 of round 8).
    ("the tail of the span is a number a currency follows after a space, with its "
     "own number", "drift", "We sold 5‸ $10 tickets at the door."),
    # Round 9. The review of round 8 found every one of these closing the books
    # (its sections 1 and 2): a span that begins on a mark which is itself part
    # of the number - a currency, a decimal point - was never asked about the
    # sign in front of that mark, and marks a space away were asked about for
    # two categories only. Each line is a decision by meaning, not by category.
    *[(f"a sign {s!r} in front of a currency, the span beginning at the currency",
       "tampering", f"They reported a loss of {s}‸$5.2 million this year.")
      for s in "-\u2212~\u2264>+\u00b1\u2248"],
    ("a sign in front of a decimal point, the span beginning at the point",
     "tampering", "The rate moved -‸.5% overnight."),
    ("a sign a space in front of a decimal point, the span beginning at the point",
     "tampering", "Margin moved - ‸.5% overnight."),
    ("a range of two sums of money, the span beginning at the second",
     "tampering", "Renovations cost $100-‸$300 a square foot."),
    ("the tail of the span is the first of a range of two sums of money",
     "tampering", "Renovations cost $100‸-$300 a square foot."),
    *[(f"a currency {s!r} a space in front of a number, the span after it",
       "tampering", f"The fee is {s} ‸120 per person.") for s in "$\u20ac\u00a3"],
    ("letters and a currency a space in front of a number, the span after them",
     "tampering", "Revenue was US$ ‸1.183 million."),
    ("small letters glued to a currency, the span after the letters", "tampering",
     "The deal was worth us‸$5 million in total."),
    *[(f"an operator {s!r} with spaces between two numbers, the span after it",
       "tampering", f"We get x = 3947 {s} ‸7 in the end.") for s in "/*"],
    *[(f"the tail of the span is a number an operator {s!r} with spaces follows, "
       f"a number after it", "tampering", f"Star A: 1.296‸ {s} 10^15 units.")
      for s in "/*"],
    ("the tail of the span is a number a degree sign follows after a space",
     "tampering", "Density at 25‸ \u00b0C is 0.997."),
    ("the tail of the span is a number a vulgar fraction follows after a space",
     "tampering", "It weighs 5‸ \u00bd pounds."),
    ("a vulgar fraction a space after a number, the span beginning at it",
     "tampering", "It weighs 5 ‸\u00bd pounds."),
    ("a bracket pair hugging a number, the span ending before the closing one",
     "tampering", "Sales fell ‸(5%‸) in the year."),
    # The honest side of the same reading (round 9, the review's section 2.2
    # and the owner's list): a mark that relates, labels or frames a number is
    # not part of it, and a rule that reads it as part refuses real tool output.
    ("the value of compact JSON after its colon", "drift",
     'result {"count":‸42‸} done'),
    ("a field of CSV after a quoted field", "drift",
     'city,population\n"Paris",‸2161000‸\n"Oslo",709000'),
    ("the value of a parameter after an equals sign", "drift",
     "Found x=‸5 in the log."),
    ("the value of a query parameter in an address", "drift",
     "See index.php?title=Cuneiform&section=‸1‸) for the table."),
    ("an equation with spaces, the span after the equals sign (SmolAgents__072)",
     "drift", "Charge e = ‸1.60 \u00d7 10\u201319 C, mp = 1.67 \u00d7 10\u201327 kg"),
    ("an arrow with spaces between two numbers, the span after it", "drift",
     "Headcount grew 5 \u2192 ‸7 this year."),
    ("an arrow glued between two numbers, the span after it", "drift",
     "Version 1\u2192‸2 migration done."),
    ("a copyright mark glued to a year, the span after it", "drift",
     "Footer text \u00a9‸2015 The Hollywood Reporter."),
    ("a check mark glued to a count, the span after it", "drift",
     "Summary: \u2705‸5 tests passed."),
    ("a bullet between a title and a count, the span after it", "drift",
     "James Bond 007\u2022‸398K views"),
    ("an equals sign with spaces between two numbers, the span after it",
     "drift", "The value is 1.296 = ‸10 units."),
    ("the tail of the span is a number an equals sign with spaces follows",
     "drift", "The value is 1.296‸ = 10 units."),
    ("an equals sign standing apart in the middle of a line, the span after it",
     "drift", "Operating margin = ‸5.2 % lower than last year."),
    ("the tail of the span is a number a currency follows after a space, the "
     "currency of the next number",
     "drift", "We sold 5‸ $ 10 tickets."),
    ("a bracket with a space inside it, the span inside", "drift",
     "Group size was (‸5 people‸) in total."),
    # Where a line begins (the review's mutants Y01, Y02, Y03): a list marker
    # opens a line after `\n`, and just as well at the very start of the text,
    # after `\r`, after U+2028, and indented.
    ("a list marker at the very start of the text", "drift", "- ‸8 Orcs and more."),
    ("a list marker after a carriage return", "drift",
     "The monsters are:\r- ‸8 Orcs and more."),
    ("a list marker after a line separator", "drift",
     "The monsters are:\u2028- ‸8 Orcs and more."),
    ("an indented list marker", "drift", "The monsters are:\n   - ‸8 Orcs and more."),
    # A sign apart from its number by more than one space, or by a tab (Y04);
    # a share sign more than one space after it (Y05).
    # A sign composed of two characters (`=` and COMBINING LONG SOLIDUS
    # OVERLAY, `≠` in the view) opening a line is a list marker like any other.
    # Built for the review's mutant Y12 and it does not catch it: that mutant
    # is equivalent, because inside a cluster `view` maps no offset but the
    # first, so a sign has one raw place whichever end the map is read from.
    ("a sign of two characters opening a line: a list marker", "drift",
     "The list:\n=\u0338 ‸5 items are left."),
    ("a sign two spaces in front of a number in the middle of a line", "tampering",
     "Operating margin -  ‸5.2 % lower than last year."),
    ("a sign a tab in front of a number in the middle of a line", "tampering",
     "Operating margin -\t‸5.2 % lower than last year."),
    ("the tail of the span is a number a share sign follows after two spaces",
     "tampering", "Operating margin fell 5.2‸  % last year."),
]


#: Pairs of two strings a rule that **widens** the number convention accepts,
#: and the shipped convention refuses by decision. The review of round 7 made
#: the rule read `66'300` as `66,300` (W3) and neither this instrument nor 1031
#: tests noticed. A declared convention is a claim that everything else is a
#: different writing: `.` is a decimal point, `'` and `_` and `·` group nothing
#: (docs/design.md; `'90s` above). The last two lines are the other direction,
#: drift a rule that narrows the convention refuses: the tuple of two decimals
#: `_already_behind_a_point` exists for (mutant X10 of the review), and the
#: grouping space with an invisible character in it (X19).
THE_CONVENTION_MUST_NOT_WIDEN: List[Tuple[str, str, str]] = [
    ("an apostrophe grouping a number read as a comma", "found 66'300 people",
     "found 66,300 people"),
    ("an apostrophe grouping a number read as a space", "found 66'300 people",
     "found 66 300 people"),
    ("a decimal point read as a grouping comma", "found 66.300 people",
     "found 66,300 people"),
    ("a low line read as a grouping comma", "found 66_300 people",
     "found 66,300 people"),
    ("a middle dot read as a grouping comma", "found 66·300 people",
     "found 66,300 people"),
    # The review's mutant V4: a list marker is a dash and a space, and the
    # space is what keeps it off the number. Dropped, the marker is a sign.
    ("a list marker glued to its number", "- 5 people were counted",
     "-5 people were counted"),
]
THE_CONVENTION_MUST_NOT_NARROW: List[Tuple[str, str, str]] = [
    ("a comma between two decimals of a tuple, a space added after it",
     "(0.16355140186915887,763.6387850467289)",
     "(0.16355140186915887, 763.6387850467289)"),
    ("a grouping space with an invisible character in it, cited with a comma",
     "about 66 \u200b 300 people", "about 66,300 people"),
    # The review's mutant V6: the digits of a time's minutes group nothing,
    # as the digits of a fraction do not.
    ("a comma after the minutes of a time, a space added",
     "at 10:30,500 people", "at 10:30, 500 people"),
]


#: Word shapes the review of round 7 broke the rule on and the instrument did
#: not ask (mutants X05, X07, X08, X17, X21). `‸` is where the span begins; the
#: side is decided by `_word_pair`, this instrument's own reading of a word.
WORDS_THE_REVIEW_BROKE: List[Tuple[str, str]] = [
    ("a capital alone after a small letter (vitaminA)", "Take vitamin‸A daily."),
    ("a word with a capital inside it, cut at its second capital (iPadPro)",
     "The iPad‸Pro model sold out."),
    ("one letter before a numeral (x10)", "It has a x‸10 zoom lens."),
    ("a span beginning on the hyphen of a word (non-lethal)", "A non‸-lethal dose was given."),
    ("a word one letter into it after a quotation mark (\"asymptomatic\")",
     "The \"a‸symptomatic\" group grew."),
]


def span_pairs(quote: str) -> List[SpanPair]:
    """Every shape, with the side decided by `span_cuts_a_number` - by what the
    boundary does to the numbers of the source, not by which shape it was."""
    return [(kind, "tampering" if span_cuts_a_number(source, start, end) else "drift",
             source, start, end, cited)
            for kind, source, start, end, cited in span_shapes(quote)]


#: What the tool is asked about a span pair: a one-document run, one derived
#: artifact, one claim, one entry citing the span. The answer comes from
#: `verify.verify_entry` itself - the path a user's trace takes, fast path and
#: all - and not from a copy of it here. That is the whole point of this class:
#: the review of round 4 found a forgery that `says_the_same` never sees,
#: because the equality above it answers first, so a threshold standing on
#: `says_the_same` cannot measure it.
_THE_ANSWER = "The committee published its finding in one sentence."


def the_tool_answers(rule, source: str, start: int, end: int, cited: str) -> bool:
    """Does the tool close the books on this entry, with `rule` as its gate?"""
    return the_tool_answers_why(rule, source, start, end, cited)[0]


def the_tool_answers_why(rule, source: str, start: int, end: int, cited: str
                         ) -> Tuple[bool, str]:
    """(does the tool close the books, the reason it gives), with `rule` as
    its gate.

    `verify.says_the_same` is swapped for the duration of the call, the way
    `with_the_number_convention_widened` swaps the rule's number pattern:
    nothing here reimplements `verify_entry`, and every rule on the dial is
    asked through the same path a real trace takes.
    """
    from tallystick import verify
    from tallystick.types import (Account, AccountType, Artifact, ArtifactKind,
                                  Claim, Entry, Run, Step)

    run = Run(artifacts={"doc": Artifact("doc", ArtifactKind.DOCUMENT, source),
                         "ans": Artifact("ans", ArtifactKind.FINAL_ANSWER, _THE_ANSWER)},
              steps=[Step("s1", "answer", ("doc",), ("ans",))],
              claims={"c1": Claim("c1", "ans", 0, len(_THE_ANSWER), _THE_ANSWER)})
    entry = Entry("e1", "c1", Account(AccountType.EVIDENCE, "doc", start, end), cited)
    shipped = verify.says_the_same
    verify.says_the_same = rule
    try:
        done = verify.verify_entry(run, entry)
        return bool(done.verified), done.reason
    finally:
        verify.says_the_same = shipped


def score_spans(rule, pairs: List[SpanPair]):
    """(refused of the tampering, accepted of the drift, by kind and side).

    The key carries the side because a shape and a side are not the same
    thing: `the span begins after a grouped number` is honest 5608 times and a
    cut 7 times, when the real quote it is built on happens to begin with a
    group of three digits and the planted separator groups the two together.
    The reading decides each pair on its own, and a table keyed by the shape
    alone would print one of those two counts under the other one's name.
    """
    refused = tampering = accepted = drift = 0
    by_kind: Dict[Tuple[str, str], List[int]] = {}
    for kind, side, source, start, end, cited in pairs:
        ok, why = the_tool_answers_why(rule, source, start, end, cited)
        # A span through a number that the tool refuses **as a cut word** has
        # been refused for the wrong reason, and the reason is what a reader
        # is shown. `cuts_a_number` stands before `cuts_a_word` in
        # `verify_entry`, so a working reading of a number answers first; a
        # refusal that reaches the word check means the number reading missed
        # it. Mutant X13 of the round-7 review (a boundary inside a numeral's
        # cluster not read as a cut) was caught by nobody because the word
        # check refused the same span a line later.
        if side == "tampering" and why == "span_cuts_a_word":
            ok = True
        row = by_kind.setdefault((kind, side), [0, 0])
        row[0] += ok
        row[1] += 1
        if side == "tampering":
            tampering += 1
            refused += not ok
        else:
            drift += 1
            accepted += ok
    return refused, tampering, accepted, drift, by_kind


# --------------------------------------------------------------------------- #
# the boundary of a span, read as a word of the source
# --------------------------------------------------------------------------- #

# The same event as the boundary class above, with letters where that one has
# digits, and it is the more dangerous of the two. `The drug is unsafe` cited
# from the character after `un` is `safe`, **word for word**: the sense is the
# opposite of the source's and the equality fast path in `verify_entry` closes
# the books before any rule of the gate is consulted. Round 5 named it, priced
# it at 118 of 5615 real spans for a crude reading, and left the decision to
# the owner.
#
# The reading below is this instrument's own, and it is written twice for the
# reason the number reading is (project rule 14): a list of forms by hand, and
# a walk that generalises it. It shares no code, no constant and no import with
# the rule, so it cannot agree with the rule about what the rule is wrong about.

#: What holds one word together across a character that is not a letter, asked
#: of Unicode: a dash of any kind (`Pd`), an apostrophe (`_APOSTROPHES`), each
#: only where a letter stands on both sides of it, and an invisible character
#: (`Cf`) anywhere inside the word.
#:
#: The dash was a hyphen and nothing else until round 7, on the argument that
#: `London–Paris flights` is two words and `Paris flights` cuts nothing. The
#: owner decided the other way (round 7, D1), and the reason is the pair the
#: argument walked past: `non–lethal` typed with an en dash is one word, and
#: cited as `lethal` it says the opposite. A dash between two letters now
#: holds them, whichever dash it is; `structure—there` cited from `there` is
#: refused, and that price is named in `NAMED_NOT_SCORED` below.
def _holds_a_word_together(text: str, i: int) -> bool:
    ch = text[i]
    if not (_dash(ch) or ch in _APOSTROPHES):
        return False
    before = _visible_before(text, i)
    after = _visible_after(text, i + 1)
    return (before is not None and after is not None
            and _letter(text[before]) and _letter(text[after]))


def _visible_before(text: str, i: int):
    """The index of the last character before `i` that is not invisible."""
    j = i - 1
    while j >= 0 and _invisible(text[j]):
        j -= 1
    return j if j >= 0 else None


def _visible_after(text: str, i: int):
    """The index of the first character at or after `i` that is not invisible."""
    j = i
    while j < len(text) and _invisible(text[j]):
        j += 1
    return j if j < len(text) else None


def _letters_left(text: str, i: int) -> str:
    """The run of letters ending just before `i`, invisible characters skipped."""
    out: List[str] = []
    j = i - 1
    while j >= 0 and (_letter(text[j]) or _invisible(text[j])):
        if _letter(text[j]):
            out.append(text[j])
        j -= 1
    return "".join(reversed(out))


def _letters_right(text: str, i: int) -> str:
    """The run of letters starting at `i`, invisible characters skipped."""
    out: List[str] = []
    j = i
    while j < len(text) and (_letter(text[j]) or _invisible(text[j])):
        if _letter(text[j]):
            out.append(text[j])
        j += 1
    return "".join(out)


def _an_escape(text: str, i: int) -> bool:
    """Is the letter at `i` the letter of an escape a scrape wrote as two
    characters - the `n` of a literal `\\n`? 92 of the 118 real spans round 5
    counted at a small letter followed by a capital are this and not a seam
    between two words: the small letter is the `n`."""
    return i > 0 and text[i - 1] == "\\" and _letter(text[i])


def _a_seam(text: str, left: int, right: int) -> bool:
    """Is the place between the letter at `left` and the letter at `right` a
    seam a scrape left, and not a place inside one word?

    Two shapes, and only two (round 7, the owner's decision C3):

      * the letter on the left is the letter of an escape (`_an_escape`),
        whatever stands on the right;
      * a small letter followed directly by a capital, where the run on the
        left is three letters or more with no capital after its first, and
        the run on the right is a capital followed by small letters only.
        `resultsThe` and `WikipediaIt` are that; `NoSQL`, `kWh`, `iPhone`,
        `disableSSL` and `McDonald's` are not, and each is one word.
    """
    if _an_escape(text, left):
        return True
    lefts, rights = _letters_left(text, left + 1), _letters_right(text, right)
    return (_small(text[left]) and _capital(text[right])
            and len(lefts) >= 3 and not any(_capital(c) for c in lefts[1:])
            and len(rights) >= 2 and not any(_capital(c) for c in rights[1:]))


#: (the text, the words it states, why). One line per form, so that changing
#: this reading is a visible change to a literal with its reason beside it.
HOW_A_WORD_IS_WRITTEN: List[Tuple[str, List[str], str]] = [
    ("unsafe",      ["unsafe"],        "a run of letters is one word, and `unsafe` cited "
                                       "as `safe` says the opposite of it"),
    ("not able",    ["not", "able"],   "a space ends a word"),
    ("notable",     ["notable"],       "and without the space there is one word: this is "
                                       "the pair round 5 left open"),
    ("wasn't",      ["wasn't"],        "an apostrophe between two letters is inside the "
                                       "word, so `wasn't` may not be cited as `was`"),
    ("wasn\u2019t", ["wasn\u2019t"],   "the typeset apostrophe does the same"),
    ("non-lethal",  ["non-lethal"],    "a hyphen between two letters is inside the word"),
    ("non\u2013lethal", ["non\u2013lethal"], "and so is an en dash (round 7, D1): the same "
                                       "word typed with another key"),
    ("London\u2013Paris", ["London\u2013Paris"], "which makes this one word to this reading "
                                       "too. What that costs is two real spans, named in "
                                       "`NAMED_NOT_SCORED`"),
    ("\u05d0\u05d9\u05be\u05d0\u05e4\u05e9\u05e8", ["\u05d0\u05d9\u05be\u05d0\u05e4\u05e9\u05e8"],
                                       "the Hebrew maqaf is a dash (Pd): `impossible`, which "
                                       "cited from its second word is `possible`"),
    ("state-of-the-art", ["state-of-the-art"], "hyphens repeat"),
    ("half-", ["half"],                "a hyphen with no letter after it is not inside a "
                                       "word: it is the end of a line or a suspended form"),
    ("COVID-19",    ["COVID"],         "a hyphen with a digit after it is not inside a "
                                       "word either; digits are the other class"),
    ("U.S.",        ["U", "S"],        "a full stop is not inside a word"),
    ("na\u00efve",  ["na\u00efve"],    "a letter is a letter whatever accent it carries"),
    ("de\u0301loyal", ["de\u0301loyal"], "and whether the accent is composed or stands "
                                       "after it as a mark (NFD): `d\u00e9loyal` cited as "
                                       "`loyal`"),
    ("un\u00adsafe", ["un\u00adsafe"], "an invisible character is not a place a reader can "
                                       "see a word end: the soft hyphen"),
    ("un\u200bsafe", ["un\u200bsafe"], "and the zero-width space"),
    ("O'Brien",     ["O'Brien"],       "the apostrophe clause does not ask about case"),
    ("resultsThe",  ["results", "The"], "a small letter followed directly by a capital, a "
                                       "word of three letters or more on the left and a "
                                       "capitalised word on the right, is a seam a scrape "
                                       "left between two texts"),
    ("\\nOption",   ["n", "Option"],   "the letter of an escape is a word of its own: 92 of "
                                       "the 118 spans round 5 counted are `\\n` and a capital"),
    ("\\nemotions", ["n", "emotions"], "whatever case follows it"),
    ("C:\\nuclear", ["C", "n", "uclear"], "**the cost of the escape, named.** A path cut "
                                       "after its backslash and one letter is read the "
                                       "same way; `NAMED_NOT_SCORED` carries it"),
    ("NoSQL",       ["NoSQL"],         "a capital inside a word that is not a seam: the run "
                                       "on the right is not a capitalised word"),
    ("kWh",         ["kWh"],           "a unit: one letter on the left"),
    ("iPhone",      ["iPhone"],        "one letter on the left, and no scrape leaves one"),
    ("disableSSL",  ["disableSSL"],    "the run on the right is all capitals"),
    ("McDonald's",  ["McDonald's"],    "two letters on the left: the seam asks for three"),
    ("LaGuardia",   ["LaGuardia"],     "a capital after the first letter on the left"),
    ("onThe",       ["onThe"],         "**the cost of the three-letter floor, named:** a "
                                       "scrape seam after a two-letter word reads as one "
                                       "word and a span cut there is refused"),
    ("MacArthur",   ["Mac", "Arthur"], "**the cost of the seam, named rather than hidden.** "
                                       "`MacArthur` cited as `Arthur` is a real cut of a "
                                       "real word and this reading calls it a seam. It is "
                                       "not built into a scored pair: a threshold that "
                                       "demanded it be accepted would fail a later reading "
                                       "that gets it right (project §45). "
                                       "`NAMED_NOT_SCORED` carries it"),
    ("\u041c\u0430\u043a\u0410\u0440\u0442\u0443\u0440", ["\u041c\u0430\u043a", "\u0410\u0440\u0442\u0443\u0440"],
                                       "the same cost in Cyrillic"),
    ("\u043d\u0435\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e", ["\u043d\u0435\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e"],
                                       "a word of any script is a run of letters"),
    ("\u0430\u03b1", ["\u0430\u03b1"], "and a run does not ask which script each letter is"),
    ("\u0928\u093e\u092a\u0938\u0902\u0926", ["\u0928\u093e\u092a\u0938\u0902\u0926"],
                                       "Devanagari: a vowel sign is a mark (Mc), and a mark "
                                       "belongs to the letter it sits on"),
    ("\u4e0d\u5b89\u5168", ["\u4e0d\u5b89\u5168"], "a script with no case and no spaces is one "
                                       "run, so a boundary inside it is a cut: `\u4e0d\u5b89"
                                       "\u5168` is `unsafe` and `\u5b89\u5168` is `safe`"),
    ("ALL CAPS",    ["ALL", "CAPS"],   "a capital after a capital is not a seam, so "
                                       "`UNSAFE` cited as `SAFE` is still a cut"),
    ("66,300 people", ["people"],      "digits are not letters: this reading says nothing "
                                       "about numbers, and `span_cuts_a_number` says "
                                       "nothing about words"),
]


#: (the text, where the boundary stands, does it cut, why) for a boundary
#: between a letter and a numeral, which the list above cannot show because a
#: numeral is not in any word. Checked against `_a_letter_and_a_numeral_meet`
#: at start-up.
HOW_A_BOUNDARY_IS_READ: List[Tuple[str, int, bool, str]] = [
    ("$5M",         2, True,  "a number and the letter after it are one amount: `$5M` cited "
                              "as `$5` is a millionth of it, and cited as `M today` it has "
                              "lost its number"),
    ("$5Million",   2, True,  "the same when the unit is a whole word. A seam here would "
                              "leave the same forgery open, so there is none (round 7, E1)"),
    ("5bn",         1, True,  "a unit of amount"),
    ("10km",        2, True,  "a unit of measure"),
    ("2,883Medal",  5, True,  "**the cost of that line, named:** a real scrape seam of a "
                              "table, and 2 of the 5615 real spans; `NAMED_NOT_SCORED`"),
    ("B12",         1, True,  "a letter directly before a digit: `B12` cited as `12`"),
    ("H5N1",        1, True,  "one capital before a digit"),
    ("COVID19",     5, True,  "a word ending in a capital before a digit"),
    ("m\u00b2",     1, True,  "a unit and its power: `m` is not `m\u00b2`"),
    ("Kashmir1",    7, False, "a word of two or more letters ending in a small letter, then "
                              "a digit: the seam a scraped table leaves, copied from the "
                              "sources (round 7, F2)"),
    ("Windows10",   7, False, "**the cost of that seam, named:** `Windows10` cited as `10` "
                              "passes. It is a forgery, carried by `NAMED_NOT_SCORED` and "
                              "not by a threshold, so that a reading closing it later is "
                              "not failed for it"),
    ("\\n1",        2, False, "the letter of an escape, then a digit"),
]


def words_and_where(text: str) -> List[Tuple[int, int, str]]:
    """Every word `text` states, in order: (where it starts, where it ends, the
    word).

    Walks the characters. A word is a run of letters (`_letter`), carried across
    invisible characters and across a dash or an apostrophe with a letter on
    both sides of it, and broken at a seam (`_a_seam`). Every property is asked
    of Unicode by the functions at the head of this file.
    """
    out: List[Tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        if not _letter(text[i]):
            i += 1
            continue
        last = i
        while True:
            k = _visible_after(text, last + 1)
            if k is None:
                break
            if _letter(text[k]):
                if _a_seam(text, last, k):
                    break
                last = k
                continue
            if _holds_a_word_together(text, k) and not _an_escape(text, last):
                last = _visible_after(text, k + 1)
                continue
            break
        out.append((i, last + 1, text[i:last + 1]))
        i = last + 1
    return out


def words_stated(text: str) -> List[str]:
    """Every word `text` states, in order."""
    return [w for _start, _end, w in words_and_where(text)]


def _a_letter_and_a_numeral_meet(text: str, i: int) -> bool:
    """Does the boundary at `i` stand between a letter and a numeral that are
    one token? A numeral followed by a letter always is (`$5M`, `10km`); a
    letter followed by a numeral is, unless the letter is an escape's or ends
    a word of two or more letters in a small letter (`Kashmir1`, a table's
    seam). Invisible characters on either side are stepped over."""
    before, after = _visible_before(text, i), _visible_after(text, i)
    if before is None or after is None:
        return False
    a, b = text[before], text[after]
    if _numeral(a) and _letter(b):
        return True
    if _letter(a) and _numeral(b):
        if _an_escape(text, before):
            return False
        return not (len(_letters_left(text, before + 1)) >= 2 and _small(a))
    return False


def span_cuts_a_word(source: str, start: int, end: int) -> bool:
    """Does the span `[start, end)` of `source` begin or end inside a word?

    A word of the source lies wholly inside the span or wholly outside it;
    anything else is a boundary drawn through a word, and the text the span
    carries says something the source does not. `The drug is unsafe` cited from
    the character after `un` is `safe`, and the citation is word for word, so
    no comparison of two strings can see it - which is the same argument
    `span_cuts_a_number` makes with digits, one class over. A boundary where a
    letter and a numeral meet as one token is the same event (`$5M` -> `$5`).
    """
    if any(s < end and start < e and not (start <= s and e <= end)
           for s, e, _w in words_and_where(source)):
        return True
    return (_a_letter_and_a_numeral_meet(source, start)
            or _a_letter_and_a_numeral_meet(source, end))


def _check_the_word_reading() -> None:
    """The hand-written lists against the walks. Stops the run if they differ."""
    wrong = [(text, want, words_stated(text))
             for text, want, _why in HOW_A_WORD_IS_WRITTEN
             if words_stated(text) != want]
    wrong += [(f"{text[:i]}|{text[i:]}", "a cut" if cuts else "no cut",
               "a cut" if not cuts else "no cut")
              for text, i, cuts, _why in HOW_A_BOUNDARY_IS_READ
              if _a_letter_and_a_numeral_meet(text, i) != cuts]
    if wrong:
        for text, want, got in wrong:
            print(f"the word reading disagrees with its own list: {text!r} "
                  f"is written {want} and read {got}", file=sys.stderr)
        raise SystemExit(2)


#: (the word as the source has it, how many characters the forger leaves
#: outside the span, what the affix does). Written out rather than generated:
#: every one of these is a citation that says something its source does not.
#: Round 7 adds the rest of the brief: the capitals inside a word that are not a
#: seam, a word cut from a number, and material that is not Latin - Cyrillic,
#: Greek, and four scripts without case (Devanagari, Chinese, Arabic, Hebrew).
CUT_AT_THE_HEAD: List[Tuple[str, int, str]] = [
    ("unsafe", 2, "un-"),
    ("insecure", 2, "in-"),
    ("impossible", 2, "im-"),
    ("nonlethal", 3, "non-"),
    ("non-lethal", 4, "non- with a hyphen"),
    ("non\u2013lethal", 4, "non- with an en dash"),
    ("disproved", 3, "dis-"),
    ("notable", 3, "the pair round 5 left open"),
    ("Unsafe", 2, "un- with the word opening a sentence"),
    ("NoSQL", 2, "a capital inside a word, NoSQL"),
    ("kWh", 1, "a capital inside a unit, kWh"),
    ("disableSSL", 7, "a capital inside a word, disableSSL"),
    ("McDonald's", 2, "a Mc- name the seam's three-letter floor keeps whole"),
    ("de\u0301loyal", 3, "d\u00e9- decomposed (NFD), after the accent"),
    ("de\u0301loyal", 2, "d\u00e9- decomposed (NFD), between the letter and its accent"),
    # Found by mutating the rule of round 7, not by reading it: a `view` that
    # maps a boundary inside a letter and its accent to the letter's start
    # passed every shape above, because inside a word the start of a letter
    # is inside the word too. At the first letter of a word it is not.
    ("e\u0301lu", 1, "\u00e9lu decomposed (NFD), between its first letter and the accent"),
    ("un\u00adsafe", 3, "un- and a soft hyphen"),
    ("un\u200bsafe", 3, "un- and a zero-width space"),
    ("\u043d\u0435\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e", 2,
     "\u043d\u0435- in Cyrillic, unsafe cut to safe"),
    ("\u03b1\u03ba\u03af\u03bd\u03b4\u03c5\u03bd\u03bf", 1,
     "\u03b1- in Greek, harmless cut to danger"),
    ("\u0928\u093e\u092a\u0938\u0902\u0926", 2,
     "\u0928\u093e- in Devanagari, dislike cut to like, after a vowel sign"),
    ("\u4e0d\u5b89\u5168", 1, "\u4e0d in Chinese, unsafe cut to safe"),
    ("\u0644\u0627\u0633\u0644\u0643\u064a", 2, "\u0644\u0627- in Arabic, wireless cut to wired"),
    ("\u05d0\u05d9\u05be\u05d0\u05e4\u05e9\u05e8", 3,
     "\u05d0\u05d9- and the maqaf in Hebrew, impossible cut to possible"),
    ("$5M", 2, "an amount cut from its number"),
    ("B12", 1, "a letter cut from its number, B12"),
    ("H5N1", 1, "a letter cut from its number, H5N1"),
    ("COVID19", 5, "a word cut from its number, COVID19"),
]
CUT_AT_THE_TAIL: List[Tuple[str, int, str]] = [
    ("harmless", 4, "-less"),
    ("wasn't", 3, "-n't"),
    ("wasn\u2019t", 3, "-n\u2019t with the typeset apostrophe"),
    ("\u0431\u0435\u0437\u0432\u0440\u0435\u0434\u043d\u044b\u0439", 6,
     "\u0431\u0435\u0437- in Cyrillic, harmless cut to without"),
    ("$5M", 1, "a number cut from its unit, $5M"),
    ("$5Million", 7, "a number cut from its unit, $5Million"),
    ("5bn", 2, "a number cut from its unit, 5bn"),
    ("10km", 2, "a number cut from its unit, 10km"),
    ("B12", 2, "a letter cut from its number, B12"),
]

#: The seams a scrape leaves, copied from the sources rather than described
#: (project §37). A span that begins or ends at one has cut no word. These are
#: the price list: a reading that calls every boundary between two letters a
#: cut loses the first nine, a reading with no escape loses the three copied
#: from `\n`, and a strict reading of a letter before a digit loses the last
#: three.
SCRAPE_SEAMS = [
    ("de Sora.\\n\\n", "El poblado fue fundado"),
    (" - Wikipedia", "It became full"),
    ("\\nMonte Carlo results", "The Venezuelan Declaration"),
    ("and Kashmir National Conference", "Deputy chief minister"),
    ("Minister of Bihar", "In office"),
    ("cm^3) for each option:\\n", "Option a: 5.50 g/cm^3"),
    ("the total luminosity\\n\\n", "Maximum brightness (no e"),
    ("\u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b",
     "\u0412\u0435\u043d\u0435\u0441\u0443\u044d\u043b\u044c\u0441\u043a\u0430\u044f "
     "\u0434\u0435\u043a\u043b\u0430\u0440\u0430\u0446\u0438\u044f"),
    ("State of Jammu and Kashmir", "1 \nBakshi Ghulam Mohammad"),
    (" \ninference; \ngraph; \nemotions", "1 Introduction\nThe semantic ma"),
    ("Paramount Pictures", "4\nJumanji: The Next Level"),
]

#: Shapes whose side this project knows and whose answer it will not score,
#: each printed with the tool's answer on every run. Two kinds, and both are
#: the price of a decision the owner made in round 7 with the number beside it:
#: forgeries a seam lets through, and honest spans a strict line refuses.
#:
#: Why printed and not scored. A threshold that demanded `Windows10` cited as
#: `10` be accepted would fail the reading that one day closes it - an exception
#: written into a threshold cannot be taken out of it later (file of errors,
#: part 110). A threshold that demanded it be refused would fail the rule the
#: owner chose knowing the price. So each is measured on its own line, which is
#: what §41 asks of anything held out of a count, and a change in any answer
#: shows in the table.
NAMED_NOT_SCORED: List[Tuple[str, ...]] = [
    # (what it is, the side it is on by meaning, the source up to the span,
    # the span, and optionally the source after the span); the citation is the
    # span word for word
    ("Windows10 cited as 10 (the cost of F2)", "tampering",
     "The update requires Windows", "10 on every machine."),
    ("MacArthur cited as Arthur (the cost of the seam)", "tampering",
     "The order was signed by Mac", "Arthur himself."),
    ("\u041c\u0430\u043a\u0410\u0440\u0442\u0443\u0440 cited as "
     "\u0410\u0440\u0442\u0443\u0440 (the same, in Cyrillic)", "tampering",
     "\u041f\u0440\u0438\u043a\u0430\u0437 \u043f\u043e\u0434\u043f\u0438\u0441"
     "\u0430\u043b \u041c\u0430\u043a", "\u0410\u0440\u0442\u0443\u0440."),
    ("C:\\nuclear cited as uclear (the cost of the escape)", "tampering",
     "The file is C:\\n", "uclear\\plan.txt today."),
    ("structure\u2014there cited from there (the cost of D1, copied from Magentic_One__011)",
     "drift", "R data matches this structure\u2014", "there are two signals, one for"),
    ("2,883Medal cited from Medal (the cost of E1, copied from Magentic_One__004)",
     "drift", "Panama1 \nTotal2,883", "Medal count[edit]Main articles"),
    ("1811The cited from The (the cost of E1, copied from Magentic_One__056)",
     "drift", "10,200,000 resultsJuly 5, 1811", "The Venezuelan Declaration of "),
    ("onThe cited from The (the cost of C3's three-letter floor)", "drift",
     "Click to read more on", "The next page has the tables."),
    ("NASAThe cited from The (an acronym run into a word)", "drift",
     "Source: NASA", "The mission ended in 2019."),
    ("l'Italia cited from Italia (an elision)", "drift",
     "Il governo ha detto che l'", "Italia crescer\u00e0."),
    ("a Chinese clause cited from its middle", "drift",
     "\u59d4\u5458\u4f1a\u8ba4\u4e3a", "\u8fd9\u79cd\u836f\u662f\u5b89\u5168\u7684\u3002"),
    # Round 8. The owner closed a sign and an operator standing apart from a
    # number in the middle of a line, and a share or currency sign after a
    # space; these are what that costs and what it leaves open. The first two
    # are real spans of the corpus, copied with the text after them (the fifth
    # field), and they are the only real spans the round-8 reading refuses.
    ("Census 2011 - 2.6 % cited as 2.6 (the cost of the spaced sign and the "
     "spaced share sign, copied from SmolAgents__063)", "drift",
     "Date published: May 25, 2017\n\nAccording to Nepal Census 2011 - ", "2.6",
     " % population of Nepal uses Urdu as their"),
    ("Hydroxide (2) > 4-methylcyclohexan-1-olate cited from 4 (the cost of an "
     "operator standing apart, copied from SmolAgents__078)", "drift",
     "... Hydroxide (2) > ", "4-methylcyclohexan-1-olate", " (1) > Propionate (3)"),
    ("a negative number opening a line cited without its sign (a list marker "
     "looks the same)", "tampering", "Change on the year:\n- ", "5.2% in margin."),
    ("4\u2033 cited as 4 (a prime is a quotation mark once normalize has run)",
     "tampering", "", "a layer 4", "\u2033 thick"),
    # Named by the review of round 7 (its section 11, item 6).
    ("$5 million cited as $5 (a size written as a word after a space; E1 closes "
     "only $5M)", "tampering", "It paid ", "$5", " million in fines."),
    ("notFound cited as Found (camelCase carries a negation)", "tampering",
     "The key was not", "Found in the index."),
    ("not_found cited as found (snake_case carries one too)", "tampering",
     "status: not_", "found in the index."),
    ("\\nleq cited as leq (the escape reads the n of a negated relation)",
     "tampering", "Since $a \\n", "leq b$ holds."),
    ("under18 cited as 18 (a comparison run into a number, F2)", "tampering",
     "Tickets for under", "18 are free."),
    ("resultsUSA cited from USA (a seam before an acronym)", "drift",
     "Search results", "USA Today reported the flood."),
    ("YouTubeThe cited from The (a seam after a word with a capital inside)",
     "drift", "Watch on YouTube", "The flood reached the town."),
    ("iPhoneThe cited from The (the same)", "drift", "Buy an iPhone",
     "The flood reached the town."),
    # Round 9: the owner's decisions, and what one reading of a number still
    # cannot tell apart.
    ("Report - 2023 cited as 2023 (a dash separating a title reads as a sign "
     "standing apart: decision 4g of round 8, kept by the owner in round 9)",
     "drift", "see Report - ", "2023 file", ""),
    ("a member of a list of numbers set with commas and no spaces (3,4,7 cited "
     "as 4; the owner's decision of round 9: a comma between two digits keeps "
     "them one number)", "drift", "3,", "4", ",7,8,11,19"),
    ("the same list where a comma and three digits follow (67,163 cited as "
     "67): one grouped number by any reading", "drift", "43,", "67", ",163"),
    ("a ket closing after a number (|00> cited from 00, copied from the corpus)",
     "drift", "(1/√30)* (|", "00", ">+ 2i|01>)"),
    ("9.109390 x 10-31 cited as 9.109390 (a power written with the letter x, "
     "copied from SmolAgents__075)", "tampering", "electron, ", "9.109390",
     " x 10-31 kg"),
    ("a TeX formula cited without its dollar signs", "drift", "The answer is $",
     "42", "$."),
    ("an ASCII arrow in a log (step 3 -> 4 cited as 4)", "drift", "step 3 -> ",
     "4", " now."),
    ("an ellipsis against a number (wait...5 min cited as 5 min)", "drift",
     "Please wait...", "5 min", " more."),
    ("a rating star after a number (4.5★ cited as 4.5)", "drift", "Rated ",
     "4.5", "★ by users."),
    ("a time cited from its minutes (10:30 cited as 30)", "tampering",
     "Meeting at 10:", "30", " today."),
]

WordPair = Tuple[str, str, str, int, int, str]   # kind, side, source, start, end, cited


def word_shapes(quote: str) -> List[Tuple[str, str, int, int, str]]:
    """(kind, source, start, end, what the recorder wrote) for every shape of
    word boundary this round is about, forged and honest together.

    Nothing here says which side a shape is on: `word_pairs` asks
    `span_cuts_a_word`. The forged shapes are two per word, because a forger
    has two roads to the same citation and only one of them carries the affix:

      * **the span carries the whole word and the citation drops the affix** -
        `unsafe for children` cited as `safe for children`. `says_the_same` is
        asked this one and refuses it, because `unsafe` and `safe` are not the
        same content.
      * **the span begins after the affix and the citation is word for word** -
        the same `safe for children`, with the span declared two characters to
        the right. `verify_entry` answers this one with its equality fast path,
        before any rule is consulted, and no rule over two strings can ever be
        asked about it.
    """
    out: List[Tuple[str, str, int, int, str]] = []
    for word, keep, what in CUT_AT_THE_HEAD:
        lead = "The committee found the result "
        source = f"{lead}{word} {quote}"
        at = len(lead)
        cited = source[at + keep:]
        out.append((f"the head of the span is inside a word, {what}, the span carrying "
                    f"the affix and the citation dropping it",
                    source, at, len(source), cited))
        out.append((f"the head of the span is inside a word, {what}, the citation word "
                    f"for word", source, at + keep, len(source), cited))
    for word, keep, what in CUT_AT_THE_TAIL:
        source = f"{quote} and the result was {word}"
        ends = len(source)
        cited = source[:ends - keep]
        out.append((f"the tail of the span is inside a word, {what}, the span carrying "
                    f"the affix and the citation dropping it", source, 0, ends, cited))
        out.append((f"the tail of the span is inside a word, {what}, the citation word "
                    f"for word", source, 0, ends - keep, cited))

    # The honest shapes. Each puts a boundary where a crude check fires and a
    # correct one must not, and the citation is the span inside quotation marks
    # so that the rule is asked rather than the fast path.
    honest: List[Tuple[str, str, int, int]] = []
    for before, after in SCRAPE_SEAMS:
        source = f"{before}{after} {quote}"
        honest.append((f"the head of the span is at a scrape seam, {before[-12:]}|{after[:10]}",
                       source, len(before), len(source)))
        tail = f"{quote} {before}{after}"
        honest.append((f"the tail of the span is at a scrape seam, {before[-12:]}|{after[:10]}",
                       tail, 0, len(tail) - len(after)))
    honest += [
        ("the span begins at a word after a space",
         f"The result was safe and {quote}", len("The result was "), None),
        ("the span begins at a word after a full stop and a space",
         f"That was agreed. Safe conditions and {quote}", len("That was agreed. "), None),
        ("the span begins at a word after a line break",
         f"The result was safe\nand {quote}", len("The result was safe\n"), None),
        ("the span begins at the whole of a word a prefix flips",
         f"The result was unsafe and {quote}", len("The result was "), None),
        ("the span ends at a word before a space",
         f"{quote} and the result was safe", None, None),
        ("the span ends at a word its own sentence ends after",
         f"{quote} and it was safe. More was written.",
         0, len(quote) + len(" and it was safe")),
        ("the span ends at a word a hyphen does not reach",
         f"{quote} and the result was half- ", 0, len(quote) + len(" and the result was half")),
        ("the span begins at a Cyrillic word after a space",
         f"\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0431\u0435\u0437\u043e"
         f"\u043f\u0430\u0441\u0435\u043d {quote}", len("\u0420\u0435\u0437\u0443\u043b\u044c"
                                                        "\u0442\u0430\u0442 "), None),
        ("the span begins at a Greek word after a space",
         f"\u03a4\u03bf \u03b1\u03c0\u03bf\u03c4\u03ad\u03bb\u03b5\u03c3\u03bc\u03b1 "
         f"\u03ae\u03c4\u03b1\u03bd \u03b1\u03ba\u03af\u03bd\u03b4\u03c5\u03bd\u03bf {quote}",
         len("\u03a4\u03bf \u03b1\u03c0\u03bf\u03c4\u03ad\u03bb\u03b5\u03c3\u03bc\u03b1 "), None),
        ("the span begins at a Devanagari word after a space",
         f"\u092a\u0930\u093f\u0923\u093e\u092e \u0938\u0941\u0930\u0915\u094d\u0937\u093f"
         f"\u0924 \u0925\u093e {quote}", len("\u092a\u0930\u093f\u0923\u093e\u092e "), None),
        ("the span begins at a Chinese sentence after a full stop",
         f"\u59d4\u5458\u4f1a\u53d1\u8868\u4e86\u7ed3\u8bba\u3002\u8fd9\u79cd\u836f\u662f"
         f"\u5b89\u5168\u7684 {quote}", len("\u59d4\u5458\u4f1a\u53d1\u8868\u4e86\u7ed3"
                                            "\u8bba\u3002"), None),
        ("the span begins at an Arabic word after a space",
         f"\u0643\u0627\u0646\u062a \u0627\u0644\u0646\u062a\u064a\u062c\u0629 \u0622\u0645"
         f"\u0646\u0629 {quote}", len("\u0643\u0627\u0646\u062a "), None),
        ("the span begins at a Hebrew word after a space",
         f"\u05d4\u05ea\u05d5\u05e6\u05d0\u05d4 \u05d4\u05d9\u05d9\u05ea\u05d4 \u05d1\u05d8"
         f"\u05d5\u05d7\u05d4 {quote}", len("\u05d4\u05ea\u05d5\u05e6\u05d0\u05d4 "), None),
    ]
    for kind, source, start, end in honest:
        start = 0 if start is None else start
        end = len(source) if end is None else end
        out.append((kind, source, start, end, '"' + source[start:end] + '"'))
    return out


def word_sweep() -> List[Tuple[str, str, int, int, str]]:
    """Every character Unicode says holds a word together, asked once each.

    `CUT_AT_THE_HEAD` names a soft hyphen, a zero-width space and three dashes.
    This walks the classes instead: every invisible character (`Cf`), every
    dash (`Pd`) and every apostrophe, standing inside `unsafe` and `nonlethal`,
    with the span declared just past it and the citation word for word - and,
    for the invisible ones, the span declared on it too. A rule that knows six
    invisible characters and seven dashes by name fails here on the rest.
    """
    out: List[Tuple[str, str, int, int, str]] = []
    tail = " for children, the trial found."
    for cp in range(0x110000):
        ch = chr(cp)
        if _invisible(ch):
            source = f"The drug is un{ch}safe{tail}"
            at = len("The drug is un")
            out.append((f"the sweep: an invisible character inside a word, the span on it",
                        source, at, len(source), source[at:]))
            out.append((f"the sweep: an invisible character inside a word, the span after it",
                        source, at + 1, len(source), source[at + 1:]))
        elif _dash(ch) or ch in _APOSTROPHES:
            source = f"The result was non{ch}lethal{tail}"
            at = len("The result was non") + 1
            out.append((f"the sweep: {'a dash' if _dash(ch) else 'an apostrophe'} holding a "
                        f"word together, the span after it", source, at, len(source),
                        source[at:]))
    return out


def word_pairs(quote: str) -> List[WordPair]:
    """Every shape, with the side decided by this instrument's own reading of a
    word and by nothing else - not by which shape it was and not by any formula
    of the rule.

    Two questions decide it, because the forger's two roads are not the same
    question and a boundary check is the right judge of only one of them:

      * **the boundary cuts a word of the source** - `span_cuts_a_word`. This
        is the road with nothing in the pair of strings to see.
      * **the citation states words the span does not** - `words_stated` of the
        two, compared. This is the road where the span carries the whole word
        and the recorder drops the affix, and it is a question about two
        strings, which is what `says_the_same` is for.

    Round 5's class needed only the first, and that is not an oversight there:
    a span that carries a grouping separator has its boundary inside the number
    by construction, so both roads are boundary questions. A span that carries
    a whole word does not.
    """
    return [_word_pair(*shape) for shape in word_shapes(quote)]


def _word_pair(kind: str, source: str, start: int, end: int, cited: str) -> WordPair:
    """One shape with its side. A third question joins the two above since
    round 7: the citation states other numbers than the span does - `$5M` cited
    as `M today` has lost its number with the word still whole."""
    span = source[start:end]
    forged = (span_cuts_a_word(source, start, end)
              or words_stated(span) != words_stated(cited)
              or numbers_stated(span) != numbers_stated(cited))
    return (kind, "tampering" if forged else "drift", source, start, end, cited)


def _the_word_class_under(install, pairs: List[WordPair]) -> Tuple[int, int, int, int]:
    """(honest kept, honest, forged refused, forged) with a mutation of the
    **rule's own modules** installed for the whole of the scoring.

    Neither control below can be a reading on the dial, and that is not an
    oversight: the boundary check stands above the equality fast path, so it is
    outside every rule `candidates()` offers and swapping `says_the_same`
    cannot reach it. `install` is handed nothing, does its swap, and returns the
    callable that puts it back.
    """
    from tallystick.verify import says_the_same
    undo = install()
    try:
        kept = honest = refused = forged = 0
        for _kind, side, source, start, end, cited in pairs:
            ok = the_tool_answers(says_the_same, source, start, end, cited)
            if side == "drift":
                honest += 1
                kept += ok
            else:
                forged += 1
                refused += not ok
    finally:
        undo()
    return kept, honest, refused, forged


def a_crude_word_check_loses_the_seams(pairs: List[WordPair]) -> Tuple[int, int, int, int]:
    """The class with a crude word check installed in the verdict path:
    **every** boundary between two letters is a cut.

    This is the positive control for the honest side (project §35 and §45). The
    honest shapes above are a price list only if an over-broad reading actually
    loses them; if a crude check keeps them all, the shapes are decoration and
    a narrow reading's 100% is measuring nothing. It is installed by wrapping
    `verify.cuts_a_number`, which is where the verdict path asks about a
    boundary, so what is mutated is the rule's module and not this file. The
    crude reading is the one round 5 priced at 118 of 5615 real spans.
    """
    from tallystick import verify

    def crude(text: str, a: int, b: int) -> bool:
        return any(0 < i < len(text) and text[i - 1].isalpha() and text[i].isalpha()
                   for i in (a, b))

    def install():
        shipped = verify.cuts_a_number
        verify.cuts_a_number = lambda t, a, b: shipped(t, a, b) or crude(t, a, b)

        def undo():
            verify.cuts_a_number = shipped
        return undo

    return _the_word_class_under(install, pairs)


def a_broken_seam_convention(pairs: List[WordPair]):
    """The class with the rule's seam convention turned upside down: a small
    letter after a small letter is called the seam instead of a capital after a
    small one.

    This is the word class's answer to the round-3 review, which showed that an
    instrument deciding a class with a copy of the rule's own predicate proves
    nothing (project rule 14). The mutant reads `unsafe` as two words and
    `resultsThe` as one, which is the convention upside down, and an instrument
    that cannot see that is not measuring the convention at all. It must lose
    both sides.

    Returns None when the rule keeps no seam convention to break - which is the
    state of the tree before the rule of this round is written, and is printed
    as exactly that rather than counted as a pass.
    """
    from tallystick import tokens

    if not hasattr(tokens, "_A_SEAM"):
        return None

    def install():
        shipped = tokens._A_SEAM
        tokens._A_SEAM = (shipped[0], shipped[0])

        def undo():
            tokens._A_SEAM = shipped
        return undo

    return _the_word_class_under(install, pairs)


def score_words(rule, pairs: List[WordPair]):
    """(refused of the tampering, accepted of the drift, by kind and side)."""
    refused = tampering = accepted = drift = 0
    by_kind: Dict[Tuple[str, str], List[int]] = {}
    for kind, side, source, start, end, cited in pairs:
        ok = the_tool_answers(rule, source, start, end, cited)
        row = by_kind.setdefault((kind, side), [0, 0])
        row[0] += ok
        row[1] += 1
        if side == "tampering":
            tampering += 1
            refused += not ok
        else:
            drift += 1
            accepted += ok
    return refused, tampering, accepted, drift, by_kind


# --------------------------------------------------------------------------- #
# the corpus
# --------------------------------------------------------------------------- #

def real_quotes(posted_dirs) -> List[str]:
    """Every EVIDENCE quote in the posted files, if the owner has the runs on disk."""
    out: List[str] = []
    for d in posted_dirs:
        for path in sorted(glob.glob(os.path.join(d, "posted", "*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            for entry in doc.get("entries", []):
                q = entry.get("quoted_span", "")
                if q and str(entry.get("account", "")).startswith("EVIDENCE:"):
                    out.append(q)
    return out


def repo_sentences() -> List[str]:
    """Fallback a fresh clone can run (project rule 13): sentences from text the
    repository itself carries."""
    out: List[str] = []
    for pattern in ("examples/*.json", "examples/logs/*.json",
                    "bench/sample/*/*.json", "bench/sample-agenthallu/*/*.json"):
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            for value in re.findall(r'"((?:[^"\\]|\\.){40,600})"', text):
                value = value.replace('\\"', '"').replace("\\n", " ")
                for sentence in re.split(r"(?<=[.!?])\s+", value):
                    if 30 < len(sentence) < 500 and re.search(r"[A-Za-z]", sentence):
                        out.append(sentence)
    return out


def build(quotes: List[str]) -> Tuple[List[Pair], List[Pair], List[Tuple[str, Pair]]]:
    """All pairs, with the ones the fast path answers held apart from the ones
    the rule is asked about - and **nothing thrown away**.

    A pair whose sides are equal after `normalize` never reaches the rule in
    `verify_entry`: the equality check above it answers first. Scoring such a
    pair as the rule's would credit the rule with a decision it does not make,
    which is what the third return value keeps it from doing. Dropping it
    instead is what the round-4 review found: 44,920 pairs of the row - the
    whole of the space, at both edges, in both directions - left the count in
    `build`, took four shapes off the printed table with them, and the tool's
    answer on them was never looked at by anybody. The third value is now the
    pairs themselves, with the side each one asserts, and `main` prints them
    with the reason and says where the same question is asked of the tool
    instead: `span_pairs`, which reads the source and the two offsets that
    `normalize` cannot fold.
    """
    drift: List[Pair] = []
    tamper: List[Pair] = []
    fast_path: List[Tuple[str, Pair]] = []

    def keep(pair: Pair, bucket: List[Pair]) -> None:
        if normalize(pair[2]) != normalize(pair[3]):
            bucket.append(pair)
        else:
            fast_path.append(("drift" if bucket is drift else "tampering", pair))

    for quote in quotes:
        for pair in drift_pairs(quote):
            keep(pair, drift)
        for pair in tampering_pairs(quote):
            keep(pair, tamper)
        # The row of marks that may differ, asked at both edges against a
        # digit. Each pair carries the side its own name states, so nothing
        # here is a judgement made twice.
        for pair in marks_against_a_digit(quote):
            keep(pair, drift if pair[0].endswith("the numbers are the same") else tamper)
    return drift, tamper, fast_path


# --------------------------------------------------------------------------- #
# the other side: values in the output of real tools
# --------------------------------------------------------------------------- #

# The review of round 8 found the round-8 reading refusing the value of compact
# JSON (`{"count":42}` cited as `42`), a field of CSV, `x=5` - and none of it
# showed here, because every class above is built on the prose of the quotes
# and on marks planted in a template. For an auditor of agent traces the
# output of tools is daily material, not an edge. Asked, the 1044 values the
# output of real tools holds in the two published runs came back 577 refused
# at f336415 (55%), 502 of them the value of a `key=value` pair.
#
# The value is found by the grammar of the format it is written in and not by
# any reading of a number: a JSON number after `"key":`, a Python dict's number
# after `'key':`, `key=number`, a cell of a Markdown table, a field of a CSV
# line. The span is the value as it stands and the citation is the span, so
# every one of them is honest by construction and a tool that refuses it
# raises a false alarm. The corpus has no compact JSON at all, so every object
# the same output holds that parses is set again compactly
# (`json.dumps(separators=(",", ":"))`) and its values are asked too: a
# derived set, counted apart.
#
# One kind is named rather than scored, by the owner's decision of round 9:
# a member of a list of numbers set with commas and no spaces (`3,4,7`,
# `[17,18,19]`). A comma between two digits keeps them one number (`7,000`),
# and nothing in `67,163` tells a list from a group.

_JSON_NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_VALUE_GRAMMARS = {
    "json": re.compile(r'"[^"\n]{1,60}"\s*:\s*(' + _JSON_NUMBER + r')(?=\s*[,}\]]|\s*$)',
                       re.M),
    "python dict": re.compile(r"'[^'\n]{1,60}'\s*:\s*(" + _JSON_NUMBER + r")(?=\s*[,}\]])"),
    "key=value": re.compile(r"(?<![\w=])[A-Za-z_][\w.]{0,30}=(-?\d+(?:\.\d+)*)"
                            r"(?=[\s&;,)'\"]|$)", re.M),
    "table cell": re.compile(r"(?<=\|) *(-?\d[\d,.]*%?) *(?=\|)"),
}
_CSV_FIELD = re.compile(_JSON_NUMBER + r'|"[^"]*"|[A-Za-z_][\w.-]*')

#: Tool output this repository carries, so a stranger without the corpus still
#: asks the class (project rule 13). Written in the forms the corpus has, and
#: compact JSON, which it has not.
BUILT_IN_TOOL_OUTPUT = [
    '{\n  "width": 1600,\n  "height": 800,\n  "delta": -3.5\n}',
    '{"city":"Oslo","count":42,"temps":{"min":-4,"max":12.5}}',
    "[{'result_id': 1, 'title': 'Cuneiform'}, {'result_id': 2, 'score': 0.93}]",
    "https://example.org/index.php?title=Cuneiform&action=edit&section=1)",
    "| Year | Count | Share |\n|---|---|---|\n| 2011 | 5,200 | 12.5% |",
    'city,population,rank\n"Paris",2161000,1\n"Oslo",709000,2',
    "x=5 y=-2 ratio=0.75",
]


def _values_in(text: str, where: str) -> List[Tuple[str, str, str, int, int]]:
    out, seen = [], set()
    for fmt, grammar in _VALUE_GRAMMARS.items():
        for m in grammar.finditer(text):
            if (m.start(1), m.end(1)) not in seen:
                seen.add((m.start(1), m.end(1)))
                out.append((fmt, where, text, m.start(1), m.end(1)))
    for m in re.finditer(r"^[^\s,][^\n]*$", text, re.M):
        fields = m.group(0).split(",")
        if len(fields) < 3 or not all(_CSV_FIELD.fullmatch(f) for f in fields):
            continue
        if re.fullmatch(r"\d{1,3}", fields[0]) and all(re.fullmatch(r"\d{3}", f)
                                                     for f in fields[1:]):
            continue        # 948,109,639,680: one number, not a line of CSV
        pos = m.start()
        for f in fields:
            if re.fullmatch(_JSON_NUMBER, f) and (pos, pos + len(f)) not in seen:
                seen.add((pos, pos + len(f)))
                out.append(("csv", where, text, pos, pos + len(f)))
            pos += len(f) + 1
    return out


def _parsed_objects(text: str):
    """Every JSON object or Python literal in `text` that parses whole."""
    import ast
    for m in re.finditer(r"[\[{]", text):
        depth, i = 0, m.start()
        for j in range(i, min(len(text), i + 20000)):
            if text[j] in "[{":
                depth += 1
            elif text[j] in "]}":
                depth -= 1
                if depth == 0:
                    for load in (json.loads, ast.literal_eval):
                        try:
                            yield load(text[i:j + 1])
                            break
                        except Exception:
                            pass
                    break


def tool_output_values(posted_dirs) -> Tuple[List, List, str]:
    """(values as the output reads, values of the same output set compactly,
    where the material came from)."""
    texts: Dict[str, str] = {}
    for d in posted_dirs:
        for path in sorted(glob.glob(os.path.join(d, "posted", "*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            for art in doc.get("artifacts", []):
                if art.get("kind") == "tool_result" and isinstance(art.get("content"), str):
                    texts.setdefault(art["content"],
                                     f"{os.path.basename(path)}:{art.get('artifact_id')}")
    origin = f"the tool output of the posted runs, {len(texts)} distinct texts"
    if not texts:
        texts = {t: f"built-in {i}" for i, t in enumerate(BUILT_IN_TOOL_OUTPUT)}
        origin = f"{len(texts)} built-in tool outputs (no posted runs on disk)"
    as_read: List = []
    for text, where in texts.items():
        as_read += _values_in(text, where)
    compact: List = []
    seen = set()
    number = re.compile(r"(?<=[:\[,])(" + _JSON_NUMBER + r")(?=[,}\]])")
    for text, where in texts.items():
        for obj in _parsed_objects(text):
            try:
                c = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if c in seen or len(c) > 20000:
                continue
            seen.add(c)
            for m in number.finditer(c):
                if c[:m.start()].count('"') % 2 == 0:
                    compact.append(("compact JSON", where, c, m.start(1), m.end(1)))
    return as_read, compact, origin


def a_member_of_a_list_of_numbers(text: str, start: int, end: int) -> bool:
    """The named kind: a comma and a digit on either side of the value."""
    return bool(re.search(r"\d,$", text[max(0, start - 2):start])
                or re.match(r",-?\d", text[end:end + 3]))


def score_tool_output(rule, values):
    """(accepted, scored, named and refused, named, refused by format and why)."""
    ok = n = named_refused = named = 0
    misses: Dict[str, List] = {}
    for fmt, where, text, start, end in values:
        closes, why = the_tool_answers_why(rule, text, start, end, text[start:end])
        if a_member_of_a_list_of_numbers(text, start, end):
            named += 1
            named_refused += not closes
            continue
        n += 1
        ok += closes
        if not closes:
            misses.setdefault(fmt, []).append((where, why, text[max(0, start - 24):start],
                                               text[start:end], text[end:end + 12]))
    return ok, n, named_refused, named, misses


# --------------------------------------------------------------------------- #
# the proposer, asked the same question as the gate
# --------------------------------------------------------------------------- #

# The review of round 8 (its section 2.3, and mutants Y04 and Y11): the
# proposer places spans with `tokens.cuts_number` on the raw text and falls
# back to a match on words that asks nothing about numbers at all, and no
# instrument asked it anything. It placed `5.2 % lower` in `margin - 5.2 %
# lower` where the gate refuses it, and a mutant that stopped it reading the
# head of a line moved where it placed a span and was caught by no one.
#
# Every span pair of the classes built by hand is put to it: the citation is
# located in its source with an empty `taken`, and three things must hold -
# the gate accepts whatever span the proposer places; an honest citation is
# placed exactly where the pair declares it; a forged one is never placed at
# the forged offsets.

def proposer_answers(pairs) -> Tuple[int, int, List]:
    """(held, asked, what failed)."""
    from tallystick import verify
    from tallystick.propose.pipeline import _locate_tolerant
    from tallystick.tokens import core
    held = asked = 0
    failed = []
    for kind, side, source, start, end, cited in pairs:
        if not cited.strip():
            continue
        asked += 1
        placed = _locate_tolerant(source, cited, [])
        problems = []
        if placed is not None:
            closes, why = the_tool_answers_why(verify.says_the_same, source,
                                               placed[0], placed[1], cited)
            if not closes:
                problems.append(f"places {placed}, which the gate refuses ({why})")
        # Compared as cores: a span with the full stop after it and one
        # without are the same place (`core` drops separators at the edges).
        want = core(source, (start, end))
        got = core(source, placed) if placed is not None else None
        if side == "drift" and got != want:
            problems.append(f"places {placed} where the honest span is {want}")
        if side == "tampering" and got == want:
            problems.append(f"places the forged span {want}")
        if problems:
            failed.append((kind, side, "; ".join(problems)))
        else:
            held += 1
    return held, asked, failed


# --------------------------------------------------------------------------- #
# the proposer, asked about a quote its source does not hold
# --------------------------------------------------------------------------- #
#
# Every class above builds a citation that is somewhere in its source and asks
# whether the boundary around it is drawn right; `proposer_answers` does the
# same for the proposer. None asks what the proposer does when the model quotes
# something the source does not say. The last review asked it once: the source
# reads `a loss of $5 million`, the model quotes `-$5 million`, and the locate
# on words places the quote on `$5 million` - the minus is a separator to the
# word match - and writes that slice into the trace as the model's quote. The
# audit then closes the books, because the slice is really at its address. The
# proposer's own docstring promises the opposite: a match on words that
# forgives punctuation and case "and never a changed character".
#
# The right answer to every quote below is "not placed": the model's number is
# not in the source, with its sign or its digits, and a span somewhere else is
# not a place for it. Beside them stand the controls the tolerant locate exists
# for - the same numbers with a full stop or quotation marks the model added -
# which must be placed, on the span declared, or a rule that refuses every word
# match would pass. And quotes that are in the source exactly, which any rule
# must place.

#: (kind, source, the model's quote). The source holds no text the quote says.
QUOTES_THE_SOURCE_DOES_NOT_HOLD: List[Tuple[str, str, str]] = [
    # a sign the model added in front of a number the source states unsigned
    ("a minus added in front of a currency (the review's case)",
     "The quarter closed with a loss of $5 million on the books.", "-$5 million"),
    ("a minus and a space added in front of a currency",
     "The quarter closed with a loss of $5 million on the books.", "- $5 million"),
    ("an en dash added in front of a currency",
     "The quarter closed with a loss of $5 million on the books.", "–$5 million"),
    ("an em dash added in front of a currency",
     "The quarter closed with a loss of $5 million on the books.", "—$5 million"),
    ("a minus sign U+2212 added in front of a currency",
     "The quarter closed with a loss of $5 million on the books.", "−$5 million"),
    ("a plus added in front of a currency",
     "The quarter closed with a gain of $5 million on the books.", "+$5 million"),
    ("a minus added in front of a euro",
     "The fee was €5 today.", "-€5"),
    ("a minus added inside a longer quote",
     "The quarter closed with a loss of $5 million on the books.", "a loss of -$5 million"),
    ("a plus added in front of a share",
     "Sales rose 5% in May.", "+5%"),
    ("a minus sign U+2212 added in front of a number",
     "The index fell 5 points.", "−5"),
    ("a minus added in front of a number",
     "The index fell 5 points.", "-5"),
    ("a minus added in front of a decimal point",
     "The rate was .5 percent.", "-.5"),
    ("a minus added in front of a decimal point, with a word",
     "The rate was .5 percent.", "-.5 percent"),
    ("a minus added a space in front of a number, mid-line",
     "The margin was 5.2 % lower.", "- 5.2 %"),
    ("a minus added in front of a bracketed share",
     "Margin fell (5%) overall.", "-(5%)"),
    # a digit the model changed
    ("a digit changed in a sum of money",
     "The quarter closed with a loss of $5 million on the books.", "$6 million"),
    ("a digit changed in a number grouped by a space",
     "In all 66 400 people came.", "66 300"),
    ("a digit changed in a number grouped by a space, with a word",
     "In all 66 400 people came.", "66 300 people"),
    ("a digit changed after a decimal point",
     "Sales rose 5.2% in May.", "5.3%"),
    ("a digit changed in a year",
     "The plant opened in 1998 and closed later.", "opened in 1999"),
    # a sign the source states and the model left out
    ("a minus left out in front of a currency",
     "The quarter closed with a loss of -$5 million on the books.", "$5 million"),
    ("a minus sign U+2212 left out in front of a currency",
     "The quarter closed with a loss of −$5 million on the books.", "$5 million"),
    ("a minus left out in front of a share",
     "Sales fell -5% in May.", "5%"),
    ("a minus left out in front of a decimal point",
     "The rate was -.5 percent.", ".5 percent"),
    ("a plus left out in front of a share",
     "Sales rose +5% in May.", "5%"),
    ("a minus standing apart left out, mid-line",
     "The margin - 5.2 % lower than planned.", "5.2 % lower"),
    ("a minus standing apart left out in front of a currency, mid-line",
     "The result was - $5 million this year.", "$5 million this year"),
    # a sign the model turned over
    ("a plus turned into a minus",
     "Sales rose +5% in May.", "-5%"),
    ("a minus turned into a plus",
     "Sales fell -5% in May.", "+5%"),
    ("a minus turned into a plus in front of a currency",
     "The quarter closed with a loss of -$5 million on the books.", "+$5 million"),
]

#: (kind, source with `‸` at both ends of the honest span, the model's quote).
QUOTES_THE_SOURCE_HOLDS: List[Tuple[str, str, str]] = [
    # the drift the tolerant locate exists for
    ("a full stop added after a sum of money",
     "a loss of ‸$5 million‸ on the books.", "$5 million."),
    ("quotation marks around a sum of money",
     "a loss of ‸$5 million‸ on the books.", "\"$5 million\""),
    ("curly quotation marks around a sum of money",
     "a loss of ‸$5 million‸ on the books.", "“$5 million”"),
    ("quotation marks and a comma around a sum of money",
     "a loss of ‸$5 million‸ on the books.", "'$5 million',"),
    ("quotation marks and a full stop around a signed share",
     "Sales fell ‸-5%‸ in May.", "\"-5%.\""),
    ("a full stop added after a share",
     "Sales rose ‸5%‸ in May.", "5%."),
    ("quotation marks and a full stop around a grouped number",
     "In all ‸66 400 people‸ came.", "\"66 400 people.\""),
    ("a full stop added after a sentence with a share",
     "‸Revenue fell 12% in the quarter‸", "Revenue fell 12% in the quarter."),
    ("a full stop added after a sentence with a year and a hyphen",
     "‸the well-known result of 2019‸", "the well-known result of 2019."),
    ("a comma left out between two words, a number after",
     "‸Revenue, in total, fell 12%‸ in May.", "Revenue in total fell 12%"),
    ("a capital changed, a number after",
     "‸Revenue fell 12%‸ in May.", "revenue fell 12%."),
    # a hyphen between a word and a number is a dash variant, not a sign
    ("a hyphen between a word and a number left out",
     "‸COVID-19 cases‸ rose.", "COVID 19 cases"),
    ("a hyphen between a word and a number added",
     "‸COVID 19 cases‸ rose.", "COVID-19 cases."),
    # exact: any rule must place these
    ("exact: a signed sum of money",
     "a loss of ‸-$5 million‸ on the books.", "-$5 million"),
    ("exact: an unsigned sum of money",
     "a loss of ‸$5 million‸ on the books.", "$5 million"),
    ("exact: a plus in front of a share",
     "Sales rose ‸+5%‸ in May.", "+5%"),
    ("exact: a minus in front of a decimal point",
     "The rate was ‸-.5 percent‸.", "-.5 percent"),
    ("exact: a minus sign U+2212 in front of a number",
     "The index fell ‸−5 points‸.", "−5 points"),
    ("exact: a grouped number",
     "In all ‸66 400 people‸ came.", "66 400 people"),
]

#: Named, not scored: honest quotes around a sign whose answer is printed every
#: run and held to no threshold. The first three were found while building the
#: class above: an honest quote of a signed number with a mark added is placed
#: nowhere, because the word match drops a sign that does not stand against a
#: digit, the span it finds then cuts the signed number, and `cuts_a_number`
#: refuses it. Placing nothing is the safe error - the credit is dropped and
#: logged - and it is not the question this class asks. The last is the other
#: way round: a list bullet the model kept, with a full stop added. The source
#: reads the dash as a bullet (it opens a line); the quote alone cannot say
#: whether its opening `- ` is a bullet or a sign, and a rule that reads it as a
#: sign places it nowhere. Named here, before any rule, so that its price is
#: printed rather than discovered.
SIGN_QUOTES_NAMED_NOT_SCORED: List[Tuple[str, str, str]] = [
    ("a full stop added after a signed sum of money",
     "a loss of ‸-$5 million‸ on the books.", "-$5 million."),
    ("quotation marks around a signed sum of money",
     "a loss of ‸-$5 million‸ on the books.", "\"-$5 million\""),
    ("quotation marks around a signed decimal",
     "The rate was ‸-.5 percent‸.", "\"-.5 percent\""),
    ("a list bullet kept, a full stop added",
     "Totals:\n‸- 5 apples‸\n- 6 pears", "- 5 apples."),
]


def _between(template: str) -> Tuple[str, int, int]:
    """A source with `‸` at both ends of a span, and the span."""
    a = template.index(_BOUNDARY)
    b = template.index(_BOUNDARY, a + 1) - 1
    return template.replace(_BOUNDARY, ""), a, b


def _check_the_absent_are_absent() -> None:
    """Before any measuring: no quote of the absent class stands whole in its
    source - it is not there at all, or every place it is found cuts a number
    of the source, as this instrument reads one (`span_cuts_a_number`, not the
    rule's), or leaves a sign of the source behind it (`_sign`, with spaces
    and a currency or a decimal point between) - and every honest span is
    where it is declared. A pair that fails this measures nothing."""
    def leaves_a_sign(src: str, i: int) -> bool:
        k = i - 1
        while k >= 0 and (src[k].isspace() or src[k] == "."
                          or unicodedata.category(src[k]) == "Sc"):
            k -= 1
        return k >= 0 and _sign(src[k]) and not (k > 0 and _letter(src[k - 1]))

    def stands_whole(src: str, q: str) -> bool:
        return any(not span_cuts_a_number(src, m.start(), m.end())
                   and not (q[:1] and not _sign(q[0]) and leaves_a_sign(src, m.start()))
                   for m in re.finditer(re.escape(q), src))
    bad = [k for k, src, q in QUOTES_THE_SOURCE_DOES_NOT_HOLD
           if stands_whole(src, q) or stands_whole(normalize(src), normalize(q))]
    for k, template, q in QUOTES_THE_SOURCE_HOLDS + SIGN_QUOTES_NAMED_NOT_SCORED:
        src, a, b = _between(template)
        if not (0 <= a < b <= len(src)) or not src[a:b].strip():
            bad.append(k)
    if bad:
        print("the class of absent quotes is built wrong: " + "; ".join(bad),
              file=sys.stderr)
        raise SystemExit(2)


def proposer_on_quotes_not_there(locate=None):
    """((not placed, of absent), (placed where declared, of present), failed,
    named). `locate` defaults to the proposer's `_locate_tolerant`; the two
    controls below pass their own."""
    from tallystick.tokens import core
    if locate is None:
        from tallystick.propose.pipeline import _locate_tolerant as locate
    failed: List[Tuple[str, str, str]] = []
    refused = 0
    for kind, src, q in QUOTES_THE_SOURCE_DOES_NOT_HOLD:
        placed = locate(src, q, [])
        if placed is None:
            refused += 1
        else:
            failed.append((kind, "absent", f"{q!r} placed on "
                           f"{src[placed[0]:placed[1]]!r} {placed}"))
    kept = 0
    for kind, template, q in QUOTES_THE_SOURCE_HOLDS:
        src, a, b = _between(template)
        placed = locate(src, q, [])
        got = core(src, placed) if placed is not None else None
        if got == core(src, (a, b)):
            kept += 1
        else:
            where = repr(src[placed[0]:placed[1]]) if placed else "nowhere"
            failed.append((kind, "present", f"{q!r} placed {where} "
                           f"where the honest span is {src[a:b]!r}"))
    named = []
    for kind, template, q in SIGN_QUOTES_NAMED_NOT_SCORED:
        src, a, b = _between(template)
        placed = locate(src, q, [])
        named.append((kind, q, src[placed[0]:placed[1]] if placed else None, src[a:b]))
    return ((refused, len(QUOTES_THE_SOURCE_DOES_NOT_HOLD)),
            (kept, len(QUOTES_THE_SOURCE_HOLDS)), failed, named)


def _a_locate_that_places_nothing_but_exact(haystack, needle, taken):
    """Control: the strict locate alone. It refuses every absent quote and
    loses the drift, so it must fail the present side."""
    from tallystick.propose.pipeline import _locate
    return _locate(haystack, needle, taken)


def _a_locate_that_places_everything(haystack, needle, taken):
    """Control: the whole source, whatever was quoted. It must fail the
    absent side, or that side cannot tell a locate that asks from one that
    does not."""
    return (0, len(haystack))


# --------------------------------------------------------------------------- #
# two boundaries that must fail: one that reads too little, one too much
# --------------------------------------------------------------------------- #

def _numeral_at(text: str, i: int) -> bool:
    return 0 <= i < len(text) and _numeral(text[i])


def a_boundary_of_digits_alone(text: str, start: int, end: int) -> bool:
    """Weakened: a span cuts a number only where a numeral stands on both
    sides of a boundary. It reads no sign, no currency, no separator."""
    return (_numeral_at(text, start - 1) and _numeral_at(text, start)) or \
        (_numeral_at(text, end - 1) and _numeral_at(text, end))


def a_boundary_of_every_mark(text: str, start: int, end: int) -> bool:
    """Widened: any character that is not a letter or whitespace next to a
    numeral at a boundary makes the numeral part of a bigger number."""
    def mark(i: int) -> bool:
        return 0 <= i < len(text) and not (_letter(text[i]) or text[i].isspace())
    return ((_numeral_at(text, start) and mark(start - 1))
            or (_numeral_at(text, end - 1) and mark(end))
            or a_boundary_of_digits_alone(text, start, end))


def with_the_boundary(check, score_fn, *args):
    """`score_fn(*args)` with `verify.cuts_a_number` swapped for `check`."""
    from tallystick import verify
    shipped = verify.cuts_a_number
    verify.cuts_a_number = check
    try:
        return score_fn(*args)
    finally:
        verify.cuts_a_number = shipped


# --------------------------------------------------------------------------- #
# the rules, including the two that must fail
# --------------------------------------------------------------------------- #

def with_the_number_convention_widened(rule):
    """The candidate rule with one thing broken in it: a grouping comma that
    takes two digits instead of three.

    This is the mutant the round-3 review built to show that the instrument was
    measuring its own copy of the rule's reading. It is carried here so the
    answer is on the record at every run rather than in a report: the mutant
    reads `since 1998, 18 people` as one number and `since 1998, 183 people` as
    two, which is the convention upside down, and an instrument that cannot see
    that is not measuring the convention at all. It must not pass.

    The rule's module-level pattern is swapped for the duration of each call, so
    nothing here reimplements the rule - only the one line the convention lives
    on is different.
    """
    from tallystick import tokens

    if not hasattr(tokens, "_ONE_NUMBER"):
        raise SystemExit("the rule no longer keeps its number convention in "
                         "tokens._ONE_NUMBER: this control mutates nothing and "
                         "has to be rewritten before the run means anything")

    two_digit_groups = re.compile(r"\d(?:\.\d|,\d{2}(?!\d)|:\d)")

    def broken(a: str, b: str) -> bool:
        shipped = tokens._ONE_NUMBER
        tokens._ONE_NUMBER = two_digit_groups
        try:
            return rule(a, b)
        finally:
            tokens._ONE_NUMBER = shipped

    return broken


#: One pair on which the shipped convention and the two-digit mutant have to
#: disagree, whatever corpus the instrument is given. A control that answers
#: exactly what it mutates is not a control, and a small `--limit` must not be
#: able to turn it into one quietly.
#:
#: The head is two digits, not a year. Until round 7 the pair was `since 1998,
#: 18 people`, and it disagreed only because the space behind `1998` grouped;
#: once a space groups behind one to three digits and no more, the rule and
#: the mutant both read `1998 18` as two numbers and the check reported the
#: instrument broken. The pair below disagrees under the old convention and
#: under the new one.
_THE_MUTANT_MUST_DIFFER_HERE = ("in 98, 18 people", "in 98 18 people")


def candidates() -> Dict[str, Callable[[str, str], bool]]:
    from tallystick.verify import says_the_same

    def v081_content_signature(a: str, b: str) -> bool:
        """The shipped v0.8.1 rule, kept as a reading on the dial: letters,
        digits, and any mark standing between two digits; everything else
        dropped."""
        def content(s: str) -> str:
            keep = []
            for i, ch in enumerate(s):
                if ch.isalnum():
                    keep.append(ch)
                elif 0 < i < len(s) - 1 and s[i - 1].isdigit() and s[i + 1].isdigit():
                    keep.append(ch)
            return "".join(keep)
        return bool(content(a)) and content(a) == content(b)

    return {
        "CONTROL always accept": lambda a, b: True,
        "CONTROL always refuse": lambda a, b: False,
        "v0.8.1 content signature": v081_content_signature,
        "v0.8.3 sequence of tokens": v083_sequence_of_tokens,
        "CONTROL the number convention widened":
            with_the_number_convention_widened(says_the_same),
        "candidate (tallystick.verify.says_the_same)": says_the_same,
    }


# The rule as it stood at b251397, copied here rather than described, so that the
# dial has a third reading and the claim "each rule refuses more than the one
# before it" is a measurement (project rule 37). It reads a string as a sequence
# of words - each word carrying whatever is stuck to a number in it - plus the
# marks that end a sentence, with whole Unicode categories counting as setting.
# That last part is what the sweep below prices: every dash and every bracket was
# setting, wherever it stood.

#: The v0.8.3 rule's own reading of a word boundary, archived with it. This is
#: a copy on purpose and of a rule that is no longer shipped: it is a reading on
#: the dial, not a property this instrument believes about a character.
_V083_SEP_CATS = frozenset({"Pd", "Ps", "Pe", "Pi", "Pf", "Pc", "Zs", "Zl", "Zp"})
_V083_SEP_CHARS = frozenset(".,;:!?'\"`…¡¿·")


def _v083_is_glue(ch: str) -> bool:
    return not ch.isalnum() and not ch.isspace() and not (
        ch in _V083_SEP_CHARS or unicodedata.category(ch) in _V083_SEP_CATS)


def _v083_is_mark(token: str) -> bool:
    return bool(token) and all(ch in ".!?\u2026" for ch in token)


def _v083_joins(left: str, right: str) -> bool:
    return not _v083_is_mark(left) and (_v083_is_glue(left[-1]) or _v083_is_glue(right[0]))


def _v083_marks(gap: str) -> List[str]:
    return [m.group(0).replace("\u2026", "...")
            for m in re.finditer("[.!?\u2026]+", gap)]


def _v083_reading(text: str) -> List[str]:
    out: List[str] = []
    end = 0
    for a, b, word in words(text):
        marks = _v083_marks(text[end:a])
        if len(word) > 2 and word.startswith("(") and word.endswith(")"):
            word = word[1:-1]
        if out and not marks and _v083_joins(out[-1], word):
            out[-1] += word
        else:
            out.extend(marks)
            out.append(word)
        end = b
    out.extend(_v083_marks(text[end:]))
    return out


def _v083_without_end_marks(seq: List[str]):
    lead, tail = 0, len(seq)
    while lead < tail and _v083_is_mark(seq[lead]):
        lead += 1
    while tail > lead and _v083_is_mark(seq[tail - 1]):
        tail -= 1
    return ("".join(seq[:lead]) or None, seq[lead:tail], "".join(seq[tail:]) or None)


def _v083_same_mark(here, there) -> bool:
    if here is None or there is None:
        return True
    return here.startswith(there) or there.startswith(here)


def v083_sequence_of_tokens(a: str, b: str) -> bool:
    if a == b:
        return True
    here, there = _v083_reading(a), _v083_reading(b)
    if here == there:
        return True
    a_lead, a_body, a_tail = _v083_without_end_marks(here)
    b_lead, b_body, b_tail = _v083_without_end_marks(there)
    if a_body != b_body:
        return False
    return _v083_same_mark(a_lead, b_lead) and _v083_same_mark(a_tail, b_tail)


#: The characters the sweep walks: every punctuation and symbol codepoint below
#: U+3000. The question it asks is not "does this rule handle the cases we
#: thought of" but "how many characters can be dropped from a quote without this
#: rule noticing", which is the question four rules in a row were never asked.
SWEPT = [chr(cp) for cp in range(0x21, 0x3000)
         if unicodedata.category(chr(cp))[0] in ("P", "S")]

#: What a rule is allowed to forgive, stated here and not imported, so that a
#: rule which widens its own list fails the sweep instead of passing it.
MAY_BE_DROPPED_BESIDE_A_SPACE = {",", '"', "'"}
MAY_BE_DROPPED_AT_AN_EDGE = MAY_BE_DROPPED_BESIDE_A_SPACE | {".", "!", "?", "\u2026"}


def characters_forgiven(rule) -> Tuple[int, List[str]]:
    """How many of `SWEPT` this rule lets a recorder drop, beside a space or at
    the end of a quote, beyond the ones the rule itself names."""
    forgiven = []
    for ch in SWEPT:
        folded = normalize(ch)
        for source, written, allowed in (
                (f"alpha {ch} beta gamma", "alpha beta gamma",
                 MAY_BE_DROPPED_BESIDE_A_SPACE),
                (f"alpha beta gamma{ch}", "alpha beta gamma",
                 MAY_BE_DROPPED_AT_AN_EDGE)):
            if normalize(source) == normalize(written):
                continue
            if folded and all(c in allowed for c in folded):
                continue
            if rule(normalize(source), normalize(written)):
                forgiven.append(f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
                break
    return len(forgiven), forgiven


def score(rule, pairs: List[Pair]):
    accepted = 0
    by_kind: Dict[str, List[int]] = {}
    for kind, site, source, written in pairs:
        ok = bool(rule(normalize(source), normalize(written)))
        accepted += ok
        row = by_kind.setdefault(f"{kind} [{site}]", [0, 0])
        row[0] += ok
        row[1] += 1
    return accepted, len(pairs), by_kind


def main() -> int:
    ap = argparse.ArgumentParser(description="the quote gate instrument")
    ap.add_argument("--posted", nargs="*", default=["bench/work", "bench/work-agenthallu"])
    ap.add_argument("--limit", type=int, default=400, help="quotes sampled from the corpus")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", help="write the table here")
    ap.add_argument("--show-misses", action="store_true",
                    help="print the kinds each rule gets wrong")
    args = ap.parse_args()

    # Before any measuring: the hand-written list of forms against the walk
    # that generalises it. If those two disagree, nothing below means anything.
    _check_the_reading()
    _check_the_word_reading()
    _check_the_absent_are_absent()
    print(f"the reading of a number: {len(HOW_A_NUMBER_IS_WRITTEN)} forms written out by "
          f"hand agree with\n        the walk that generalises them; neither shares code "
          f"with the rule")
    print(f"the reading of a word:   {len(HOW_A_WORD_IS_WRITTEN)} forms written out by "
          f"hand agree with\n        the walk that generalises them; neither shares code "
          f"with the rule\n")

    quotes = real_quotes(args.posted)
    where = f"{len(quotes)} real EVIDENCE quotes from the posted runs"
    if not quotes:
        quotes = repo_sentences()
        where = f"{len(quotes)} sentences from text in the repository (no posted runs on disk)"
    if not quotes:
        print("no text to build a corpus from", file=sys.stderr)
        return 2
    population = len(quotes)
    if len(quotes) > args.limit:
        quotes = random.Random(args.seed).sample(quotes, args.limit)

    drift, tamper, fast_path = build(quotes)
    # Round 8: what stands against a numeral, decided by hand and checked
    # against every character the corpus puts there.
    sources = real_sources(args.posted)
    found = marks_in_the_corpus(sources)
    _check_the_marks_are_decided(found)
    examples_checked = _check_the_examples_are_real(
        [t for t in sources if not _binary(t)])
    marks = mark_shapes()
    for kind, a, b in THE_CONVENTION_MUST_NOT_WIDEN:
        tamper.append((f"the number convention widened: {kind}", "middle", a, b))
    for kind, a, b in THE_CONVENTION_MUST_NOT_NARROW:
        drift.append((f"the number convention narrowed: {kind}", "middle", a, b))
    spans: List[SpanPair] = []
    words_spans: List[WordPair] = []
    for quote in quotes:
        spans += span_pairs(quote)
        words_spans += word_pairs(quote)
    # The two sweeps are asked once, not once per quote: each is a class of
    # characters walked in full, and the material around it is fixed.
    # A span that carries the mark while the citation leaves it out is
    # tampering whether or not the mark was inside a number: the citation has
    # dropped a character the allow-list does not name. Until round 9 every
    # such mark was inside one, so the number alone decided; the bullet
    # (`007•398K`) is the first that is not.
    def _drops_content(kind: str, source: str, start: int, cited: str) -> bool:
        if not kind.endswith("the span on it"):
            return False
        dropped = source[start]
        return not (dropped.isspace() or dropped in MAY_BE_DROPPED_AT_AN_EDGE)
    swept_numbers = [(kind, "tampering" if span_cuts_a_number(source, start, end)
                      or _drops_content(kind, source, start, cited)
                      else "drift", source, start, end, cited)
                     for kind, source, start, end, cited in number_sweep()]
    swept_words = [_word_pair(*shape) for shape in word_sweep()]
    spans += swept_numbers
    words_spans += swept_words
    spans += marks
    for kind, template in WORDS_THE_REVIEW_BROKE:
        src, i = _at(template)
        words_spans.append(_word_pair(f"the review broke: {kind}", src, i, len(src), src[i:]))
    cut_spans = [p for p in spans if p[1] == "tampering"]
    whole_spans = [p for p in spans if p[1] == "drift"]
    cut_words = [p for p in words_spans if p[1] == "tampering"]
    whole_words = [p for p in words_spans if p[1] == "drift"]
    print(f"corpus: {where}; {len(quotes)} of {population} sampled (seed {args.seed})")
    print(f"        {len(tamper)} tampering pairs, {len(drift)} drift pairs, "
          f"each planted in the middle and at both edges")
    print(f"        {len(cut_spans)} spans cutting a number of their source and "
          f"{len(whole_spans)} cutting none,\n        asked of the tool as a source, "
          f"two offsets and a citation")
    print(f"        {len(cut_words)} spans cutting a word of their source and "
          f"{len(whole_words)} cutting none,\n        asked the same way")
    decided = sorted({ch for ch, _w in found})
    print(f"        of those, {len(marks)} are marks against a numeral, decided by hand: "
          f"{len(MARKS_BESIDE_A_NUMERAL)} groups;\n        the corpus puts "
          f"{len(decided)} distinct characters against a numeral at "
          f"{sum(found.values())} places in\n        {sum(not _binary(t) for t in sources)} "
          f"texts that are not binary, and every one of them is decided;\n        "
          f"{examples_checked} of the table's cells carry an example copied out of "
          f"those texts, checked\n        to be there")
    print(f"        of those, {len(swept_numbers)} and {len(swept_words)} are the two "
          f"sweeps: every whitespace,\n        punctuation, invisible and non-digit "
          f"numeral character in a number, every\n        invisible character, dash and "
          f"apostrophe in a word")
    print(f"        {len(fast_path)} pairs answered by the equality fast path of "
          f"`verify_entry`: equal after\n        normalize, so the tool accepts and the "
          f"rule is never asked. Held apart, not dropped:")
    seen: Dict[Tuple[str, str], int] = {}
    for side, pair in fast_path:
        seen[(pair[0], side)] = seen.get((pair[0], side), 0) + 1
    for (kind, side), n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"          {n:>5}  [{side}]  {kind}")
    folded_tampering = sum(n for (_k, side), n in seen.items() if side == "tampering")
    print(f"        of those, {folded_tampering} assert tampering. A pair that asserts "
          f"tampering and is\n        answered by the fast path is a forgery the rule "
          f"cannot be blamed for and the\n        tool accepts; the boundary class above "
          f"is where that question is asked of\n        the source instead of of two "
          f"strings.")
    print()

    # Round 9: the output of real tools, and the proposer. Both are the tool's
    # answer on exact slices, so they are asked once, with the shipped rule.
    from tallystick.verify import says_the_same as _the_gate
    tool_read, tool_compact, tool_origin = tool_output_values(args.posted)
    tool_values = tool_read + tool_compact
    tool_ok, tool_n, tool_named_refused, tool_named, tool_misses = score_tool_output(_the_gate, tool_values)
    tool_rate = tool_ok / tool_n if tool_n else 0.0
    p_held, p_asked, p_failed = proposer_answers(marks)
    proposer_rate = p_held / p_asked if p_asked else 0.0
    # The round of the search: quotes the source does not hold, and the
    # drift beside them. Asked of the shipped proposer and of two locates that
    # must fail, one on each side.
    (a_refused, a_n), (h_kept, h_n), a_failed, a_named = proposer_on_quotes_not_there()
    absent_rate = a_refused / a_n if a_n else 0.0
    drift_kept_rate = h_kept / h_n if h_n else 0.0
    (_xr, _xn), (x_kept, _xh), _xf, _xnm = proposer_on_quotes_not_there(
        _a_locate_that_places_nothing_but_exact)
    (e_refused, _en), (e_kept, _eh), _ef, _enm = proposer_on_quotes_not_there(
        _a_locate_that_places_everything)
    # The two boundaries that must fail, each on the side it breaks.
    weak = with_the_boundary(a_boundary_of_digits_alone, score_spans, _the_gate, spans)
    wide_spans = with_the_boundary(a_boundary_of_every_mark, score_spans, _the_gate, spans)
    wide_tools = with_the_boundary(a_boundary_of_every_mark, score_tool_output, _the_gate,
                                   tool_values)

    rows, results = [], {}
    for name, rule in candidates().items():
        d_ok, d_n, d_kinds = score(rule, drift)
        t_ok, t_n, t_kinds = score(rule, tamper)
        s_refused, s_n, s_ok, s_drift_n, s_kinds = score_spans(rule, spans)
        w_refused, w_n, w_ok, w_drift_n, w_kinds = score_words(rule, words_spans)
        drift_rate = d_ok / d_n if d_n else 0.0
        refused_rate = 1 - (t_ok / t_n if t_n else 0.0)
        span_refused_rate = s_refused / s_n if s_n else 0.0
        span_accepted_rate = s_ok / s_drift_n if s_drift_n else 0.0
        word_refused_rate = w_refused / w_n if w_n else 0.0
        word_accepted_rate = w_ok / w_drift_n if w_drift_n else 0.0
        swept, swept_names = characters_forgiven(rule)
        verdict = ("pass" if refused_rate >= THRESHOLDS["tampering_refused"]
                   and drift_rate >= THRESHOLDS["drift_accepted"]
                   and swept <= THRESHOLDS["characters_forgiven"]
                   and span_refused_rate >= THRESHOLDS["spans_through_a_number_refused"]
                   and span_accepted_rate >= THRESHOLDS["spans_between_numbers_accepted"]
                   and word_refused_rate >= THRESHOLDS["spans_through_a_word_refused"]
                   and word_accepted_rate >= THRESHOLDS["spans_between_words_accepted"]
                   and (name != "candidate (tallystick.verify.says_the_same)" or (
                       tool_rate >= THRESHOLDS["tool_output_values_accepted"]
                       and proposer_rate >= THRESHOLDS["proposer_agrees_with_the_gate"]
                       and absent_rate >= THRESHOLDS["proposer_places_no_quote_the_source_lacks"]
                       and drift_kept_rate
                       >= THRESHOLDS["proposer_places_honest_drift_where_it_stands"]))
                   else "FAIL")
        rows.append((name, drift_rate, refused_rate, swept,
                     span_accepted_rate, span_refused_rate,
                     word_accepted_rate, word_refused_rate, verdict))
        results[name] = {"drift_accepted": drift_rate,
                         "drift_accepted_of": [d_ok, d_n],
                         "tampering_refused": refused_rate,
                         "tampering_refused_of": [t_n - t_ok, t_n],
                         "characters_forgiven": swept,
                         "characters_forgiven_names": swept_names[:40],
                         "spans_through_a_number_refused": span_refused_rate,
                         "spans_through_a_number_refused_of": [s_refused, s_n],
                         "spans_between_numbers_accepted": span_accepted_rate,
                         "spans_between_numbers_accepted_of": [s_ok, s_drift_n],
                         "spans_through_a_word_refused": word_refused_rate,
                         "spans_through_a_word_refused_of": [w_refused, w_n],
                         "spans_between_words_accepted": word_accepted_rate,
                         "spans_between_words_accepted_of": [w_ok, w_drift_n],
                         "spans_by_kind": s_kinds, "words_by_kind": w_kinds,
                         "drift_by_kind": d_kinds, "tampering_by_kind": t_kinds,
                         "verdict": verdict}

    width = max(len(r[0]) for r in rows)
    print(f"{'rule':<{width}}  drift accepted  tampering refused  "
          f"characters forgiven  whole spans kept  cut spans refused  "
          f"whole words kept  cut words refused  verdict")
    # Four decimal places, not one: 267161 of 267162 pairs accepted is not
    # 100.0%, and a threshold of 1.00 that prints as met when it is not is the
    # footnote §41 forbids.
    for name, d, t, swept, s_ok, s_ref, w_ok, w_ref, verdict in rows:
        print(f"{name:<{width}}  {d:>13.4%}  {t:>16.4%}  {swept:>19}  "
              f"{s_ok:>15.4%}  {s_ref:>16.4%}  {w_ok:>15.4%}  {w_ref:>16.4%}  {verdict}")
    print(f"\n{len(SWEPT)} punctuation and symbol characters swept: for each, a quote "
          f"with it\nbeside a space and at its end, cited without it. A rule may forgive "
          f"only the\ncharacters it names - the comma, the two quotation marks, and a "
          f"sentence mark\nat an edge - and every other one is a case nobody thought of.")
    print("\nThe last two columns are the tool's answer, not the rule's: each pair there "
          "is\na source, a pair of offsets and a citation, put through "
          "`verify.verify_entry`\nwith this rule in the gate. A boundary check standing "
          "above the fast path is\noutside every rule on the dial, so these two columns "
          "move with the tool and not\nwith the reading - which is the point, and the "
          "reason the row above them cannot\nsee the forgery at all.")
    print(f"\nthresholds, named before the measurement: refuse "
          f"{THRESHOLDS['tampering_refused']:.0%} of tampering, accept "
          f"{THRESHOLDS['drift_accepted']:.0%} of the drift the rule is asked about, "
          f"forgive {THRESHOLDS['characters_forgiven']} characters it has not named, "
          f"refuse\n{THRESHOLDS['spans_through_a_number_refused']:.0%} of the spans whose "
          f"boundary cuts a number of the source and accept "
          f"{THRESHOLDS['spans_between_numbers_accepted']:.0%} of the\nspans whose "
          f"boundary cuts none, refuse "
          f"{THRESHOLDS['spans_through_a_word_refused']:.0%} of the spans whose boundary "
          f"cuts a word and\naccept {THRESHOLDS['spans_between_words_accepted']:.0%} of "
          f"the spans whose boundary cuts none")

    # --- the row of marks that may differ, at both edges ----------------- #
    cand_name = "candidate (tallystick.verify.says_the_same)"
    rule_of_the_candidate = candidates()[cand_name]
    census: Dict[Tuple[str, str, str], List[int]] = {}
    # Every pair of the row, including the ones the fast path answers. At round
    # 4 this loop ran over `drift + tamper` alone, so the four shapes whose
    # pairs fold under `normalize` - the space, at both edges, in both
    # directions - were not in the table at all: 24 lines where the sweep
    # builds 28 shapes. A shape missing from a census is not a shape that
    # passed.
    for side, (kind, _site, source, written) in (
            [("", p) for p in drift + tamper] + list(fast_path)):
        if " against a digit " not in kind:
            continue
        head, _, form = kind.partition(", and ")
        side = "drift" if form == "the numbers are the same" else "tampering"
        row = census.setdefault((head, form, side), [0, 0, 0])
        row[1] += 1
        if normalize(source) == normalize(written):
            row[0] += 1        # the tool accepts: the fast path answered
            row[2] += 1
        else:
            row[0] += bool(rule_of_the_candidate(normalize(source), normalize(written)))
    if census:
        print("\nthe row: every mark the rule may forgive, set against a digit at each "
              "edge,\ntaken away and put there, classified by what the numbers do "
              "(round-4 brief b).\n'the tool answers same quote' counts the fast path's "
              "answer where the fast path\nanswers, and the rule's where the rule is "
              "asked; it must be all of a tampering\nrow's pairs or none of a drift "
              "row's.\n")
        width = max(len(k[0]) for k in census)
        print(f"{'shape':<{width}}  {'the numbers':<24}  {'side':<9}  "
              f"tool says same  of which by the fast path")
        for (head, form, side), (same, n, fast) in sorted(census.items()):
            flag = "" if (same == n if side == "drift" else same == 0) else "   <- FAIL"
            print(f"{head:<{width}}  {form:<24}  {side:<9}  {same:>6}/{n}  "
                  f"{fast:>22}{flag}")
        print(f"\n{len(census)} shapes, {sum(n for _s, n, _f in census.values())} pairs, "
              f"{sum(f for _s, _n, f in census.values())} of them answered by the fast "
              f"path.\nWhat the fast path answers is asked again of the source and the "
              f"two offsets in\nthe boundary table below: that is where a space "
              f"grouping one number stops being\ninvisible.")

    # --- the boundary of a span, read in the source -------------------- #
    if spans:
        print("\nthe boundary: a span declared inside a number of its source, and a span "
              "whose\nboundary cuts none. The answer is the tool's - `verify_entry` on a "
              "one-document\nrun - so the fast path is part of the measurement rather "
              "than a hole in it.\n")
        by_kind = results[cand_name]["spans_by_kind"]
        width = max(len(k) for k, _side in by_kind)
        print(f"{'shape':<{width}}  {'side':<9}  tool closes the books")
        for kind, side in sorted(by_kind):
            ok, n = by_kind[(kind, side)]
            flag = "" if (ok == n if side == "drift" else ok == 0) else "   <- FAIL"
            print(f"{kind:<{width}}  {side:<9}  {ok:>7}/{n}{flag}")

    # --- the boundary of a span, read as a word of the source ---------- #
    if words_spans:
        print("\nthe word: a span declared inside a word of its source, and a span whose "
              "boundary\ncuts none. Same reading as the table above with letters in place "
              "of digits, and\nthe same answer - the tool's, through `verify_entry`. The "
              "honest shapes are the\nprice list: `SCRAPE_SEAMS` holds ten seams copied "
              "out of the sources and one\nbuilt on their shape in Cyrillic, and the 118 "
              "spans round 5 counted are of\ntheir first two kinds - 92 the letter of a "
              "literal `\\n` and a capital, 26 a\nword run into a capitalised word.\n")
        by_kind = results[cand_name]["words_by_kind"]
        width = max(len(k) for k, _side in by_kind)
        print(f"{'shape':<{width}}  {'side':<9}  tool closes the books")
        for kind, side in sorted(by_kind):
            ok, n = by_kind[(kind, side)]
            flag = "" if (ok == n if side == "drift" else ok == 0) else "   <- FAIL"
            print(f"{kind:<{width}}  {side:<9}  {ok:>7}/{n}{flag}")

    # --- the output of real tools --------------------------------------- #
    print(f"\nthe other side: values in the output of real tools ({tool_origin}),"
          f"\nfound by the grammar of their format and cited as they stand. Every one "
          f"must\nclose the books; a member of a list of numbers set with commas is "
          f"named, not scored.\n")
    by_fmt: Dict[str, List[int]] = {}
    for fmt, _w, text, a, b in tool_values:
        row = by_fmt.setdefault(fmt, [0, 0])
        if not a_member_of_a_list_of_numbers(text, a, b):
            row[1] += 1
    for fmt, items in sorted(tool_misses.items()):
        by_fmt[fmt][0] += len(items)
    for fmt, (bad, n) in sorted(by_fmt.items()):
        print(f"  {fmt:<14} refused {bad:>5} of {n:<5}" + ("   <- FAIL" if bad else ""))
    print(f"  {'':<14} accepted {tool_ok} of {tool_n} ({tool_rate:.4%}); named: {tool_named_refused} "
          f"of {tool_named} list members refused")
    for fmt, items in sorted(tool_misses.items()):
        for where, why, before, value, after in items[:4]:
            print(f"    {fmt}: {where}: {why}  {before!r}|{value!r}|{after!r}")
        if len(items) > 4:
            print(f"    {fmt}: ... {len(items) - 4} more")

    # --- the proposer ----------------------------------------------------- #
    print(f"\nthe proposer: the {p_asked} span pairs built by hand, located in their "
          f"source by\n`propose.pipeline._locate_tolerant`. It must place nothing the "
          f"gate refuses, every\nhonest citation where it stands and no forged one "
          f"where it was forged.\n")
    print(f"  held {p_held} of {p_asked} ({proposer_rate:.4%})")
    for kind, side, why in p_failed[:40]:
        print(f"    [{side}] {kind}: {why}   <- FAIL")
    if len(p_failed) > 40:
        print(f"    ... {len(p_failed) - 40} more")

    # --- the proposer, on a quote its source does not hold --------------- #
    print(f"\nthe proposer, on a quote its source does not hold: "
          f"{a_n} quotes whose sign or digit\nthe source does not state, located by "
          f"`_locate_tolerant`. Every one must be\nplaced nowhere. Beside them {h_n} "
          f"the source does hold - a full stop, quotation\nmarks, a comma or a capital "
          f"the model changed, or nothing - and every one must\nbe placed where it "
          f"stands.\n")
    print(f"  placed nowhere        {a_refused} of {a_n} ({absent_rate:.4%})"
          + ("" if a_refused == a_n else "   <- FAIL"))
    print(f"  placed where it stands {h_kept} of {h_n} ({drift_kept_rate:.4%})"
          + ("" if h_kept == h_n else "   <- FAIL"))
    for kind, side, why in a_failed:
        print(f"    [{side}] {kind}: {why}   <- FAIL")
    print("\n  named, not scored (SIGN_QUOTES_NAMED_NOT_SCORED), each honest:")
    for kind, q, got, want in a_named:
        print(f"    {kind}: {q!r} placed on {got!r} (the honest span is {want!r})")

    # --- named, not scored ---------------------------------------------- #
    from tallystick.verify import says_the_same as _shipped
    print("\nnamed, not scored: the price of decisions the owner made with the number "
          "beside\nthem. Each is asked of the tool on every run and printed; none is in "
          "a threshold,\nso a reading that closes one later is not failed for it "
          "(`NAMED_NOT_SCORED`).\n")
    width = max(len(n[0]) for n in NAMED_NOT_SCORED)
    print(f"{'shape':<{width}}  {'by meaning':<10}  tool closes the books")
    named = {}
    for what, side, before, after, *rest in NAMED_NOT_SCORED:
        source = before + after + "".join(rest)
        ok = the_tool_answers(_shipped, source, len(before), len(before) + len(after),
                              after)
        named[what] = {"side": side, "tool_closes_the_books": ok}
        print(f"{what:<{width}}  {side:<10}  {'yes' if ok else 'no'}")

    # --- the positive control ------------------------------------------- #
    crude_kept, crude_honest, _r, _f = a_crude_word_check_loses_the_seams(words_spans)
    whole_words_lost, whole_words_total = crude_honest - crude_kept, crude_honest
    broken = a_broken_seam_convention(words_spans)
    if broken is None:
        seam_note = ("the rule keeps no seam convention yet, so there is none to break - "
                     "this\n         tree is the one the instrument is being written "
                     "against")
        seam_ok = True
    else:
        b_kept, b_honest, b_refused, b_forged = broken
        seam_note = (f"loses {b_honest - b_kept} of the {b_honest} honest shapes and lets "
                     f"{b_forged - b_refused} of\n         the {b_forged} forgeries "
                     f"through")
        seam_ok = (b_kept < b_honest) and (b_refused < b_forged)
    accept_all = results["CONTROL always accept"]
    refuse_all = results["CONTROL always refuse"]
    widened = results["CONTROL the number convention widened"]
    cand = results[cand_name]
    points = {(round(r["drift_accepted"], 6), round(r["tampering_refused"], 6))
              for r in (accept_all, refuse_all, cand)}
    checks = [
        ("there are pairs on both sides", len(drift) > 0 and len(tamper) > 0),
        ("there are spans on both sides of the boundary class",
         len(cut_spans) > 0 and len(whole_spans) > 0),
        ("there are spans on both sides of the word class",
         len(cut_words) > 0 and len(whole_words) > 0),
        ("'always accept' fails the tampering side",
         accept_all["tampering_refused"] < THRESHOLDS["tampering_refused"]),
        ("'always refuse' fails the drift side",
         refuse_all["drift_accepted"] < THRESHOLDS["drift_accepted"]),
        # The boundary class is answered by the verdict path, so a check
        # standing above the fast path is outside every rule on the dial and
        # 'always accept' cannot fail the cut side once one exists. What holds
        # that side honest is the other one: a boundary check that refuses
        # everything scores 0% on the spans that cut no number, and 'always
        # refuse' is here to show that side can be failed.
        ("'always refuse' fails the side where the boundary cuts no number",
         refuse_all["spans_between_numbers_accepted"]
         < THRESHOLDS["spans_between_numbers_accepted"]),
        ("'always refuse' fails the side where the boundary cuts no word",
         refuse_all["spans_between_words_accepted"]
         < THRESHOLDS["spans_between_words_accepted"]),
        # The control the word class needs and 'always refuse' cannot give: a
        # boundary check installed in the verdict path that reads **every**
        # boundary between two letters as a cut. It must lose the honest
        # shapes. If it keeps them, they are decoration and the narrow
        # reading's 100% is measuring nothing (project §35, §45).
        (f"a crude word check - every boundary between two letters - loses "
         f"{whole_words_lost} of the {whole_words_total} honest shapes",
         whole_words_lost > 0),
        (f"the rule's seam convention, turned upside down, {seam_note}", seam_ok),
        ("the three rules land on three different points", len(points) == 3),
        ("the two-digit mutant answers differently from the rule it mutates",
         candidates()["CONTROL the number convention widened"](
             *_THE_MUTANT_MUST_DIFFER_HERE)
         != candidates()[cand_name](*_THE_MUTANT_MUST_DIFFER_HERE)),
        ("the rule with a two-digit grouping convention fails",
         widened["verdict"] == "FAIL"),
        # Round 9: the one reading of a number is asked from both sides. A
        # boundary that reads digits alone must let forgeries through, and one
        # that reads every mark against a numeral as part of it must refuse
        # honest spans and honest tool output. If either passes, the class
        # that should have caught it measures nothing.
        (f"a boundary reading digits alone lets {weak[1] - weak[0]} of the "
         f"{weak[1]} cut spans through", weak[0] < weak[1]),
        (f"a boundary reading every mark as part of a number refuses "
         f"{wide_spans[3] - wide_spans[2]} of the {wide_spans[3]} whole spans",
         wide_spans[2] < wide_spans[3]),
        (f"the same boundary refuses {wide_tools[1] - wide_tools[0]} of the "
         f"{wide_tools[1]} values of tool output", wide_tools[0] < wide_tools[1]),
        # The round of the search: a locate that places only exact text must
        # lose the drift, and one that places the whole source for any quote
        # must place the absent ones. If either passes its side, that side
        # measures nothing.
        (f"a locate of exact text alone places {x_kept} of the {h_n} honest "
         f"quotes", x_kept < h_n),
        (f"a locate that places the whole source places {a_n - e_refused} of "
         f"the {a_n} absent quotes", e_refused < a_n),
    ]
    powered = all(ok for _, ok in checks)
    print("\npositive control:")
    for what, ok in checks:
        print(f"  [{'ok ' if ok else 'NO '}] {what}")
    print("  => " + ("the instrument separates the rules and catches a broken reading "
                     "of a number,\n     so the candidate's number is a measurement"
                     if powered else
                     "BROKEN - the instrument does not separate the rules it is given. "
                     "Fix the instrument, not the rule."))

    if args.show_misses:
        for name in ("v0.8.1 content signature", "v0.8.3 sequence of tokens",
                     "CONTROL the number convention widened", cand_name):
            r = results[name]
            print(f"\n  {name}")
            wrong = [(k, v) for k, v in sorted(r["tampering_by_kind"].items()) if v[0]]
            print("    tampering accepted (each of these must be refused):"
                  if wrong else "    tampering: all refused")
            for k, (ok, n) in wrong:
                print(f"      {ok:>5}/{n:<5} {ok / n:>5.0%}  {k}")
            wrong = [(k, v) for k, v in sorted(r["drift_by_kind"].items()) if v[0] < v[1]]
            print("    drift refused (each of these should be accepted):"
                  if wrong else "    drift: all accepted")
            for k, (ok, n) in wrong:
                print(f"      {n - ok:>5}/{n:<5} {(n - ok) / n:>5.0%}  {k}")

    if args.json:
        # The per-shape table of the boundary class is keyed by (shape, side);
        # JSON has no tuple keys, so it is written as one string here and
        # nowhere else, leaving the table above reading the pair it was built
        # with.
        for r in results.values():
            r["spans_by_kind"] = {f"{k} [{side}]": v
                                  for (k, side), v in sorted(r["spans_by_kind"].items())}
            r["words_by_kind"] = {f"{k} [{side}]": v
                                  for (k, side), v in sorted(r["words_by_kind"].items())}
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"thresholds": THRESHOLDS, "corpus": where, "sampled": len(quotes),
                       "drift_pairs": len(drift), "tampering_pairs": len(tamper),
                       "spans_cutting_a_number": len(cut_spans),
                       "spans_cutting_none": len(whole_spans),
                       "spans_cutting_a_word": len(cut_words),
                       "spans_cutting_no_word": len(whole_words),
                       "honest_word_shapes_lost_by_a_crude_check":
                           [whole_words_lost, whole_words_total],
                       "named_not_scored": named,
                       "tool_output": {"origin": tool_origin,
                                       "accepted_of": [tool_ok, tool_n],
                                       "named_list_members_refused_of":
                                           [tool_named_refused, tool_named]},
                       "proposer_agrees_of": [p_held, p_asked],
                       "proposer_absent_placed_nowhere_of": [a_refused, a_n],
                       "proposer_present_placed_where_it_stands_of": [h_kept, h_n],
                       "answered_by_the_fast_path": {f"{k} [{side}]": n
                                                     for (k, side), n in sorted(seen.items())},
                       "powered": powered,
                       "characters_swept": len(SWEPT),
                       "marks_against_a_digit": {" | ".join(k): v
                                                 for k, v in sorted(census.items())},
                       "results": results}, fh, indent=1)
        print(f"\nwritten to {args.json}")
    # The exit code is about the verdict, not only about whether the instrument
    # works. At round 3 `main` returned `0 if powered`, so a run that printed
    # FAIL beside the candidate still left a zero behind it for anything reading
    # the code rather than the table.
    if not powered:
        return 2
    return 0 if cand["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
