"""What a first-time reader of README.md is told, checked against what the code
does. Each test fails on 23908ba."""

from __future__ import annotations

import io
import os
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from tallystick import TraceError, audit  # noqa: E402
from tallystick.cli import main  # noqa: E402


def _verdict_of(argv):
    out, err = io.StringIO(), io.StringIO()
    cwd = Path.cwd()
    os.chdir(ROOT)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
    finally:
        os.chdir(cwd)
    text = out.getvalue()
    for word in ("CANNOT BE CHECKED", "PARTLY", "CAN BE CHECKED"):
        if re.search(rf"^{word}\b", text, re.M):
            return word, code
    return None, code


def test_readme_verdict_comments_on_example_logs_match_the_tool():
    """README 'How to Use' step 1 annotates each command with what it prints.
    ambiguous_tools.json is annotated 'PARTLY'; the tool prints CANNOT BE
    CHECKED (and examples/logs/README.md says so too)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    wrong = []
    for m in re.finditer(r"^tallystick check-trace (examples/logs/\S+)\s+#\s*(.+)$", readme, re.M):
        path, comment = m.group(1), m.group(2)
        verdict, code = _verdict_of(["check-trace", path])
        for word in ("PARTLY", "CANNOT BE CHECKED"):
            if word in comment and verdict != word:
                wrong.append(f"{path}: README says {word!r}, tool prints {verdict!r} (exit {code})")
    assert not wrong, wrong


def test_readme_python_version_matches_pyproject():
    """Every 'Python 3.x or newer' in the README must name the floor in
    pyproject's requires-python. The README once said 3.9 against a floor of
    3.10, which sends a reader to an install pip refuses ('requires a different
    Python'); it says 3.10 now, and this holds it there."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    req = re.search(r'^requires-python = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    floor = re.fullmatch(r">=\s*3\.(\d+)", req).group(1)
    claims = re.findall(r"Python 3\.(\d+)(?: or newer|\+)", readme)
    assert claims and all(c == floor for c in claims), (req, claims)


@pytest.mark.parametrize("make", ["missing", "directory"])
def test_audit_on_an_unreadable_file_raises_trace_error_as_readme_says(tmp_path, make):
    """README: 'a file that cannot be read raises TraceError (the terminal's
    exit 2)'. A missing path or a directory raises OSError instead, so
    `except TraceError` - the documented contract - does not catch it."""
    path = tmp_path / "run.json"
    if make == "directory":
        path.mkdir()
    with pytest.raises(TraceError):
        audit(str(path))


def test_readme_status_line_names_the_version_pyproject_installs():
    """The status line is where a reader learns which version this is. It said
    v0.7.x while pyproject said 0.8.0 and the latest tag said v0.7.4, and no
    test compared the three."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    line = next((l for l in readme.splitlines() if l.startswith("**Status:**")), None)
    assert line, "README has no '**Status:**' line"
    stated = re.search(r"\bv(\d+\.\d+\.\d+)\b", line)
    assert stated, f"the status line names no version: {line!r}"
    assert stated.group(1) == version, (stated.group(1), version)


def test_the_package_version_matches_pyproject():
    """`pip install` reads pyproject; `tallystick.__version__` is what a benchmark
    row file records. Two numbers in two files, so they are compared here."""
    import tallystick

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert tallystick.__version__ == version
