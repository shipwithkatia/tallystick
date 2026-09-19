# Mutation runs

Tooling for checking the tests: does a test actually guard the behaviour it is
named after?

```bash
.venv/bin/python bench/mutations/mutate.py            # HEAD, all mutations, ~1 minute
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

Recorded on 4cdf21f (the code as of 48c8b8a), 424 tests:

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
