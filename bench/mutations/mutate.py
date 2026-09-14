"""Break one thing in a copy of the code, run the suite, see which tests notice.

    .venv/bin/python bench/mutations/mutate.py                 # HEAD, every mutation
    .venv/bin/python bench/mutations/mutate.py proverka4 --only M1,M5
    .venv/bin/python bench/mutations/mutate.py --out mutations.json

This is mutation testing. A test that still passes when the behaviour it is
named after has been removed is not guarding that behaviour; a test that passes
under two opposite mutations passes whatever the code does.

The copy is made with `git archive REF` in a temporary directory, so the working
tree is never touched and uncommitted edits are NOT in the copy - commit first.
The AgentHallu corpus (gitignored) is symlinked in when it exists; without it
the corpus tests skip.

Each mutation replaces an exact snippet of source. After a rewrite a snippet
may no longer exist: that mutation is reported STALE and skipped rather than
run as a silent no-op. Update its snippet, or delete it.

Printed at the end:
  - per mutation, the tests that failed (the ones that noticed);
  - tests in the FOCUS files that pass under every mutation run;
  - tests that pass under both M3 (never demote) and M4 (demote everything);
  - tests that fail when the echo warning is removed (M1) but pass again when
    every result is merely listed in another `_meta` list (M5).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OC = "tallystick/adapters/openai_chat.py"
CLI = "tallystick/cli.py"
GATE = "tallystick/echo_gate.py"
_SPLIT = '(accepted if w["tool"] and w["tool"] in confirmed else unreviewed).append(w)'

#: name -> [(file, exact snippet, replacement)]. Each snippet must occur once.
#: Snippets follow the code as of proverka7; on an older ref they report STALE.
MUTATIONS = {
    "M0_none": [],
    # The reader never reports an echo from an earlier turn.
    "M1_no_earlier_echo_report": [(OC, "if piece in earlier_pieces:", "if False:")],
    # The gate never blocks: every warning counts as reviewed.
    "M2_gate_never_blocks": [(GATE, _SPLIT, "accepted.append(w)")],
    # The answering-call rule never demotes.
    "M3_rule_never_demotes": [(OC, '    if not sent:\n        return False\n    return bool(_is_a_value_of(result, sent, whole_only=True))', "    return False")],
    # The answering-call rule demotes every tool result.
    "M4_rule_demotes_every_result": [(OC, '    if not sent:\n        return False\n    return bool(_is_a_value_of(result, sent, whole_only=True))', "    return True")],
    # M1, and every tool result is written into guessed_tool_names: a test that
    # only asks "is this result named anywhere in _meta" passes again.
    "M5_no_report_but_every_result_in_guessed": [
        (OC, "if piece in earlier_pieces:", "if False:"),
        (OC, "            aid = f\"t{k}\"\n",
         "            aid = f\"t{k}\"\n            guessed.append(f\"tool[{k}] -> x\")\n")],
    # Naming any tool confirms every warning.
    "M6_any_confirmation_accepts_all": [
        (GATE, _SPLIT, "(accepted if confirmed else unreviewed).append(w)")],
    # propose writes the posted trace without the reading's _meta.
    "M7_propose_drops_meta": [(CLI, "        posted[\"_meta\"] = meta\n", "        pass\n")],
    # audit ignores whether the books balance.
    "M8_audit_ignores_books": [(CLI, "if not balance.books_balance:", "if False:")],
    # The reader stops writing the structured warning records.
    "M9_no_details_recorded": [(OC, "        \"echo_warning_details\": earlier_echo_details,\n", "")],

    # --- proverka7 -----------------------------------------------------------
    # Eleven boundaries of the rewritten echo path. Every one of these went
    # unnoticed by the whole suite in the sixth review, or guards something the
    # sixth review found broken; each now has a test that fails without it.

    # A one-character match counts as a value, whatever the reply is.
    "K1_no_one_character_guard": [
        (OC, "        if form in values and (len(form) > 1 or form == whole):",
             "        if form in values:")],
    # The reply itself is no longer a candidate: only what stands inside it.
    "K4_no_whole_reply_candidate": [
        (OC, "    for text in _reply_texts(result):\n        yield from _forms(text)\n",
             "    for text in _reply_texts(result):\n")],
    # A reply that is JSON carrying one value no longer offers that value.
    "K6_no_single_json_value": [
        (OC, "                values = _json_values(stripped)\n"
             "                if len(values) == 1:\n"
             "                    yield from _forms(values[0])",
             "                values = ()\n"
             "                if values:\n"
             "                    pass")],
    # An external declaration vouches even for a tool the log never named -
    # the protection commit 7760068 states in words and nothing tested.
    "K9_external_vouches_without_a_name": [
        (OC, "vouched = name_known and dkey in external", "vouched = dkey in external")],
    # A reply is never unescaped before it is compared.
    "K14_never_unescape_the_reply": [(OC, "UNESCAPE_RESULT_CHARS = 2000", "UNESCAPE_RESULT_CHARS = 0")],
    # The arguments are compared only as written, with no escapes undone.
    "K15_no_unescaped_spellings": [
        (OC, "    once = _unescape(text)\n    return text, once, _unescape(once)",
             "    return text, text, text")],
    # Partial coverage never reaches the warning threshold.
    "K16_coverage_never_warns": [(OC, "WARN_SHARE = 0.10", "WARN_SHARE = 1.10")],
    # A line of the reply is no longer a candidate for the demotion.
    "K17_no_line_candidate": [
        (OC, "            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]\n            for line in lines:\n                yield from _forms(line)\n",
             "            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]\n")],
    # Trailing punctuation is not trimmed at all. What fixes the sixth review's
    # second hole is that the same trimming runs over the arguments AND over
    # the reply; trimming a run rather than one mark at a time turns out to
    # change nothing once both sides do it, and this mutation is the one that
    # does.
    "K18_no_trailing_punctuation_trim": [
        (OC, "            if value and value[-1] in _TRAILING:", "            if False:")],
    # Declarations are compared byte for byte again.
    "K19_declarations_compared_byte_for_byte": [
        (OC, "return str(name).strip().casefold()", "return str(name)")],
    # The placeholder is kept out of the declarations twice - `_declared` drops
    # the name, and a result whose name the log never gave carries no key at
    # all. Either guard alone holds, so the mutation removes both.
    "K20_the_placeholder_can_be_declared": [
        (OC, 'dkey = _key(tool) if named_by_log else ""', "dkey = _key(tool)"),
        (OC, "        if key and key != PLACEHOLDER:", "        if key:")],
}

FOCUS = ("test_openai_chat_", "test_check_trace_echo_gate", "test_audit_echo_gate",
         "test_openai_roundtrip_numbers")


def copy_of(ref: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", ref], cwd=REPO, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    corpus = REPO / "bench" / "work-agenthallu"
    if corpus.exists():
        (dest / "bench").mkdir(exist_ok=True)
        (dest / "bench" / "work-agenthallu").symlink_to(corpus)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ref", nargs="?", default="HEAD", help="git ref to copy (default HEAD)")
    ap.add_argument("--only", help="comma-separated mutation name prefixes, e.g. M1,M5")
    ap.add_argument("--out", metavar="PATH", help="write pass/fail sets as JSON here")
    args = ap.parse_args()

    names = list(MUTATIONS)
    if args.only:
        wanted = [w.strip() for w in args.only.split(",")]
        names = [n for n in names if any(n.startswith(w) for w in wanted)]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    work = Path(tempfile.mkdtemp(prefix="tallystick-mutations-"))
    results: dict = {}
    try:
        for name in names:
            d = work / name
            copy_of(args.ref, d)
            stale = []
            for rel, old, new in MUTATIONS[name]:
                path = d / rel
                text = path.read_text(encoding="utf-8")
                if text.count(old) != 1:
                    stale.append(f"{rel}: snippet found {text.count(old)} times")
                    continue
                path.write_text(text.replace(old, new), encoding="utf-8")
            if stale:
                print(f"{name:<44} STALE, skipped ({'; '.join(stale)})")
                continue
            proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "-q"],
                                  cwd=d, capture_output=True, text=True, env=env)
            passed = sorted(set(re.findall(r"^PASSED (\S+)", proc.stdout, re.M)))
            failed = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.M)))
            tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "(no output)"
            results[name] = {"passed": passed, "failed": failed, "summary": tail}
            print(f"{name:<44} {tail}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    def short(t):
        return t.split("tests/", 1)[-1]

    for name, r in results.items():
        if name != "M0_none" and r["failed"]:
            print(f"\n{name}: noticed by {len(r['failed'])}")
            for t in r["failed"]:
                print(f"    {short(t)}")

    run = [n for n in results if n != "M0_none"]
    if run:
        every = set.intersection(*(set(results[n]["passed"]) for n in run))
        print(f"\nFocus tests passing under every mutation run ({len(run)}):")
        for t in sorted(every):
            if any(f in t for f in FOCUS):
                print(f"    {short(t)}")
    pairs = (("M3_rule_never_demotes", "M4_rule_demotes_every_result", "pass under both M3 and M4"),)
    for a, b, label in pairs:
        if a in results and b in results:
            both = set(results[a]["passed"]) & set(results[b]["passed"])
            print(f"\nopenai_chat tests that {label}:")
            for t in sorted(both):
                if "test_openai_chat_" in t:
                    print(f"    {short(t)}")
    m1, m5 = "M1_no_earlier_echo_report", "M5_no_report_but_every_result_in_guessed"
    if m1 in results and m5 in results:
        print("\nfail without the echo warning (M1) but pass once results are named elsewhere in _meta (M5):")
        for t in sorted(set(results[m1]["failed"]) - set(results[m5]["failed"])):
            print(f"    {short(t)}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
