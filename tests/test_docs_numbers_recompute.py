"""README opens with one "In short" paragraph, not two that repeat each other.

The test fails on 23908ba. The growth numbers README and docs publish are
recomputed in test_docs_growth_numbers.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from tallystick.adapters.openai_chat import to_trace
from tallystick.convert import json_bytes

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_has_one_in_short_paragraph():
    """README.md lines 5 and 6 both open with **In short.**; line 6 repeats
    the first three sentences of line 5."""
    n = README.count("**In short.**")
    assert n == 1, f"README opens with {n} 'In short' paragraphs"

