"""Are the 4cdf21f snippets still the ones the README table was made with?

    python3 bench/mutations/check_snippets.py

`mutate.py` carries a second snippet set, `AS_OF_4CDF21F`, so the command
printed beside the table in `README.md` runs instead of reporting STALE. The
worth of that set rests on one thing: the snippets are the ones the table was
recorded with, not snippets chosen until something was found. This checks it,
against the source rather than against a copy of it:

  1. every entry equals the same entry of `mutate.py` as of 9deaac4, the commit
     the table and the script were published in, read out of git here;
  2. every snippet stands in the code of 4cdf21f exactly once, and its file is
     a file of that ref;
  3. no entry is there for a mutation whose current snippet already fits
     4cdf21f - a second set that is never reached hides drift in the first.

Exit 0 and a line per mutation, or exit 1 naming what drifted. Not a pytest
file on purpose: the suite's own count is quoted in `README.md`, and a file
under `tests/` would also be copied into every mutation run and change every
number in the table.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECORDED_WITH = "9deaac4"
REF = "4cdf21f"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _at(ref: str, rel: str) -> str | None:
    """`rel` as of `ref`, or None when the ref has no such file."""
    done = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=REPO,
                          capture_output=True, text=True)
    return None if done.returncode else done.stdout


def main() -> int:
    now = _module(HERE / "mutate.py", "mutate_now")
    with tempfile.TemporaryDirectory() as tmp:
        then_path = Path(tmp) / "mutate_then.py"
        source = _at(RECORDED_WITH, "bench/mutations/mutate.py")
        if source is None:
            print(f"cannot read bench/mutations/mutate.py at {RECORDED_WITH}")
            return 1
        then_path.write_text(source, encoding="utf-8")
        then = _module(then_path, "mutate_then")

    bad = []
    for name, edits in sorted(now.AS_OF_4CDF21F.items()):
        recorded = then.MUTATIONS.get(name)
        if recorded != edits:
            bad.append(f"{name}: does not match {RECORDED_WITH}")
            continue
        counts = []
        for rel, old, _new in edits:
            text = _at(REF, rel)
            counts.append("no file" if text is None else text.count(old))
        if counts != [1] * len(edits):
            bad.append(f"{name}: occurrences at {REF} are {counts}, want all 1")
            continue
        print(f"{name:<44} matches {RECORDED_WITH}, {len(edits)} snippet(s), "
              f"each once at {REF}")

    # (3): a mutation is in the second set only because the first does not fit.
    for name, edits in sorted(now.AS_OF_4CDF21F.items()):
        if not now.misses(now.MUTATIONS[name], _Tree()):
            bad.append(f"{name}: current snippet fits {REF}; the entry is dead")

    for line in bad:
        print(f"DRIFTED {line}")
    print(f"\n{len(now.AS_OF_4CDF21F)} mutation(s) pinned to {RECORDED_WITH}, "
          f"{len(bad)} drifted")
    return 1 if bad else 0


class _Tree:
    """Enough of a `Path` for `mutate.misses` to read `4cdf21f` out of git
    instead of a checked-out copy, so the check needs no working tree."""

    def __init__(self, rel: str = ""):
        self.rel = rel

    def __truediv__(self, other: str) -> "_Tree":
        return _Tree(other)

    def exists(self) -> bool:
        return _at(REF, self.rel) is not None

    def read_text(self, encoding: str = "utf-8") -> str:
        return _at(REF, self.rel) or ""


if __name__ == "__main__":
    sys.exit(main())
