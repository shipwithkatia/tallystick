"""README opens with one summary paragraph, not two that repeat each other.

The test fails on 23908ba. The growth numbers README and docs publish are
recomputed in test_docs_growth_numbers.py.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


#: The forms the README's opening summary has taken. `**In short.**` is the
#: one that was doubled; `## TL;DR` replaced it in 2569bc2.
OPENERS = ("**In short.**", "## TL;DR")


def test_readme_has_one_opening_summary():
    """README.md lines 5 and 6 both opened with **In short.**, the second
    repeating the first three sentences of the first - which is what fails on
    23908ba. Counting whichever form is present keeps that failure and adds
    one: both forms at once is the same duplication under two names."""
    found = {form: README.count(form) for form in OPENERS if form in README}
    assert found, f"README opens with no summary; expected one of {OPENERS}"
    assert sum(found.values()) == 1, f"README opens with {found}"

