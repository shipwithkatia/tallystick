"""The examples say what the tool will print. This keeps that true.

A first-time user has no way to tell a correct answer from a plausible one, so
`examples/expected/` holds the exact output of each example log and this test
compares it with what the code prints now. Without it the promise in
`examples/logs/README.md` - "run this, you should see this" - would rot on the
first change to the report, and rot silently, which is the worst way.

`<name>.txt` is exactly what the command writes to stdout, so the `diff` that
README gives a stranger is empty on a correct copy. It used to carry a
`$ tallystick ...` line on top and `exit: N` at the bottom, and that diff was
never empty (review 16, 5.1). What the command writes to stderr is in
`<name>.stderr.txt` where there is any, and the exit code is in CASES below,
the same number the README's table gives.

Regenerate after a deliberate change to the report:

    python tests/test_examples.py --update
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Run as a script (`--update`) this file is not on the package's path, and
# would regenerate the expected output from whatever copy of tallystick is
# installed rather than from this working tree - which is how a golden file
# ends up disagreeing with the code it is supposed to pin.
sys.path.insert(0, str(ROOT))

from tallystick.cli import main  # noqa: E402
LOGS = ROOT / "examples" / "logs"
EXPECTED = ROOT / "examples" / "expected"

#: file -> the arguments a reader of the examples README would type, and the
#: exit code its table promises.
CASES = {
    "clean_run": ([], 0),
    "laundered_search": ([], 0),
    "ambiguous_tools": ([], 1),
    "otel_spans": ([], 0),
    "not_an_agent_log": ([], 2),
    "langgraph_export": ([], 2),
    "laundered_search_flagged": (["--tool-returns-model-text", "save_note"], 0),
    "clean_run_verbatim": (["--tool-returns-verbatim", "read_file"], 0),
}


def _log_for(name: str) -> Path:
    stem = name.replace("_flagged", "").replace("_verbatim", "")
    return LOGS / f"{stem}.json"


def _run(name: str):
    """Exactly what a person sees: stdout, stderr, and the exit code."""
    out, err = io.StringIO(), io.StringIO()
    argv = ["check-trace", str(_log_for(name).relative_to(ROOT))] + CASES[name][0]
    cwd = Path.cwd()
    import os
    os.chdir(ROOT)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
    finally:
        os.chdir(cwd)
    return out.getvalue(), err.getvalue(), code


def _expected_stderr(name: str) -> str:
    path = EXPECTED / f"{name}.stderr.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_example_prints_what_the_examples_say_it_prints(name):
    path = EXPECTED / f"{name}.txt"
    assert path.exists(), f"no expected output committed for {name}"
    out, err, code = _run(name)
    hint = "If the report changed on purpose, run: python tests/test_examples.py --update"
    assert out == path.read_text(encoding="utf-8"), (
        f"{name} no longer prints what examples/expected/{name}.txt says. {hint}")
    assert err == _expected_stderr(name), (
        f"{name} no longer writes to stderr what examples/expected/{name}.stderr.txt "
        f"says. {hint}")
    assert code == CASES[name][1], (
        f"{name} exits {code}; examples/logs/README.md and CASES say {CASES[name][1]}")


if __name__ == "__main__":
    if "--update" in sys.argv:
        EXPECTED.mkdir(parents=True, exist_ok=True)
        for name in sorted(CASES):
            out, err, code = _run(name)
            (EXPECTED / f"{name}.txt").write_text(out, encoding="utf-8")
            print("wrote", EXPECTED / f"{name}.txt")
            stderr_path = EXPECTED / f"{name}.stderr.txt"
            if err:
                stderr_path.write_text(err, encoding="utf-8")
                print("wrote", stderr_path)
            elif stderr_path.exists():
                stderr_path.unlink()
                print("removed", stderr_path)
            if code != CASES[name][1]:
                # Not rewritten: the exit code is a promise in the README's
                # table, and a golden file must not quietly move it.
                print(f"exit code of {name} is {code}, CASES says {CASES[name][1]}")


#: The documents allowed to quote a report block. Searched in full, so the
#: guard follows the text rather than one filename: 2569bc2 moved this block
#: out of the README and into docs/design.md, and a guard naming only the
#: README would have passed by finding nothing to check.
QUOTING_DOCS = ("README.md", "docs/design.md")


def test_the_docs_quote_output_the_tool_actually_produces():
    """Twice in this project a number or a sample in the README came from a run
    that no longer existed. A quoted terminal block is the same risk with more
    surface: it looks like evidence and rots silently. This pins it to the
    golden file, so the two cannot disagree - and fails if no document quotes
    it at all, which is how such a block goes missing."""
    golden = (EXPECTED / "laundered_search.txt").read_text(encoding="utf-8")
    body = golden.rstrip()
    opener = body.splitlines()[0]
    texts = {name: (ROOT / name).read_text(encoding="utf-8") for name in QUOTING_DOCS}
    quoting = [name for name, text in texts.items() if opener in text]
    assert quoting, (
        f"no document quotes the report block (searched {', '.join(QUOTING_DOCS)}). "
        "If it moved, add the file to QUOTING_DOCS; if it was dropped, say so here.")
    for name in quoting:
        assert body in texts[name], (
            f"the report block quoted in {name} is not what the tool prints. "
            "Regenerate with: python tests/test_examples.py --update, then paste "
            "examples/expected/laundered_search.txt into the block")
