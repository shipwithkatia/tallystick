"""The examples say what the tool will print. This keeps that true.

A first-time user has no way to tell a correct answer from a plausible one, so
`examples/expected/` holds the exact output of each example log and this test
compares it with what the code prints now. Without it the promise in
`examples/logs/README.md` - "run this, you should see this" - would rot on the
first change to the report, and rot silently, which is the worst way.

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

#: file -> the arguments a reader of the examples README would type.
CASES = {
    "clean_run": [],
    "laundered_search": [],
    "ambiguous_tools": [],
    "otel_spans": [],
    "not_an_agent_log": [],
    "langgraph_export": [],
    "laundered_search_flagged": ["--tool-returns-model-text", "save_note"],
    "clean_run_verbatim": ["--tool-returns-verbatim", "read_file"],
}


def _log_for(name: str) -> Path:
    stem = name.replace("_flagged", "").replace("_verbatim", "")
    return LOGS / f"{stem}.json"


def _run(name: str) -> str:
    """Exactly what a person sees: stdout, stderr, and the exit code."""
    out, err = io.StringIO(), io.StringIO()
    argv = ["check-trace", str(_log_for(name).relative_to(ROOT))] + CASES[name]
    cwd = Path.cwd()
    import os
    os.chdir(ROOT)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
    finally:
        os.chdir(cwd)
    return f"$ tallystick {' '.join(argv)}\n\n{out.getvalue()}{err.getvalue()}\nexit: {code}\n"


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_example_prints_what_the_examples_say_it_prints(name):
    path = EXPECTED / f"{name}.txt"
    assert path.exists(), f"no expected output committed for {name}"
    assert _run(name) == path.read_text(encoding="utf-8"), (
        f"{name} no longer prints what examples/expected/{name}.txt says. If the "
        f"report changed on purpose, run: python tests/test_examples.py --update")


if __name__ == "__main__":
    if "--update" in sys.argv:
        EXPECTED.mkdir(parents=True, exist_ok=True)
        for name in sorted(CASES):
            (EXPECTED / f"{name}.txt").write_text(_run(name), encoding="utf-8")
            print("wrote", EXPECTED / f"{name}.txt")


def test_the_readme_quotes_output_the_tool_actually_produces():
    """Twice in this project a number or a sample in the README came from a run
    that no longer existed. A quoted terminal block is the same risk with more
    surface: it looks like evidence and rots silently. This pins it to the
    golden file, so the two cannot disagree."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    golden = (EXPECTED / "laundered_search.txt").read_text(encoding="utf-8")
    body = golden.split("\n", 2)[2].rsplit("\nexit:", 1)[0].rstrip()
    assert body in readme, (
        "the report block quoted in README.md is not what the tool prints. "
        "Regenerate with: python tests/test_examples.py --update, then paste "
        "examples/expected/laundered_search.txt into the README block")
