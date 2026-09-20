"""Russian is data here, not voice.

A reader that breaks on Cyrillic is a bug, so `tests/` and `examples/logs/`
carry Russian strings on purpose and are exempt. What is not exempt is the
project speaking: eight section headers of `bench/repro_gate_findings.sh` were
Russian, in a repository whose every other line of prose is English, until this
test existed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: Data, where a non-ASCII string is the point of the file.
EXEMPT = ("tests/", "examples/", "bench/sample/", "bench/sample-agenthallu/",
          "bench/results/")

#: The project's own voice: prose, scripts and source. Suffixes, not globs, so a
#: file added later is covered without anyone remembering to list it.
VOICE_SUFFIXES = (".md", ".py", ".sh", ".yml", ".toml")


def _voice_files():
    """Tracked files only. An untracked draft in the working tree is nobody's
    business but its author's; this test is about what gets published."""
    listed = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.split("\n")
    for rel in listed:
        if rel and rel.endswith(VOICE_SUFFIXES):
            yield ROOT / rel


def test_the_project_speaks_english_outside_its_test_data():
    offences = []
    for path in sorted(set(_voice_files())):
        rel = path.relative_to(ROOT).as_posix()
        if any(rel.startswith(e) for e in EXEMPT):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CYRILLIC.search(line):
                offences.append(f"{rel}:{i}: {line.strip()[:60]}")
    assert not offences, (
        "the repository's own prose is English; Cyrillic belongs in test data "
        f"only: {offences}")
