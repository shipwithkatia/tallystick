"""Text normalisation for span comparison.

Deliberately small. Everything here is a transformation a copy-paste or a PDF
extractor can plausibly introduce: unicode composition, whitespace, quote and dash
variants, invisible characters, letter case. Nothing here is semantic.

The moment this module starts to know about meaning - stemming, synonyms,
embeddings - the project's central claim is dead, because a "credit" would then be
accepted on a judgement call rather than on evidence. That is the line, and this
docstring is where it is written down.

**One reading, two users.** `view` is the text as the comparison reads it, with
a map from every offset of the source to where it stands in that text.
`normalize` is `view` with the two ends stripped and the case folded, so the
comparison reads `view` by construction. The boundary checks in `tokens.py`
(`cuts_a_number`, `cuts_a_word`) read `view` too, with the offsets mapped.

Why this exists (round 7). Until then the comparison read a quote after
`normalize` and the boundary checks read the source raw, and a word or a number
the comparison saw whole could be cut at the boundary: `66 300 people` with a
no-break space is one number to `normalize`, which folds every whitespace to a
space, and was two to the boundary, so `300 people affected` cited from it
closed the books; `unsafe` with a soft hyphen after `un` is one word to
`normalize`, which drops the hyphen, and `safe` cited from it closed them too.
The other road - keep both raw and teach the boundary everything `normalize`
knows - would have written that knowledge twice, and it is how the hole came
about: `normalize` knew about the soft hyphen and the boundary did not.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, List, Optional, Tuple

# characters that carry no meaning but break exact comparison
_INVISIBLE = dict.fromkeys(
    map(ord, "­​‌‍⁠﻿"), None
)

_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "«": '"', "»": '"', "′": "'", "″": '"',
})

_DASHES = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
})


_WS = re.compile(r"\s+")


def _invisible(ch: str) -> bool:
    return ord(ch) in _INVISIBLE


def _ascii_view(text: str) -> Tuple[str, List[Optional[int]]]:
    """`view` of a text made of ASCII characters only, where every unit but a
    run of whitespace is one character that NFC, the quotation-mark table and
    the dash table all leave alone, and nothing is invisible. So the view is
    the text with each whitespace run made one space, and this writes exactly
    that at the speed of a regular expression rather than of a loop over the
    characters. Round 7 measured the loop alone at fifteen times the time
    `verify_run` took over the two published runs before `view` existed, and
    most of their text is ASCII."""
    out: List[str] = []
    at: List[Optional[int]] = []
    pos = prev = 0
    for m in _WS.finditer(text):
        s, e = m.span()
        out.append(text[prev:s])
        at.extend(range(pos, pos + s - prev))
        pos += s - prev
        out.append(" ")
        at.extend([pos] * (e - s))
        pos += 1
        prev = e
    out.append(text[prev:])
    at.extend(range(pos, pos + len(text) - prev))
    at.append(pos + len(text) - prev)
    return "".join(out), at


def view(text: str, drop: Callable[[str], bool] = _invisible
         ) -> Tuple[str, List[Optional[int]]]:
    """(the text as the comparison reads it, before its ends are stripped and
    its case folded; where each offset of `text` stands in it).

    `text` is read as a row of units, and each unit becomes its part of the
    view:

      * a run of whitespace, of any kind and with any character `drop` names
        inside it, becomes one space;
      * a character `drop` names becomes nothing - by default the six
        invisible characters above;
      * any other character, together with the marks that follow it and
        whatever composes with it, becomes its NFC, with quotation marks and
        dashes folded to one of each.

    The second value has one entry per offset of `text`, and one more for its
    end. An offset between two units is where the later unit begins in the
    view; an offset inside a run of whitespace is where the run's space
    stands. An offset **between a letter and a mark that belongs to it** has
    no place in the view at all, and is `None`: a boundary drawn there cuts a
    character, which is a question the caller has to answer and not one this
    function can answer by rounding.

    Why unit by unit rather than one call to NFC over the whole text: the map
    of offsets. What comes out is the same text - `normalize` is built on this
    function, and round 7 compared it with the normalize it replaced on the
    4954 distinct texts and quotes of the two published runs and on 200,000
    random strings of invisible, combining, Hangul, quotation, dash and space
    characters, with 0 differences.
    """
    if text.isascii():
        return _ascii_view(text)
    n = len(text)
    out: List[str] = []
    at: List[Optional[int]] = [0] * (n + 1)
    pos = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i + 1
            while j < n and (text[j].isspace() or drop(text[j])):
                j += 1
            # A dropped character after the last whitespace of the run is a
            # unit of its own: the run's space is behind it.
            while j > i + 1 and drop(text[j - 1]):
                j -= 1
            for k in range(i, j):
                at[k] = pos
            out.append(" ")
            pos += 1
            i = j
            continue
        if drop(ch):
            at[i] = pos
            i += 1
            continue
        j = i + 1
        while j < n:
            c = text[j]
            if c.isspace() or drop(c):
                break
            if unicodedata.category(c)[0] == "M" or (
                    ord(c) > 127 and unicodedata.normalize("NFC", text[i:j + 1])
                    != unicodedata.normalize("NFC", text[i:j]) + c):
                j += 1
                continue
            break
        piece = text[i:j]
        if j > i + 1 or ord(ch) > 127:
            piece = unicodedata.normalize("NFC", piece)
        piece = piece.translate(_QUOTES).translate(_DASHES)
        at[i] = pos
        for k in range(i + 1, j):
            at[k] = None
        out.append(piece)
        pos += len(piece)
        i = j
    at[n] = pos
    return "".join(out), at


def normalize(text: str) -> str:
    """Reduce a string to the form used for exact span comparison: `view`,
    with its two ends stripped and its case folded.

    NFC, not NFKC: composition only. NFKC also folds compatibility characters
    and would let "10²" verify against "102" - a semantic collapse, exactly
    what this module promises not to do."""
    return view(text)[0].strip().casefold()
