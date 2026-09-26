"""`tallystick --version` prints the version and exits 0.

It is the first thing a stranger types at an unfamiliar command, and until this
test it was answered with `audit`'s usage error about a missing trace: anything
that is not a subcommand became a file name for `audit`, and `--version` was no
exception. The shorthand itself is worth keeping - `tallystick run.json` is
`tallystick audit run.json`, and `tallystick --quiet run.json` works through the
same rule - so the fix is a version flag handled before the rule, not a change
to the rule. What the rule still does with a mistyped flag is named in
docs/known-limitations.md rather than guessed at here.

What this does not check, named rather than left to be found:

- What the shorthand does with anything else. `tallystick --versoin` still
  reaches `audit` and is answered with its usage error; that is the limitation
  named in the document above, and a narrower rule for it is a round of its
  own.
- The version string itself. These tests ask that the two flags print what the
  package reports and exit 0; that the package's number matches the tag is
  `test_a_version_claim_matches_git.py`, and nothing here repeats it.
- Anything about a subcommand's own flags. `--version` is handled before the
  parser runs, so these tests say nothing about what `audit` accepts.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from tallystick import __version__
from tallystick.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_prints_the_version_and_exits_zero(flag, capsys):
    with pytest.raises(SystemExit) as stop:
        main([flag])
    assert stop.value.code == 0
    printed = capsys.readouterr().out.strip()
    assert __version__ in printed, printed
    assert printed.startswith("tallystick"), printed


def test_a_path_still_means_audit_that_path(tmp_path):
    """The shorthand the version flag steps in front of must keep working."""
    empty = tmp_path / "not-a-trace.json"
    empty.write_text("{}", encoding="utf-8")
    assert main([str(empty)]) != 0


def test_a_flag_before_a_path_still_reaches_audit(tmp_path, capsys):
    """`tallystick --quiet run.json` is undocumented and works; the version
    flag
    must not take that away. Control for the fix, not a promise about the
    form."""
    honest = ROOT / "examples" / "balanced_run.json"
    assert main(["--quiet", str(honest)]) == 0
