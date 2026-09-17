"""proverka20, 2.6 / 2.7: what the README says against what the tool does, and
numbers the README gives with no command to recompute them. Measured on 34dc1d4."""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tallystick.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _paragraph(marker: str) -> str:
    return next(p for p in README.split("\n\n") if marker in p)


def _note_log(note: str, readback: str) -> dict:
    return {"messages": [
        {"role": "user", "content": "Remember this."},
        {"role": "assistant", "content": "Saving.", "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "save_note", "arguments": json.dumps({"text": note})}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "save_note", "content": "ok"},
        {"role": "assistant", "content": "Reading.", "tool_calls": [{"id": "c2", "type": "function",
            "function": {"name": "read_note", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "read_note", "content": readback},
        {"role": "assistant", "content": "Done."}]}


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.xfail(strict=True, reason=(
    "README no longer promises one line; the promise was corrected, not the "
    "behaviour. `--quiet` prints a header line, then one line per noted result, "
    "at most ten and a `+N more` line - README.md, the paragraph on notes above "
    "Known limitations, and `check-trace --help`. The test asks for the old "
    "sentence word for word and can never pass. Documented, not fixed, by the "
    "owner's decision of 16 September 2026 after review 20; round 22 marked it"))
def test_quiet_says_the_note_in_one_line_on_stderr_as_the_readme_and_help_say(tmp_path):
    """README ("`--quiet` says it in one line on stderr") and `check-trace --help`
    ("is still one line on stderr") promise one line. With one noted result the
    command prints 2 lines; with 12, 12 (header, 10 results, "+2 more"). A CI
    step that greps the first stderr line, as documented, misses the results."""
    assert "`--quiet` says it in one\nline on stderr" in README
    help_text = build_parser()._subparsers._group_actions[0].choices["check-trace"].format_help()
    assert "one line on stderr" in " ".join(help_text.split())
    note = "Sydney is the capital of Australia and it was chosen in 1901 as a neutral site."
    path = tmp_path / "log.json"
    path.write_text(json.dumps(_note_log(note, note)), encoding="utf-8")
    code, out, err = _run(["check-trace", str(path), "--quiet"])
    assert code == 0 and out == ""
    assert len(err.strip().splitlines()) == 1, err


@pytest.mark.xfail(strict=True, reason=(
    "README no longer says a 20-word English note is silent; the promise was "
    "corrected, not the behaviour. The limit is in characters: 20 long words (278 "
    "characters) are listed, as the test's own note is - README.md, Known "
    "limitations, the item on a note read back inside a store's record "
    "(python bench/store_record_threshold.py). The test asks for the old sentence "
    "and for no note, and can never pass. Documented, not fixed, by the owner's "
    "decision of 16 September 2026 after review 20; round 22 marked it"))
def test_the_store_record_sentence_holds_for_a_20_word_english_note(tmp_path):
    """README: "On a 20-word English note nothing is said; from about 165
    characters (29 words) the note is listed". The limit is in characters, not
    words: a 20-word English note of long words (278 characters) is listed, and
    a note of short words is silent up to 54 words (189 characters). No command
    in the repository recomputes the sentence (the script is in the lab)."""
    words = ("Unquestionably international telecommunications infrastructure investments "
             "significantly outperformed conventional manufacturing expectations throughout "
             "nineteenth century industrialisation ") * 3
    note = " ".join(words.split()[:20])
    record = json.dumps({"results": [{
        "id": "892db2ae-06d9-49e5-8b3e-585ef9b85b8e", "memory": note, "hash": "3f2b1c9e",
        "metadata": None, "score": 0.38, "created_at": "2026-09-16T10:00:00-07:00",
        "updated_at": None, "user_id": "alice"}]})
    path, out = tmp_path / "log.json", tmp_path / "out.json"
    path.write_text(json.dumps(_note_log(note, record)), encoding="utf-8")
    _run(["check-trace", str(path), "--quiet", "--json", str(out)])
    assert "On a 20-word English note nothing is said" in " ".join(README.split())
    assert json.loads(out.read_text())["may_be_model_text"]["count"] == 0


def test_the_price_of_the_half_line_names_a_command_that_recomputes_it():
    """Rule 5: README gives 1,489 posted traces, 130 moved 0 -> 1, and 60 named
    before, with no command and no address: the traces are this project's own
    benchmark output (bench/work*/posted, .gitignore) and no script in bench/
    counts them. Recounted outside the repo on 34dc1d4: 1489 files, 130 whose
    only exit-1 reason is answer_mostly_unclaimed - the number holds, nobody
    else can check it. Same for "about 165 characters (29 words)"."""
    para = _paragraph("1,489")
    assert re.search(r"python bench/\S+\.py", para), para[:300]
    store = _paragraph("about 165 characters")
    assert re.search(r"python bench/\S+\.py", store), store[:300]
