"""proverka16, item 0: the replacement for
test_proverka13_docs_numbers.py::test_docs_trace_growth_holds_for_the_chat_it_describes.

That test is left in place and still fails. It guarded a sentence that is gone:
docs/auditable-traces.md at f731bdb said "ten times the size of the log at 116
turns" and named no command. Round 15 rewrote the paragraph and named
`python bench/trace_growth.py`. The old test now demands that the CODE reach 10x
at 116 turns on a tool-call-only chat, which the docs no longer claim (they say
516 turns for that shape), and which only a change of trace format could give.

This test guards what the docs claim today: that the command they name exists
and prints the numbers they quote. It reads the prose, so a number changed in
the docs without a new run of the script fails here, and so does a script
change that moves a published number.

Passes on 67090d6. Run against the text of docs/auditable-traces.md and
README.md at f731bdb (see `_check`), it fails: that text names no command, and
its 116 turns, 667 turns and 4.5 MB are not what the script prints.
"""

from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "auditable-traces.md"
README = ROOT / "README.md"
COMMAND = "python bench/trace_growth.py"


def _script_output() -> str:
    sys.path.insert(0, str(ROOT / "bench"))
    try:
        import trace_growth
    finally:
        sys.path.remove(str(ROOT / "bench"))
    out = io.StringIO()
    cwd = Path.cwd()
    os.chdir(ROOT)
    try:
        with redirect_stdout(out):
            assert trace_growth.main([]) == 0
    finally:
        os.chdir(cwd)
    return out.getvalue()


def _paragraph(text: str, start: str, end: str) -> str:
    i = text.find(start)
    assert i >= 0, f"paragraph starting {start!r} not found"
    j = text.find(end, i)
    assert j >= 0, f"end marker {end!r} not found after {start!r}"
    return " ".join(text[i:j].split())


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _rounds_to(published: str, printed: float) -> bool:
    """`printed` written at the precision `published` is written at."""
    decimals = len(published.split(".")[1]) if "." in published else 0
    return f"{printed:,.{decimals}f}".replace(",", "") == published.replace(",", "")


def _check(docs_text: str, readme_text: str, output: str) -> list:
    """Every growth number the docs and README publish, against the script."""
    problems = []
    # The crossings, and the size point every row is measured at ("2,000 turns:").
    printed_turns = {int(m.replace(",", "")) for m in re.findall(r"at ([\d,]+) turns", output)}
    printed_turns |= {int(m.replace(",", "")) for m in re.findall(r"([\d,]+) turns:", output)}
    printed_mb = [float(m) for m in re.findall(r"([\d.]+) MB", output)]
    printed_x = [int(m) for m in re.findall(r"\((\d+)x\)", output)]

    docs = _paragraph(docs_text, "That record has a cost", "None of AgentHallu")
    readme = _paragraph(readme_text, "A long chat makes a big trace", "Why the format keeps it")
    for where, text in (("docs", docs), ("README", readme)):
        if COMMAND not in text:
            problems.append(f"{where}: the growth paragraph does not name `{COMMAND}`")
        # "ten times the log at 127 turns", "the crossing is at 677 turns",
        # "ten times at 516 turns"
        for m in re.finditer(r"at ([\d,]+) turns", text):
            if int(m.group(1).replace(",", "")) not in printed_turns:
                problems.append(f"{where}: {m.group(0)!r} - the script prints crossings "
                                f"{sorted(printed_turns)}")
        for m in re.finditer(r"(\d+(?:\.\d+)?) MB", text):
            if not any(_rounds_to(m.group(1), v) for v in printed_mb):
                problems.append(f"{where}: {m.group(0)!r} - the script prints {printed_mb} MB")
        # "(8 times, 4.6 MB and 36 MB)"
        for m in re.finditer(r"\((\d+) times", text):
            if int(m.group(1)) not in printed_x:
                problems.append(f"{where}: {m.group(0)!r} - the script prints {printed_x}x")
    return problems


def test_the_growth_numbers_in_the_docs_are_what_the_named_command_prints():
    output = _script_output()
    problems = _check(DOCS.read_text(encoding="utf-8"),
                      README.read_text(encoding="utf-8"), output)
    assert not problems, "\n".join(problems) + "\n--- script output ---\n" + output


def test_the_check_would_have_caught_the_f731bdb_text():
    """The guard is only worth something if it fails on the text the first
    review found wrong. The f731bdb paragraphs are quoted here verbatim."""
    old_docs = (
        "That record has a cost for a long chat, and it is better known than met. Every "
        "step lists every artifact before it, so the trace grows with the square of the "
        "turns. The text is stored once; the ids repeat. Measured with the OpenAI reader "
        "on a chat of one tool call per turn: with tool replies of 40 characters the "
        "trace reaches ten times the size of the log at 116 turns, and 2,000 turns turn a "
        "0.8 MB log into a 127 MB trace; with replies of 2,000 characters the crossing is "
        "at 667 turns, and 2,000 turns give 4.5 MB of log and 131 MB of trace. None of "
        "AgentHallu's 693 trajectories comes near it")
    old_readme = (
        "A long chat makes a big trace. Every step lists every artifact recorded before "
        "it, because a chat sends its whole history each turn, so the trace grows with "
        "the square of the turns: 2,000 short turns turn a 0.8 MB log into a 127 MB "
        "trace. `convert` and `check-trace` say so, with the sizes, once the trace is ten "
        "times the log; the exit code does not change. Why the format keeps it that way")
    problems = _check(old_docs, old_readme, _script_output())
    text = "\n".join(problems)
    assert "116 turns" in text and "667 turns" in text and "4.5 MB" in text, text
    assert "does not name" in text, text


CORPUS = ROOT / "bench" / "work-agenthallu" / "AgentHallu"


@pytest.mark.skipif(not CORPUS.is_dir(), reason=f"AgentHallu corpus not found at {CORPUS}")
def test_the_corpus_sentence_is_what_the_named_command_prints():
    """docs: "None of AgentHallu's 693 trajectories comes near it: the largest trace
    there is 2.3 times its log (`python bench/trace_growth.py --corpus <AgentHallu>`".
    Needs the corpus, which is not in the repository, so it guards nothing for a
    stranger; the two tests above do not."""
    text = " ".join(DOCS.read_text(encoding="utf-8").split())
    m = re.search(r"None of AgentHallu's (\d+) trajectories comes near it: the largest "
                  r"trace there is ([\d.]+) times its log \(`python bench/trace_growth.py "
                  r"--corpus <AgentHallu>`", text)
    assert m, "the corpus sentence or its command changed"
    sys.path.insert(0, str(ROOT / "bench"))
    try:
        import trace_growth
    finally:
        sys.path.remove(str(ROOT / "bench"))
    n, best, _where = trace_growth.corpus_ratio(CORPUS)
    assert n == int(m.group(1))
    assert _rounds_to(m.group(2), best)
