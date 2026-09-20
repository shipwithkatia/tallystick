# Mutation runs

Tooling for checking the tests: does a test actually guard the behaviour it is
named after?

```bash
.venv/bin/python bench/mutations/mutate.py            # HEAD, every mutation, one suite run each
.venv/bin/python bench/mutations/mutate.py 4cdf21f --only M1,M5
```

Each mutation breaks one thing in a `git archive` copy of the chosen ref (the
working tree is not touched; uncommitted edits are not included) and runs the
whole suite there. The script prints which tests noticed each mutation, and
three lists worth reading:

- focus tests that pass under every mutation - candidates for tests that pass
  whatever the code does;
- tests that pass both when the echo rule never demotes (M3) and when it demotes
  everything (M4);
- tests that fail when the echo warning is removed (M1) but pass again when
  every result is merely named in another `_meta` list (M5).

Mutations are exact source snippets. After a rewrite a snippet can disappear;
the script then reports that mutation as `STALE` instead of running a no-op.

Six of the nine M mutations - M1 to M6 - carry a second set of snippets, for
`4cdf21f`, the ref the table below records, taken from this script as of
`9deaac4`, the commit the table was made with. M7 to M9 carry none and need
none: their current snippets still stand in that ref exactly once, and a second
set that is never reached would hide drift in the first. That is why the check
below prints six and not nine. The current set is tried first, so a sweep of `HEAD` reads
as it did before. That the older set is still the recorded one, and still fits
`4cdf21f`, is itself checked:

```bash
python3 bench/mutations/check_snippets.py   # 6 mutation(s) pinned to 9deaac4, 0 drifted
```

## What the table records

**One commit, not the code in this checkout.** The nine numbers were taken on
`4cdf21f`, whose `tallystick/` and `tests/` are the code of `48c8b8a`:

```bash
git diff --stat 48c8b8a 4cdf21f -- tallystick tests   # empty: the same code
```

To ask the same question of today's code, run the script on `HEAD` and read its
own output. Two of the nine cannot be asked there at all: the gate M2 and M6
break was taken out in round 18 (`tallystick/echo_gate.py` says so at the top,
and why), so both report `STALE` on `HEAD`; no snippet brings back behaviour the
code no longer has.

**Which interpreter, and whether the corpus is there.** The size of the suite
depends on the interpreter and what passes depends on the corpus, so both are
named here. Under `.venv`, which has `langchain_core` installed, `4cdf21f` holds
424 tests; under a system `python3` without the library, 403. Eight of those
tests skip when AgentHallu is not attached, which is what a fresh clone has:

```bash
.venv/bin/python bench/mutations/mutate.py 4cdf21f --only M0_none  # 416 passed, 8 skipped
python3 bench/mutations/mutate.py 4cdf21f --only M0_none           # 395 passed, 9 skipped
```

With the corpus symlinked in, the same two commands give 424 passed and 403
passed, 1 skipped. The ninth skip under `python3` is the langchain module itself, which
skips at its own first line rather than test by test: 395 + 8 = 403 collected,
and the module's skip is the one on top.

The 21 between the two are one file, which stops at its own first line of code
when the library is missing, so the whole module counts as one skip instead of
21 tests - and 403 + 21 = 424:

```bash
.venv/bin/python -m pytest -q --collect-only tests/test_adapters_langchain.py | tail -1
                                                       # 21 tests collected
sed -n 15p tests/test_adapters_langchain.py   # pytest.importorskip("langchain_core")
```

(Those two run in this checkout, where the file and its line 15 are unchanged
since `4cdf21f`.)

A second path agrees with each of the two suite counts separately. That is a
weaker statement than it looks: the same interpreter gives the same number by
either path, which is not the same as the number being the same in any
environment.

```bash
git worktree add --detach <dir> 4cdf21f
cd <dir> && python3 -m pytest -q --collect-only | tail -1                   # 403 tests collected
cd <dir> && <repo>/.venv/bin/python -m pytest -q --collect-only | tail -1   # 424 tests collected
```

The nine numbers in the table, unlike the size of the suite, come out the same
under both.

**With the AgentHallu corpus attached.** It is not in this repository:

```bash
git clone https://github.com/liuxuannan/AgentHallu bench/work-agenthallu/AgentHallu
```

The script symlinks it into every copy it makes when it is there. Without it -
which is what a fresh clone has - the same commands give 39 for M3 and 62 for M4;
the other seven rows do not move.

Every row below is one line of this, and `--only M` is the nine and the
unmutated run:

```bash
python3 bench/mutations/mutate.py 4cdf21f --only M
```

| mutation | tests that noticed |
|---|---|
| M1 no earlier-turn echo report | 22 |
| M2 gate never blocks | 13 |
| M3 rule never demotes | 46 |
| M4 rule demotes every result | 70 |
| M5 M1 + every result listed in guessed_tool_names | 23 |
| M6 any confirmation accepts all warnings | 6 |
| M7 propose drops `_meta` | 8 |
| M8 audit ignores the books | 5 |
| M9 no structured warning records | 16 |

`bench/repro_gate_findings.sh` reproduces the rest of the review of 4cdf21f: gate
bypasses, name confirmation, the answering-call rule, the corpus gate rate,
reading time and the commit figures.
