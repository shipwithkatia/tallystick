"""A number the documents give with no command to recompute it. Measured on 64ff136.

The rule is CONTRIBUTING's: a number in the README or under `docs/` either
names the run it came from or comes with the command that recomputes it. The
two paragraphs this holds to it lived in README.md until the README was split;
they live in `docs/known-limitations.md` now, and the test reads both files so
the rule follows the text rather than one filename.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The README and the file its long sections moved to.
DOCUMENTS = ("README.md", "docs/known-limitations.md")
TEXT = "\n\n".join((ROOT / name).read_text(encoding="utf-8") for name in DOCUMENTS)


def _paragraph(marker: str) -> str:
    return next(p for p in TEXT.split("\n\n") if marker in p)


def test_the_price_of_the_half_line_names_a_command_that_recomputes_it():
    """A published number comes with a command that recomputes it. The docs give
    1,489 posted traces, 130 moved 0 -> 1, and 60 named
    before, with no command and no address: the traces are this project's own
    benchmark output (bench/work*/posted, .gitignore) and no script in bench/
    counts them. Recounted outside the repo on 64ff136: 1489 files, 130 whose
    only exit-1 reason is answer_mostly_unclaimed - the number holds, nobody
    else can check it. Same for "about 165 characters (29 words)"."""
    para = _paragraph("1,489")
    assert re.search(r"python bench/\S+\.py", para), para[:300]
    store = _paragraph("about 165 characters")
    assert re.search(r"python bench/\S+\.py", store), store[:300]
