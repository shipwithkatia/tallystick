"""What a piece of text says, and where its words are.

Two questions live here, and they are not the same question:

  `words` / `cuts_number` - **where a word ends**, so the proposer can place a
      span and refuse one that cuts through a number. Used by `propose/`.
  `content` / `edge_marks` - **what may differ between two texts** that are
      supposed to say the same thing. Used by the quote gate in `verify.py`.

The second one is the reason this module exists, and it is written the other way
round from the three rules that came before it.

**The default is content.** Three times a rule for the gate named what counts as
content and let the rest be noise: an edit budget of 2% of the span, then a
signature of letters, digits and separators standing between two digits, then a
sequence of tokens where whole Unicode categories - every dash, every bracket -
were declared setting. Each time an outside reader found a character the list had
not thought of, and each time the answer was to name one more: the digit, then the
edge of the span, then whatever was stuck to a number. A `-` standing one space
away from its number went through all three, so a source reading `margin - 5.2 %`
could be cited as `margin 5.2 %` and the books balanced.

So the list is inverted. `SETTING` below names the characters that are **allowed**
to differ, four of them, each with the condition under which it may and a reason
why. Every other character - every dash, bracket, slash, colon, sign, currency
mark, and every character nobody in this project has thought of yet - is content,
and content must be there on both sides. A forgotten character is now safe by
construction: forgetting it leaves it content, and the gate refuses to forgive it.
The list is what has to be argued with, and it is four lines long.

It lives on the verdict path, and the direction of the import is fixed:
`verify.py` imports this, `propose/pipeline.py` imports this. Nothing here may
import `propose` - `tests/test_no_model_imports.py` enforces it, and the rule it
enforces is the central promise of the library. This module therefore calls no
model, touches no network, reads no clock, and depends on nothing outside the
standard library and `normalize.py`, whose `view` the boundary checks read.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, List, Optional, Tuple

from .normalize import view

# --------------------------------------------------------------------------- #
# where a word ends: the reading the proposer places a span with
# --------------------------------------------------------------------------- #

#: Unicode categories a word match may disagree on: dashes, brackets, quotation
#: marks, connectors, and the three kinds of separator space. This answers "where
#: does a word end", not "what may differ" - the second question is `SETTING`
#: below, and it is deliberately a much shorter list.
_SEP_CATS = frozenset({"Pd", "Ps", "Pe", "Pi", "Pf", "Pc", "Zs", "Zl", "Zp"})
_SEP_CHARS = frozenset(".,;:!?'\"`…¡¿·")


def _numeral(ch: str) -> bool:
    """A digit or any other character that writes a number, by category (`N*`):
    `½`, `²` and `Ⅻ` are numbers to a reader, and `str.isdigit` says no to the
    first and the last."""
    return unicodedata.category(ch)[0] == "N"


def _letter(ch: str) -> bool:
    """A letter of any script, or a mark that sits on one (`L*`, `M*`). The vowel
    sign of a Devanagari syllable is a mark, and `str.isalpha` says no to it."""
    return unicodedata.category(ch)[0] in "LM"


#: The two symbols that are markup and not a mark on a number, by their Unicode
#: names: the rule of a table (`| 5 | 6 |` holds two values) and the tick of a
#: code span. The owner's decision of round 8 for the first; the second is the
#: category's other member that `bench/quote_gate_corpus.py` found against a
#: number in real text and decided the other way from `^` (`` `5` `` against
#: `x^2`).
_MARKUP = ("VERTICAL LINE", "GRAVE ACCENT")


def _relates(ch: str) -> bool:
    """An equals sign or an arrow: it relates two values and changes neither.

    **Round 9.** Round 8 read every symbol against a number as part of it, and
    the review of that round found it refusing `x=5`, `Version 1→2` and the
    value of every `key=value` pair: asked, the real output of tools in the
    two published runs holds 535 such values and round 8 refused 502. What
    `=` does beside a number in the corpus is `id=506170199519639`,
    `10^7=3.78`, `J=0→1` - an assignment, an equation, a transition - and a
    span that begins after it states the number the source states."""
    name = unicodedata.name(ch, "")
    return name == "EQUALS SIGN" or "ARROW" in name


def _a_symbol(ch: str) -> bool:
    """A symbol (`S*`: mathematics, currency, modifier, other) that is neither
    markup nor a relation. Round 7 asked Unicode what a character is and asked
    only about punctuation, so `1∕2`, `×10¹⁵`, `≤5%` and `US$1.183` were not
    numbers at their edges: DIVISION SLASH, the multiplication sign and the
    comparison signs are `Sm`, the currencies `Sc`, the caret `Sk`, the degree
    `So`."""
    return (unicodedata.category(ch)[0] == "S" and unicodedata.name(ch, "") not in _MARKUP
            and not _relates(ch))


#: What a share sign is called. `%` is punctuation (`Po`) like `#` beside it,
#: and only one of the two says what the number measures, so a category cannot
#: answer and the name does: PERCENT SIGN, ARABIC PERCENT SIGN, PER MILLE SIGN,
#: PER TEN THOUSAND SIGN and their fullwidth and small forms.
_A_SHARE = ("PERCENT", "PER MILLE", "PER TEN THOUSAND")


def _a_share(ch: str) -> bool:
    return any(k in unicodedata.name(ch, "") for k in _A_SHARE)


def _in_front_of_a_number(ch: str) -> bool:
    """A mark standing directly in front of a number that is part of it: a
    dash of any kind (`-5`, the sign), and any symbol but one of the other
    kind (`So`) - a sign (`+5`, `±1`), a currency (`$5`, `€5`), an operator of
    comparison or approximation (`≤5%`, `~3`, `≈9400`), a power or a root
    (`^2`, `⁻31`, `√3`).

    Not `So` (round 9): a copyright mark, a check mark, a degree sign in front
    of a number label it (`©2015 The Hollywood`, `✅5 tests`) and leave it
    whole. After a number the same kind is its unit (`72°`, `60◦`), and
    `_after_a_number` keeps it."""
    cat = unicodedata.category(ch)
    return cat == "Pd" or (_a_symbol(ch) and cat != "So")


def _after_a_number(ch: str) -> bool:
    """A mark standing directly after a numeral that belongs to the number: a
    share sign (`5%`, `5‰`), and any symbol that is not markup or a relation
    - a currency (`5€`), `or more` (`65+`), a unit glued to it (`72°`), a
    power to follow (`10^-34`). Not a dash: `5-year` is a compound and the
    number is whole."""
    return _a_share(ch) or _a_symbol(ch)


def _joins_two_numerals(ch: str) -> bool:
    """A character with a numeral glued on each side that makes one number of
    the two: any punctuation (`P*`), any symbol that is not markup or a
    relation, and the rest of what `is_separator` names except whitespace.
    `3/4`, `66٬300`, `2020-2021`, `1∕2`, `2×10`, `10⁻31` and `4√3` are one
    number each.

    Not a bullet (round 9): `James Bond 007•398K views` in the corpus is a
    title and a count, and a middle dot (`2.5·107`), a product, stays."""
    return (not ch.isspace() and not _relates(ch)
            and unicodedata.name(ch, "") != "BULLET"
            and (unicodedata.category(ch)[0] == "P" or _a_symbol(ch) or is_separator(ch)))


#: Characters that end a line. `normalize` folds every whitespace to a space,
#: so the view the boundary reads cannot see a line break; `_opens_a_line` asks
#: the raw text.
_LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def _opens_a_line(text: str, i: int) -> bool:
    """Is the character at `i` of the raw `text` the first thing on its line,
    with nothing before it but spaces and invisible characters?"""
    k = i - 1
    while k >= 0 and text[k] not in _LINE_BREAKS and (
            text[k].isspace() or unicodedata.category(text[k]) == "Cf"):
        k -= 1
    return k < 0 or text[k] in _LINE_BREAKS


def _a_sign_apart(ch: str) -> bool:
    """A sign that belongs to the number after it although a space stands
    between (**the owner's decision 4g of round 8**, kept in round 9): a dash
    or a mathematical symbol, in the middle of a line and not hanging off a
    word. It costs `Census 2011 - 2.6 %` and `see Report - 2023`, which no
    reading of the characters tells from `margin - 5.2 %`."""
    cat = unicodedata.category(ch)
    return cat == "Pd" or (cat == "Sm" and _a_symbol(ch))


def _an_operator_apart(ch: str) -> bool:
    """An operator with a space on each side that makes one number of the two
    around it: a mathematical symbol (the owner's decision of round 8,
    `1.296 × 10`) and the two written with ASCII punctuation (round 9,
    `3947 / 7` in Magentic_One__056, `1.296 * 10^15`)."""
    return (unicodedata.category(ch) == "Sm" and _a_symbol(ch)) or ch in "/*"


def _prefix(text: str, k: int) -> int:
    """From `k`, left across the marks glued in front of a number: signs,
    currencies, operators (`-$`, `~$`, `≤$`), and letters glued to a currency
    (`US$`). Where the prefix begins."""
    while k > 0 and _in_front_of_a_number(text[k - 1]):
        k -= 1
        if unicodedata.category(text[k]) == "Sc":
            while k > 0 and _letter(text[k - 1]):
                k -= 1
    return k


def _numeral_ahead(text: str, k: int) -> bool:
    n = len(text)
    while k < n and text[k].isspace():
        k += 1
    return k < n and _numeral(text[k])


def _next_part(text: str, j: int) -> Optional[int]:
    """`text[j - 1]` is a numeral. If the number goes on past `j`, where its
    next numeral stands; otherwise None.

    It goes on across a character that joins two numerals (`3.5`, `2×10`),
    with the next number's own prefix after it (`$100-$300`, one range as
    `5–10` is one); across the space that groups a number (`66 300`,
    `_groups_a_number`); across an operator with a space on each side
    (`1.296 × 10`, `3947 / 7`); and across a space to a vulgar fraction
    (`5 ½`)."""
    n = len(text)
    if j >= n:
        return None
    ch = text[j]
    if _joins_two_numerals(ch):
        m = _prefix_forward(text, j + 1)
        return m if m < n and _numeral(text[m]) else None
    if _groups_a_number(text, j):
        return j + 1
    if not ch.isspace():
        return None
    k = j
    while k < n and text[k].isspace():
        k += 1
    if k >= n:
        return None
    if _numeral(text[k]) and "VULGAR FRACTION" in unicodedata.name(text[k], ""):
        return k
    if _an_operator_apart(text[k]) and k + 1 < n and text[k + 1].isspace():
        m = k + 1
        while m < n and text[m].isspace():
            m += 1
        m = _prefix_forward(text, m)
        if m < n and _numeral(text[m]):
            return m
    return None


def _prefix_forward(text: str, m: int) -> int:
    while m < len(text) and _in_front_of_a_number(text[m]):
        m += 1
    return m


def _start(text: str, a: int, opens_a_line: Callable[[int], bool]) -> int:
    """Where the number whose first numeral stands at `a` begins.

    Its head, a decimal point with no word or number before it (`.5`), or a
    grouping comma with a space, the start of the text, a bracket or a sign
    before it (` ,500`) - a comma after a quotation mark is a field of CSV
    (`"Paris",2161000`, round 9), and a colon is never a head: in front of a
    number it labels it (`arXiv:1804`, `"count":42`, round 9). Then the marks
    glued in front (`_prefix`). Then a sign a space away (`_a_sign_apart`), or
    a currency a space away (`€ 120`, `US$ 1.183`, round 9)."""
    k = a
    if k > 0 and text[k - 1] == "." and not (k > 1 and (_letter(text[k - 2])
                                                        or _numeral(text[k - 2]))):
        k -= 1
    elif k > 0 and text[k - 1] == ",":
        before = text[k - 2] if k > 1 else ""
        if not before or before.isspace() or before in "([{" or _in_front_of_a_number(before):
            k -= 1
    k = _prefix(text, k)
    if k > 0 and text[k - 1].isspace():
        s = k - 1
        while s >= 0 and text[s].isspace():
            s -= 1
        if s >= 0:
            sign = text[s]
            if _a_sign_apart(sign):
                if not (s > 0 and (_letter(text[s - 1]) or _numeral(text[s - 1]))) \
                        and not opens_a_line(s):
                    k = s
            elif unicodedata.category(sign) == "Sc":
                t = s
                while t > 0 and _letter(text[t - 1]):
                    t -= 1
                if not (t > 0 and _numeral(text[t - 1])):
                    k = t
    return k


def _end(text: str, j: int) -> int:
    """Where the number whose last numeral ends at `j` ends: the marks glued
    after it (`5%`, `72°`), then a share or currency sign a space away
    (`5.2 %`, `5 €`; not a currency with a number after it, which is that
    number's), or a degree sign a space away (`25 °C`, round 9)."""
    n = len(text)
    k = j
    while k < n and _after_a_number(text[k]):
        k += 1
    if k < n and text[k].isspace():
        m = k
        while m < n and text[m].isspace():
            m += 1
        if m < n:
            mark = text[m]
            if _a_share(mark) or "DEGREE" in unicodedata.name(mark, ""):
                return m + 1
            if unicodedata.category(mark) == "Sc" and not _numeral_ahead(text, m + 1):
                return m + 1
    return k


def numbers_near(text: str, lo: int, hi: int,
                 opens_a_line: Callable[[int], bool]) -> List[Tuple[int, int]]:
    """(start, end) of every number with a numeral in `text[lo:hi]`, each read
    whole: `_start`, the numerals and what joins them (`_next_part`), `_end`,
    and a bracket pair hugging all of it (`(5%)`)."""
    out: List[Tuple[int, int]] = []
    n = len(text)
    i = lo
    while i < hi:
        if not _numeral(text[i]):
            i += 1
            continue
        a = i
        while a > 0 and _numeral(text[a - 1]):
            a -= 1
        j = i
        while True:
            while j < n and _numeral(text[j]):
                j += 1
            nxt = _next_part(text, j)
            if nxt is None:
                break
            j = nxt
        s, e = _start(text, a, opens_a_line), _end(text, j)
        if s > 0 and e < n and text[s - 1] == "(" and text[e] == ")":
            s, e = s - 1, e + 1
        out.append((s, e))
        i = max(j, i + 1)
    return out


def is_separator(ch: str) -> bool:
    """Characters a word match may disagree on: whitespace, dashes, brackets,
    quotation marks, connectors and sentence punctuation. Everything else is
    part of a word - a "%" or "$" changes what a number means and is kept.

    This decides where the proposer's words end. It does **not** decide what the
    gate may forgive: a dash is a word boundary and is still content."""
    return ch.isspace() or unicodedata.category(ch) in _SEP_CATS or ch in _SEP_CHARS


def words(text: str) -> List[Tuple[int, int, str]]:
    """(start, end, folded word) for every maximal run of non-separator
    characters in `text`, with three extensions for numbers: a mark between
    two digits ("3.5", "1,000"), a dash directly before a number that starts
    a word ("-5%") and a bracket pair hugging a single token with a digit in
    it ("(5%)", "(2024)") belong to the word. "-5%" and "(5%)" are not "5%",
    and "3-5" is not "3.5". Folding is NFC + casefold of the word
    alone, so the span is still read from the original text whatever the fold
    does to the word's length."""
    n = len(text)

    def sep(k: int) -> bool:
        # "3.5", "1,000", "10:30", "2020-2021": a mark between two digits is
        # part of the number, not a place where "3-5" may stand in for "3.5".
        # A bracket is not that kind of mark, whatever stands either side of it:
        # "10¹⁵(100%)" is a number and a bracketed number, not one long
        # number, and reading it as one made a space in front of the bracket
        # look like a change of content.
        ch = text[k]
        if not is_separator(ch):
            return False
        if unicodedata.category(ch) in ("Ps", "Pe"):
            return True
        return ch.isspace() or not (
            0 < k < n - 1 and text[k - 1].isdigit() and text[k + 1].isdigit())

    out: List[Tuple[int, int, str]] = []
    i = 0
    while i < n:
        if sep(i):
            i += 1
            continue
        j = i
        while j < n and not sep(j):
            j += 1
        a, b = i, j
        if text[a].isdigit() and a > 0 and unicodedata.category(text[a - 1]) == "Pd" \
                and (a == 1 or is_separator(text[a - 2])):
            a -= 1
        if any(ch.isdigit() for ch in text[i:j]) and a > 0 and b < n \
                and text[a - 1] == "(" and text[b] == ")" \
                and (a == 1 or is_separator(text[a - 2])) \
                and (b + 1 == n or is_separator(text[b + 1])):
            a, b = a - 1, b + 1
        out.append((a, b, unicodedata.normalize("NFC", text[a:b]).casefold()))
        i = j
    return out


def core(content_text: str, span: Tuple[int, int]) -> Tuple[int, int]:
    """`span` shrunk to its first and last word character. Punctuation and
    spacing at the edges are not text anybody vouched for or quoted."""
    a, b = span
    while a < b and is_separator(content_text[a]):
        a += 1
    while b > a and is_separator(content_text[b - 1]):
        b -= 1
    return a, b


def cuts_number(text: str, a: int, b: int,
                opens_a_line: Optional[Callable[[int], bool]] = None) -> bool:
    """Does the span [a, b) start or end inside a number?

    **One reading of a number, and one question (round 9).** The number at
    each boundary is read whole by `numbers_near` - its sign and currency,
    its numerals and what joins them, its fraction and power, its share sign,
    a sign or a mark a space away, a bracket pair around it - and the span
    cuts it when a boundary falls strictly inside it and the span holds a
    numeral of it.

    Until round 8 this was a list of cases counted outward from the numeral at
    the edge of the span: the character before it, the character after it, a
    sign a space away. A span that began on the currency or the decimal point
    had no numeral at its edge and was asked nothing, so `a loss of -$5.2
    million` cited as `$5.2 million` closed the books - `-5.2%` cited as
    `5.2%` with a currency between the sign and the digits - and so did
    `~$3`, `≤$5`, `-.5%` and `$100-$300`. Every earlier case is what the
    reading gives: `-5%`, `(5%)`, `66,300` cut at either side of its comma,
    `66 300`, `10⁻31`, `margin - 5.2 %`.

    The span has to hold a numeral of the number it cuts: a span that ends on
    `margin -` or begins on `% more` states no number, while one that begins
    at `5.2` states one the source does not.

    A numeral is any character of category `N*` (`1½`, `Ⅻ`, `¹⁵`).
    `opens_a_line(i)` says whether the character at `i` is the first on its
    line; the default asks `text`, and the gate, which reads the view, passes
    the raw text's answer (`cuts_a_number`).
    """
    if a >= b:
        return False
    if opens_a_line is None:
        def opens_a_line(i: int) -> bool:
            return _opens_a_line(text, i)
    for p in (a, b):
        for s, e in numbers_near(text, _reach(text, p, -1), _reach(text, p, +1),
                                 opens_a_line):
            if s < p < e and any(_numeral(text[x]) for x in range(max(s, a), min(e, b))):
                return True
    return False


# --------------------------------------------------------------------------- #
# what may differ: the allow-list, and nothing outside it
# --------------------------------------------------------------------------- #

#: Marks that end a sentence. They are content wherever they stand inside a
#: quote - a statement is not a question, and `U.S.` is not `US` - and setting
#: only in the run that opens or closes the quote, where keeping or dropping one
#: is how a recorder cuts a sentence out of a paragraph.
SENTENCE_MARKS = ".!?…"

#: The characters a quote may differ by, and the only ones. Each is setting only
#: under the condition beside it; anywhere else, and for every character not on
#: this list, the character is content and must be there on both sides.
#:
#:   whitespace  - a recorder re-wraps, re-indents and copies across a line
#:                 break. Never where dropping it would make one number out of
#:                 two (`1946 - 5` is a range, `1946 -5` is a signed number -
#:                 `cuts_number`, the proposer's reading since v0.7.0).
#:   ,           - the comma that ends a clause or an item; models drop and add
#:                 it, and `propose/pipeline.py` records that this cost the
#:                 project real credits at the v0.5.3 run.
#:   " '         - quotation marks, after `normalize` has folded their variants.
#:                 The quote is being quoted: the marks around it are the
#:                 recorder's punctuation, not the source's words.
#:   . ! ? …     - only in the run that opens or closes the quote, and there they
#:                 may be added or dropped whole but never exchanged for others
#:                 (`safe.` cited as `safe` is a cut, `safe.` cited as `safe?` is
#:                 a different sentence).
#:
#: A run of these that separates two letters or digits leaves **one space**
#: behind it, whatever the run was made of, because that run is a word boundary.
#: That single sentence is why none of the four needs a condition about where it
#: stands: `not able` is still not `notable`, and a space dropped after a comma
#: (`calendar, Orthodox` cited as `calendar,Orthodox`) is the spacing habit it
#: looks like rather than two words run together. A draft that made the comma
#: content between two word characters refused 35 of 417 real drift pairs on
#: exactly that.
#:
#: One place is not a word boundary, and it is where the two entries above stop
#: being setting: **between two digits, where the mark is the separator inside
#: one number.** `66,300` and `66 300` are one number set two ways and read as
#: the same thing; `66300` is not; and `since 1998, 183` is two numbers, which
#: is why it is not `since 1998,183`. `_groups_a_number` and
#: `_space_joins_a_number` hold that line, and both ask `_ONE_NUMBER` - the
#: reading of a number `cuts_number` has used since v0.7.0 - rather than
#: deciding it again.
#:
#: Everything else is content. That is the whole of the tolerance, together with
#: what `normalize` folds before any of this runs (case, NFC composition,
#: invisible characters, quotation and dash styles, runs of whitespace).
SETTING = ",\"'"

#: The signs that turn the number after them into a different number. Only "-"
#: and "+" survive `normalize`, which folds every dash to "-"; the rest are here
#: so the rule still holds on text that has not been through it.
_SIGNS = "-+−–—"
_SIGNED_NUMBER = re.compile(f"[{re.escape(_SIGNS)}]\\d")

#: How this project reads a number that carries a mark inside it: a decimal
#: point, a grouping comma with exactly three digits after it, or the colon of
#: `10:30`. Not a new convention - `cuts_number` above has refused to cut a span
#: at "a mark between two digits" since v0.7.0 and names the same three. Written
#: as a pattern so that one declared reading serves the locate and the gate.
#:
#: The three digits are what tells `since 1998, 183 people`, where closing the
#: gap leaves the single number 1,998,183, from `March 11, 2011`, where
#: `11,2011` is no number anybody writes. That is the whole of the difference:
#: no list of months, and nothing about what the sentence is about.
_ONE_NUMBER = re.compile(r"\d(?:\.\d|,\d{3}(?!\d)|:\d)")

#: Marks the run that opens a quote may not be stripped through, because a
#: digit stands directly after them: `.5% of assets` is not `5% of assets`, and
#: `,500 people affected` is not `500 people affected`.
#:
#: Why these two and not the rest of the list. The reason a quote may differ by
#: the mark at its opening run is that a recorder cuts a sentence out of a
#: paragraph and keeps or drops the mark that ended the sentence before it - and
#: a mark that ended a sentence is followed by a space and a letter, never by a
#: digit with nothing between. So a mark standing against a digit at the head of
#: a span is not that mark. What it is instead is the inside of a number whose
#: head the span does not carry: the decimal point of `.5`, the grouping comma
#: of `,500`, and by the same reading a colon, which is content everywhere
#: already by not being on the list at all.
#:
#: The two quotation marks keep their place, and that is measured rather than
#: assumed: 2 of the 5615 real quotes open with a quotation mark against a digit
#: and a recorder may drop it, while 0 open with any other mark of the list. The
#: cost of the line drawn here is those 2 quotes, and the cost of drawing it
#: further would be a source set in Swiss grouping (`66'300`) or an elided year
#: (`'90s`) at the very head of a span - 0 places in the same 5615.
#:
#: The closing run is not the same question and is deliberately left alone: 277
#: of the 5615 quotes end on a digit followed by a full stop and 2 on a digit
#: followed by a comma, and there the mark **is** the writer's own sentence or
#: clause punctuation. The asymmetry is orthography - nothing in English begins
#: with a full stop and a great deal of it ends with one - and it is counted in
#: `bench/quote_gate_corpus.py` rather than asserted.
_NUMBER_MARKS = ".,"

#: Characters that may stand at the edge of a quote without being its first or
#: last word: the sentence marks themselves and the setting that can surround
#: them (`he said "yes."` cited as `he said "yes"`).
_EDGE = frozenset(SENTENCE_MARKS + SETTING + " ")


def _space_joins_a_number(text: str, i: int) -> bool:
    """Would dropping the space at `i` leave a sign against a digit?

    `1946 - 5 July` is a range and `1946 -5 July` is a signed number, so that
    space is not spacing - it is what keeps the sign off the number. Read from
    either side: the space after the sign (`- 5`), and the space in front of a
    sign that already has its digit (`1946 -5`).

    A third shape belongs here, and two versions of this function got it wrong
    in opposite directions before it was measured: a mark with a digit behind
    it and a digit in front. v0.8.3 called every pair of that shape a change of
    content and raised 408 false alarms on dates. v0.8.4 called every pair of it
    drift, on the argument that all of them *are* dates - an argument made about
    a class that had been read and applied to a rule that cannot read. Counted,
    the class is 297 dates and 114 others, and `since 1998, 183 people` cited as
    `since 1998,183 people` is one number where the source has two.

    So the shape is decided by what comes out of the edit, by `_ONE_NUMBER`:
    the gap closes into a single number, or it does not. `Note 3. 5` and
    `1000: 16` do; `March 11, 2011` and `0 gives 2, 5/3` do not, and stay
    setting. The dates cost nothing, because none of them makes a number.
    """
    before = text[i - 1] if i else ""
    after = text[i + 1] if i + 1 < len(text) else ""
    if before in _SIGNS and after.isdigit():
        return True
    if (after.isdigit() and i > 1 and text[i - 2].isdigit()
            and not before.isspace() and not before.isalnum()
            and not _already_behind_a_point(text, i - 1)
            and _ONE_NUMBER.match(text[i - 2] + before + text[i + 1:i + 5])):
        return True
    return bool(_SIGNED_NUMBER.match(text[i + 1:i + 3]))


def _already_behind_a_point(text: str, i: int) -> bool:
    """Do the digits ending at `i - 1` already sit behind a decimal point or the
    colon of a time?

    A number groups its whole part in threes and nothing groups a fraction, so
    `0.16355140186915887,763.6387850467289` is two numbers standing in a tuple
    and not one number with a separator inside it. `_ONE_NUMBER` cannot see this
    on its own: it is shown one digit of context, and that digit looks the same
    whether or not a point stands behind it.

    Found by `bench/quote_gate_corpus.py` once the instrument read a number by a
    road of its own instead of by a copy of `_ONE_NUMBER` - a rule and an
    instrument sharing a pattern agree about everything the pattern is wrong
    about. One real pair of the corpus, and it was honest drift the gate
    refused.
    """
    j = i
    while j and text[j - 1].isdigit():
        j -= 1
    return j > 1 and text[j - 1] in ".:" and text[j - 2].isdigit()


def _groups_a_number(text: str, i: int) -> bool:
    """Is the character at `i` the separator inside **one** number - the comma
    of `66,300` or the space of `66 300`?

    Both are written for the same job and the project has always read them as
    one thing: which mark a writer groups with is typography, that there is a
    group at all is not. So a grouping separator is content, and `content`
    writes whichever one it was down as a comma, which keeps `66,300` and
    `66 300` the same quote while neither of them is `66300`.

    What it is not is the comma of `since 1998, 183 people`, where a space
    follows and two numbers stay two, or of `March 11, 2011`, where closing the
    gap spells no number anybody writes. `_ONE_NUMBER` is that test.

    Nor is it the space of `in 2011 300 people died` (round 7). A space groups
    a number only behind one to three digits, because nobody writes a number
    with a head of four digits and a space, and everybody writes a year and a
    count that way. Read as one number, `in 2011 300 people died` could be
    cited as `in 2011,300` and the books balanced, while `In 1998, 183 people
    died` cited as `In 1998 183` was refused. The comma keeps no such limit: a
    comma with no space after it divides no words, and `2011,300` is one
    number written wrongly rather than two. The question is asked of the
    digits directly behind the space, not of the head of a chain, so the
    `300 400` of `2011 300 400` is one number whatever stands before it.
    """
    if text[i] not in ", " or not (i and text[i - 1].isdigit()):
        return False
    if _already_behind_a_point(text, i):
        return False
    if text[i] == " ":
        j = i
        while j and text[j - 1].isdigit():
            j -= 1
        if i - j > 3:
            return False
    after = text[i + 1:i + 5]
    if not after[:1].isdigit():
        return False
    return bool(_ONE_NUMBER.match(text[i - 1] + "," + after))


def cuts_a_number(text: str, start: int, end: int) -> bool:
    """Does the span `[start, end)` of `text` begin or end inside a number?

    This is the only question on the verdict path that is asked of the
    **source** rather than of two strings, and it is here because four rounds
    of this branch closed the same hole one character at a time. `.5% of
    assets` cited as `5% of assets`, `,500 people affected` cited as `500
    people affected`, ` 300 people affected` cited as `300 people affected` -
    each was answered by naming one more mark that may not open a quote, and
    each time the next mark was found by somebody else. They are one event:
    **the boundary of the span falls inside a number**, so the text the span
    carries states a number the source does not.

    The shape that proves a rule over two strings can never close it carries no
    mark at all. A trace is free to declare its span one character further
    right, and then

        source    The report found that 66 300 people affected ...
        span      from character 25
        citation  300 people affected

    is a citation that matches its span **word for word**: the equality fast
    path in `verify_entry` answers it before any rule is consulted, and there
    is nothing in the pair of strings to see. What there is to see is where the
    cut was made, and that needs the source and two offsets.

    One reading of a number answers (`cuts_number`, round 9), and it holds
    the two that answered before it: the proposer's, asked of a numeral at
    the edge of the span, and `_inside_one_number`, asked of a separator the
    span carries or leaves just outside it (`,300 people` opens on the comma
    of `66,300`; `300 people` cut out of `1,500 people` leaves it behind).
    Both are a boundary falling strictly inside the number read whole, and
    the grouping space (`66 300`, `_groups_a_number`) is one of the things
    that reading joins.

    **The reading is asked of `_the_boundary_view`, not of the raw source**
    (round 7). The comparison reads a quote after `normalize`, which folds
    every whitespace to a space, and this check read the source raw: `66 300`
    set with a no-break space was one number to the comparison and two here,
    and `300 people affected` cited from it closed the books.
    """
    if start >= end:
        return False
    v, a, b, bases, raw_at = _the_boundary_view(text, start, end)
    if a is None or b is None:
        # a boundary between a numeral and a mark on it cuts the numeral
        return any(_numeral(base) for base in bases)
    if a >= b:
        return False
    return cuts_number(v, a, b, lambda i: _opens_a_line(text, raw_at[i]))


def _the_boundary_view(text: str, start: int, end: int
                       ) -> Tuple[str, Optional[int], Optional[int], List[str], List[int]]:
    """(the view the two checks read, where `start` and `end` stand in it, the
    character each boundary falling inside a character cluster cuts, and for
    each character of the view where in `text` it came from).

    It is `normalize.view` with every format character (`Cf`) dropped. The
    comparison drops six invisible characters and keeps the rest of the
    category as content, so a left-to-right mark inside a quote is a
    difference it will refuse. The boundary drops all of them, because a mark
    no reader can see is no place a reader can see a word or a number end:
    `un` + LRM + `safe` is `unsafe`. Dropping more here can only make the
    boundary stricter, never the comparison looser.

    Only the stretch of `text` the two questions can reach is read: from the
    word before the one `start` stands in to the word after the one `end`
    stands in, whitespace between them included. Every question the checks
    ask at a boundary is about the word or number it stands in, the
    whitespace next to it and the token on the other side of that whitespace
    (`66 300`, `0.163 763`), and a view is built unit by unit, so a stretch
    that begins and ends at whitespace reads the same as the whole text does.
    Reading the whole artifact instead cost a view of 20,000 characters twice
    per entry.

    Round 8 reads **two** words each way rather than one, because a sign can
    stand a space away from its number (`margin - 5.2`) and the question is
    then about the word before the sign too; and it counts an invisible
    character as the whitespace it stands in. Before that, `66 \u200b 300`
    read from its `300` began at the zero-width space, and the `66` the
    comparison sees was outside the stretch.
    """
    lo, hi = _reach(text, start, -1), _reach(text, end, +1)
    v, at = view(text[lo:hi], drop=lambda ch: unicodedata.category(ch) == "Cf")
    a, b = at[start - lo], at[end - lo]
    bases = []
    for k in (start - lo, end - lo):
        if at[k] is None:
            while at[k] is None:
                k -= 1
            bases.append(text[lo + k])
    raw_at = [0] * len(v)
    for r in range(len(at) - 1, -1, -1):
        if at[r] is not None and at[r] < len(v):
            raw_at[at[r]] = lo + r
    return v, a, b, bases, raw_at


#: How far into one word or one run of whitespace the stretch a boundary reads
#: may go (round 9). The review of round 8 timed a downloaded page with an
#: image inlined as base64 and the quote right after it: one word a million
#: characters long within two words of the quote, and the stretch - and the
#: view built over it - took all of it, 1.09 s for one entry. No question the
#: boundary asks looks further than a few characters past the word it stands
#: in, the sign or number next to it and the one after that (`_a_seam` reads
#: three letters, a grouping reads three digits). What it costs, named: a
#: number whose whole part runs longer than this in front of a decimal point
#: is read from here, so `_already_behind_a_point` cannot see that point.
#: Measured on the 5615 real spans: 0 verdicts move at 64, 256 or 1024.
_REACH_PER_WORD = 256


def _reach(text: str, i: int, step: int) -> int:
    """From `i`, across the rest of the word it stands in, the whitespace after
    that, the next word, the whitespace after it and the word after that, in
    the direction `step`: where a stretch read for the boundary at `i` may
    begin (`step` -1) or end (`step` +1). An invisible character (`Cf`) is
    read as part of the whitespace it stands in. No word and no run of
    whitespace is read further than `_REACH_PER_WORD` characters."""
    n = len(text)

    def space(ch: str) -> bool:
        return ch.isspace() or unicodedata.category(ch) == "Cf"

    k = i
    for want_space in (False, True, False, True, False):
        if step < 0:
            stop = max(0, k - _REACH_PER_WORD)
            while k > stop and space(text[k - 1]) == want_space:
                k -= 1
        else:
            stop = min(n, k + _REACH_PER_WORD)
            while k < stop and space(text[k]) == want_space:
                k += 1
    return k


def _joins_a_word(ch: str) -> bool:
    """A character that holds one word together across it where a letter stands
    on both sides: a dash of any kind (`Pd`), or a character Unicode names an
    apostrophe. The view has already folded the typeset apostrophe and the
    quotation marks that stand in for one to `'`.

    **Any dash, not the hyphen alone (round 7, the owner's decision D1).** Until
    then an en dash and an em dash were word boundaries, on the argument that
    `London–Paris flights` is two words and `Paris flights` cuts nothing. The
    argument walked past `non–lethal` typed with an en dash, which is one word
    and cited as `lethal` says the opposite - and `normalize` folds seven dashes
    into one before the comparison, so reading the same text as the comparison
    reads leaves no way to tell them apart. What the line costs is two of the
    5615 real spans, both `structure—there` cited from `there` in
    Magentic_One__011, and a refusal is an alarm a reader sees; the other
    reading costs a silent inversion.
    """
    return unicodedata.category(ch) == "Pd" or "APOSTROPHE" in unicodedata.name(ch, "")


#: The one pair of categories a boundary between two letters can have and still
#: not be a place inside a word: a small letter (`Ll`) followed directly by a
#: capital (`Lu`) - and only where both runs look like words a scrape ran
#: together, `_a_seam` below.
#:
#: Why the exception exists. A reading that calls every boundary between two
#: letters a cut refuses 118 of the 5615 real spans of the two published runs,
#: and none of those is a place inside a word. Round 7 read them one by one:
#: 92 are a literal `\n` - a backslash and the letter `n` - followed by a
#: capital (`...\nOption a:`), where the small letter is the escape's and not a
#: word's; 26 are the end of one text run into the start of the next
#: (`WikipediaIt became`, `resultsThe Venezuelan`). The first is its own shape
#: (`_an_escape`) and needs no case at all; the second is what the pair is for.
#:
#: **Why it is narrow (round 7, the owner's decision C3).** At fee1dd7 the pair
#: alone was the whole test, and it let through every word with a capital
#: inside it: `NoSQL` cited as `SQL`, `kWh` as `Wh`, `disableSSL` as `SSL`,
#: `McDonald's` as `Donald's`. So both runs are read: three letters or more on
#: the left with no capital after the first, a capital and small letters only
#: on the right. All 26 real seams are that, and none of those words is.
#:
#: **What it costs, named rather than hidden.** `MacArthur` cited as `Arthur`
#: (and its Cyrillic spelling) is a real cut of a real word that this reading
#: calls a seam. The 2325 distinct texts of the two published runs hold 18
#: Mc-/Mac- forms at 40 places; the three-letter floor keeps the 16 Mc- forms
#: whole (31 places), and the two Mac- forms, `MacArthur` and `MacKinnon`, still
#: read as seams (9 places). None of the 40 is at the boundary of any of the
#: 5615 real spans. A seam after a two-letter word (`onThe`) is now read as a
#: cut: 251 places in the same texts, 0 at the boundary of a real span. The
#: instrument prints the tool's answer on both under `NAMED_NOT_SCORED` on
#: every run.
#:
#: It is a module-level literal so that breaking it is one visible edit, and
#: `bench/quote_gate_corpus.py` breaks it on every run: a mutant that calls a
#: small letter after a small letter the seam reads `nonlethal` as two words and
#: `resultsThe` as one, and the instrument has to fail it.
_A_SEAM = ("Ll", "Lu")


def _run_left(text: str, i: int) -> str:
    """The run of letters ending just before `i`."""
    j = i
    while j and _letter(text[j - 1]):
        j -= 1
    return text[j:i]


def _run_right(text: str, i: int) -> str:
    """The run of letters starting at `i`."""
    k = i
    while k < len(text) and _letter(text[k]):
        k += 1
    return text[i:k]


def _an_escape(text: str, i: int) -> bool:
    """Is the letter at `i` the letter of an escape a scrape wrote as two
    characters - the `n` of a literal `\\n`? A boundary right after it is not a
    place inside a word, whatever follows: `\\nOption`, `\\nemotions`, `\\n1`.

    The price is a path cut after its backslash and one letter: `C:\\nuclear`
    cited as `uclear` is read the same way. Of the 125 boundaries of real
    spans that stand right after an escape's letter, 92 have a capital after
    it, 33 no letter at all, and none a small letter; the instrument prints
    the path under `NAMED_NOT_SCORED`."""
    return i > 0 and text[i - 1] == "\\" and _letter(text[i])


def _a_seam(text: str, i: int) -> bool:
    """Is the boundary at `i`, between two letters, a seam a scrape left?
    `_A_SEAM` and both runs; see the comment above it."""
    left, right = text[i - 1], text[i]
    if (unicodedata.category(left), unicodedata.category(right)) != _A_SEAM:
        return False
    lefts, rights = _run_left(text, i), _run_right(text, i)
    return (len(lefts) >= 3 and not any(unicodedata.category(c) == "Lu" for c in lefts[1:])
            and len(rights) >= 2
            and not any(unicodedata.category(c) == "Lu" for c in rights[1:]))


def _a_word_ends_before_a_numeral(text: str, i: int) -> bool:
    """Is the boundary at `i`, between a letter and a numeral, the end of a
    word rather than a place inside a token like `B12`?

    **The owner's decision F2, round 7.** A letter directly before a numeral is
    one token (`B12`, `H5N1`, `COVID19`), unless the letter ends a word of two
    letters or more in a small letter: `Kashmir1 \\nBakshi`, `emotions1
    Introduction` and `Pictures4\\nJumanji` are the seams of scraped tables,
    and the strict line refuses 21 real spans on them where this one refuses
    0. What it lets through is `Windows10` cited as `10`; that is the price,
    named here and printed by the instrument rather than hidden."""
    lefts = _run_left(text, i)
    return len(lefts) >= 2 and unicodedata.category(text[i - 1]) == "Ll"


def _holds_a_word_together(text: str, i: int) -> bool:
    """Is the character at `i` a joiner (`_joins_a_word`) with a letter on both
    sides of it? `wasn't` is one word and `half- ` is not."""
    return (0 < i < len(text) - 1 and _joins_a_word(text[i])
            and _letter(text[i - 1]) and _letter(text[i + 1]))


def _inside_one_word(text: str, i: int) -> bool:
    """Is the boundary standing at `i` - between `text[i-1]` and `text[i]` - a
    place inside one word?

    Three shapes are, and nothing here is a list of affixes: `un-`, `non-` and
    `-n't` are not named anywhere in this module, because a rule that named
    them would be a guard with a list in it, and a list is walked around by
    using a word that is not on it (project §24).

      * a letter or a joiner on both sides, unless it is a seam (`_a_seam`)
        or the letter on the left is an escape's;
      * a numeral followed by a letter: one amount, `$5M`, `10km`. No seam at
        all (round 7, E1): a seam before a capitalised word would have left
        `$5Million` cited as `$5` open, the same forgery of the size of a
        number. It costs 2 of the 5615 real spans, `2,883Medal` and
        `1811The`;
      * a letter followed by a numeral, unless a word ends there
        (`_a_word_ends_before_a_numeral`).

    `text` is the view (`_the_boundary_view`): a letter is `L*` or `M*` in any
    script, and a numeral is `N*`.
    """
    if not 0 < i < len(text):
        return False
    left, right = text[i - 1], text[i]
    if _an_escape(text, i - 1):
        return False
    lefty = _letter(left) or _holds_a_word_together(text, i - 1)
    righty = _letter(right) or _holds_a_word_together(text, i)
    if lefty and righty:
        return not _a_seam(text, i)
    if _numeral(left) and _letter(right):
        return True
    if _letter(left) and _numeral(right):
        return not _a_word_ends_before_a_numeral(text, i)
    return False


def cuts_a_word(text: str, start: int, end: int) -> bool:
    """Does the span `[start, end)` of `text` begin or end inside a word?

    The same event as `cuts_a_number` with letters in place of digits, and the
    more dangerous of the two, because a word can carry the negation of the
    sentence it stands in:

        source    The trial found the drug is unsafe for children.
        span      from the character after `un`
        citation  safe for children.

    The citation matches its span **word for word**, so the equality fast path
    in `verify_entry` closes the books before any rule of the gate is asked,
    and no comparison of two strings can see it at any width. What there is to
    see is where the cut was made, and that needs the source and two offsets -
    which is the argument round 5 made about `66 300 people affected`, one
    class over.

    Only the two boundaries are asked about, and not the character beyond each
    of them as in `cuts_a_number`. The asymmetry is the two classes' own: a
    number can leave its separator outside the span (`300 people` cut out of
    `1,500 people`), and a word has no separator to leave - the letters of a
    word are the word, so a boundary between two of them is inside it and a
    boundary anywhere else is not.

    Asked of `_the_boundary_view` since round 7, as `cuts_a_number` is: a soft
    hyphen or a left-to-right mark inside `unsafe` is not a place a word ends,
    and a boundary between `e` and the combining accent of a decomposed
    `déloyal` cuts the letter itself.
    """
    if start >= end:
        return False
    v, a, b, _bases, _raw_at = _the_boundary_view(text, start, end)
    if a is None or b is None:
        return True
    if a >= b:
        return False
    return _inside_one_word(v, a) or _inside_one_word(v, b)


def opens_a_number(text: str, i: int) -> bool:
    """Is the character at `i` a mark on `_NUMBER_MARKS` standing at the head of
    `text` with a digit directly after it?

    `edge_marks` stops the opening run here, so that `.5% of assets` keeps its
    point; this says the same thing to `content`, which would otherwise take the
    comma of `,500 people` straight back out again - the point is content by not
    being on `SETTING` at all, and the comma is on it.
    """
    return (i == 0 and text[:1] in _NUMBER_MARKS
            and len(text) > 1 and text[1].isdigit())


def is_setting(text: str, i: int) -> bool:
    """Is the character at `i` one the quote may differ by? The four entries of
    `SETTING`, each under its own condition, and nothing else."""
    if _groups_a_number(text, i) or opens_a_number(text, i):
        return False
    ch = text[i]
    if ch.isspace():
        return not _space_joins_a_number(text, i)
    return ch in SETTING


def content(text: str) -> str:
    """What `text` says, with the setting taken out.

    Every character that is not setting is kept, in order. A run of setting that
    stands between two letters or digits leaves one space behind it, because
    that run is a word boundary and dropping it outright would make `not able`
    into `notable`.

    `edge_marks` is applied first by `verify.says_the_same`: the marks opening
    and closing a quote are handled there, and everything reaching this function
    is the body between them.
    """
    out: List[str] = []
    gap = False
    for i, ch in enumerate(text):
        if opens_a_number(text, i):
            # the head of a number the span does not carry whole: `,500` is not
            # `500`, and the mark is written down as it stands.
            out.append(ch)
            continue
        if _groups_a_number(text, i):
            # one mark, whichever of the two was written: `66,300` and
            # `66 300` are the same number set two ways, and neither is
            # `66300`.
            gap = False
            out.append(",")
            continue
        if is_setting(text, i):
            gap = True
            continue
        if gap and out and out[-1].isalnum() and ch.isalnum():
            out.append(" ")
        gap = False
        out.append(ch)
    return "".join(out)


def edge_marks(text: str) -> Tuple[str, str, str]:
    """(the marks opening the quote, the body between, the marks closing it).

    An edge is read through the setting that can sit outside it, so the full
    stop in `he said "yes."` is found although a quotation mark stands after it.
    A quote that is nothing but marks and setting has an empty body, and
    `says_the_same` refuses to call two such quotes the same thing.

    The opening run stops at a mark with a digit against it. The reason this
    entry is on the list at all is that a recorder cuts a sentence out of a
    paragraph and keeps or drops the mark that ended it; `.5% of assets` has no
    sentence in front of it, the mark there is a decimal point, and dropping it
    moves the number by a factor of ten. A closing run needs no such clause:
    a mark after a digit at the end of a span (`the ratio fell to 5.`) is a
    sentence ending, and dropping it changes nothing.
    """
    i, j = 0, len(text)
    while i < j and text[i] in _EDGE:
        if text[i] in _NUMBER_MARKS and i + 1 < j and text[i + 1].isdigit():
            break
        i += 1
    while j > i and text[j - 1] in _EDGE:
        j -= 1
    marks_before = "".join(ch for ch in text[:i] if ch in SENTENCE_MARKS)
    marks_after = "".join(ch for ch in text[j:] if ch in SENTENCE_MARKS)
    return marks_before, text[i:j], marks_after


def same_ending(in_source: str, as_cited: str) -> bool:
    """Do two runs of sentence marks end the sentence the same way?

    The two arguments are not interchangeable, and reading them as if they were
    is what let a question be quoted from a source that does not ask one. The
    first is what the source has at that edge; the second is what the recorder
    wrote. So:

      * nothing in the citation - the recorder cut the sentence out of a
        paragraph and said the same thing, whatever the source's mark was;
      * nothing in the source - only a full stop may appear, because a full
        stop is the recorder's own punctuation closing their own sentence and
        a question mark is not. `safe` cited as `safe?` asks something the
        source does not, and it cost one character of span to get there while
        `safe.` cited as `safe?` was refused;
      * a run on both sides - it has to say the same ending, with a full stop
        allowed on top of it and no other mark. A recorder quoting a question
        inside their own sentence writes `... is it safe?".`, and the corpus
        has three such pairs.

    A mark typed twice is one mark (`safe..`), and `...` is the ellipsis
    character set another way.
    """
    if not as_cited:
        return True
    if not in_source:
        return set(_one_ending(as_cited)) <= {"."}
    a, b = _one_ending(in_source), _one_ending(as_cited)
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return longer.startswith(shorter) and set(longer[len(shorter):]) <= {"."}


def _one_ending(run: str) -> str:
    """A run of marks with repeats squeezed out: `..` and `.` are one ending,
    `…` and `...` are one ending, `.?` is its own."""
    squeezed: List[str] = []
    for ch in run.replace("…", "."):
        if not squeezed or squeezed[-1] != ch:
            squeezed.append(ch)
    return "".join(squeezed)
