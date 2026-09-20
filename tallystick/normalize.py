"""Text normalisation for span comparison.

Deliberately small. Everything here is a transformation a copy-paste or a PDF
extractor can plausibly introduce: unicode composition, whitespace, quote and dash
variants, invisible characters, letter case. Nothing here is semantic.

The moment this module starts to know about meaning - stemming, synonyms,
embeddings - the project's central claim is dead, because a "credit" would then be
accepted on a judgement call rather than on evidence. That is the line, and this
docstring is where it is written down.
"""

from __future__ import annotations

import re
import unicodedata

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


def normalize(text: str) -> str:
    """Reduce a string to the form used for exact span comparison."""
    # NFC, not NFKC: composition only. NFKC also folds compatibility characters
    # and would let "10²" verify against "102" - a semantic collapse, exactly what
    # this module promises not to do.
    t = unicodedata.normalize("NFC", text)
    t = t.translate(_INVISIBLE).translate(_QUOTES).translate(_DASHES)
    t = _WS.sub(" ", t)
    return t.strip().casefold()
