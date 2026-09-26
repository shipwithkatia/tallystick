"""A document may not say a version is out when git does not show it.

Two rounds fixed the same defect by hand: README, CHANGELOG, `bench/HISTORY.md`
and `docs/design.md` each claimed versions that no tag and no commit on `main`
support. The second round found three places the first had missed, because the
first searched for the wordings it was replacing - and a checkbox, a "Done so
far" heading and the word "published" carry the same claim without any of those
words in them. A third round would find a fourth notation. So the rule stops
being a pass over the prose and becomes this test.

`bench/HISTORY.md` defines the three states this checks against: a version is
**tagged** when `git tag` shows it, it **reached `main`** when `pyproject.toml`
carried its number on the public default branch, and it **never reached `main`**
otherwise. The numbers and the states both come from git here, never from a
document, so a document cannot certify itself.

What this does not do, said plainly rather than left to be found:

- It knows the notations listed in `DONE_*` below and no others. A status put in
  a table column, a badge or an emoji passes unseen. Each new notation is added
  here when it is found, not remembered.
- A line that disclaims one version covers every version it names. A line
  reading "v0.8.2 never reached `main`" would also shield an `[x] v9.9.9` beside
  it. No line in the repository does that today.
- It needs git history and tags. A shallow checkout has neither, and the test
  fails with that as the reason rather than passing on no evidence: a guard that
  cannot look must not report "clean". `.github/workflows/ci.yml` checks out
  with `fetch-depth: 0` for exactly this.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: `v0.9.0` or `v0.9` anywhere in a line.
VERSION = re.compile(r"\bv(\d+\.\d+(?:\.\d+)?)\b")

#: The notations a claim of "this one is out" is written in. A checkbox says it
#: with a character, a heading says it above the numbers it covers, and the
#: three words say it outright.
DONE_BOX = re.compile(r"^\s*[-*]\s*\[x\]", re.IGNORECASE)
DONE_WORD = re.compile(r"\b(published|released|shipped)\b", re.IGNORECASE)
DONE_HEADING = re.compile(r"\bDone so far\b")

#: A line that states the state itself is not claiming the opposite.
DISCLAIMS = re.compile(
    r"never reached `?main|never merged|never released|not in this list",
    re.IGNORECASE)

#: Lines where one of the words above is about something other than a version's
#: state. Each entry carries the reason it is here; an entry with no reason is a
#: hole, not an exception.
ALLOWED = {
    "The published tables cannot move":
        "'published' is the benchmark tables, not a version's state",
}


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True)
    return done.stdout if done.returncode == 0 else ""


def _tags() -> set[str]:
    return {t[1:] for t in _git("tag").split() if t.startswith("v")}


def _main_ref() -> str:
    """`main` in a full clone, `origin/main` in a CI checkout of a branch."""
    for ref in ("main", "origin/main", "HEAD"):
        if _git("rev-parse", "--verify", "--quiet", ref).strip():
            return ref
    return ""


def _versions_on_main(ref: str) -> set[str]:
    """Every number `pyproject.toml` has carried on the public branch."""
    seen = set()
    history = _git("log", "--format=%H", ref, "--", "pyproject.toml")
    for commit in history.split():
        found = re.search(r'^version = "([0-9.]+)"',
                          _git("show", f"{commit}:pyproject.toml"), re.M)
        if found:
            seen.add(found.group(1))
    return seen


def _reached(version: str, tags: set[str], on_main: set[str]) -> bool:
    """`v0.5` is reached when the 0.5 series reached `main`: the entries for a
    series name it two ways, and `pyproject.toml` never carried `0.5` itself."""
    if version in tags or version in on_main:
        return True
    return any(m == f"{version}.0" or m.startswith(f"{version}.")
               for m in tags | on_main)


def _documents():
    for rel in _git("ls-files").split("\n"):
        if rel.endswith(".md") and not rel.startswith("bench/work"):
            yield rel


def test_no_document_claims_a_version_git_does_not_show():
    tags, ref = _tags(), _main_ref()
    assert tags, (
        "no tags found: this test compares documents against git and has "
        "nothing to compare with. A shallow checkout is the usual cause - "
        "`fetch-depth: 0`. Failing rather than passing, because a guard that "
        "cannot look must not report clean.")
    assert ref, "neither `main` nor `origin/main` is present in this clone"
    on_main = _versions_on_main(ref)
    assert on_main, f"no version bump of pyproject.toml found on {ref}"

    offences = []
    for rel in _documents():
        for number, line in enumerate(
                (ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            claims = (DONE_BOX.search(line) or DONE_WORD.search(line)
                      or DONE_HEADING.search(line))
            if not claims or any(a in line for a in ALLOWED):
                continue
            if DISCLAIMS.search(line):
                continue
            for version in VERSION.findall(line):
                if not _reached(version, tags, on_main):
                    offences.append(
                        f"{rel}:{number}: says v{version} is out; git shows no "
                        f"tag and no commit on {ref} carrying it - "
                        f"{line.strip()[:70]}")
    assert not offences, (
        "a document claims a version state git does not support. Either tag "
        "the version, or say which of the three states it is in - "
        f"`bench/HISTORY.md` defines them: {offences}")
