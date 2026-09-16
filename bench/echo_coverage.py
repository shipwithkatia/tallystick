"""How much of a tool result is text from the call it answered - measured, not
thresholded, so a warning threshold can be chosen from the corpus instead of
guessed.

    .venv/bin/python bench/echo_coverage.py bench/work-agenthallu/AgentHallu/AgentHallu

What it measures
----------------
For each tool result R and the arguments A of the call it answers (in this
corpus every call carries an id, so the pairing is exact, never positional):

  * both are normalised - every run of whitespace becomes one space - so a
    newline inserted into the middle of an echo changes nothing;
  * R's words are tiled left to right by maximal runs that occur as a
    contiguous word sequence in A;
  * runs shorter than MIN_RUN_CHARS letters and digits are dropped, so a
    shared "the" is not a match;
  * coverage = the letters and digits of the kept runs, over those of R.

No last line, no "more than half", no requirement that a match hold a space.
The tiling is greedy, which can only UNDER-count: a greedy maximal run is
never longer than the best tiling of the same text.

A trajectory "requires confirmation" at threshold X when any of its tool
results reaches coverage >= X - the point of the proposed rule being that such
a result gets a warning, and a warning holds the exit code at 1 until a person
confirms the tool by name.

Printed against the baseline: what already requires confirmation today, so the
column that matters is what the change ADDS.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tallystick.adapters import agenthallu, openai_chat as oc   # noqa: E402
from openai_roundtrip import render                             # noqa: E402

_NOT_ALNUM = re.compile(r"[\W_]+")

#: A matched run shorter than this is not evidence of anything - two texts in
#: the same language share short strings. Reported at three values because the
#: answer moves with it.
MIN_RUN_CHARS = 8

THRESHOLDS = (0.30, 0.20, 0.10)


def _alnum(text: str) -> int:
    return len(_NOT_ALNUM.sub("", text))


def _values(obj):
    """Every string and number inside parsed arguments, as text."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield str(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)


def haystacks(args: str) -> List[str]:
    """The call's arguments as text to look in: the raw string, and - where the
    arguments are JSON - their values on their own, so that `{"answer": "0"}`
    can be matched by the result `0`. Without this the JSON punctuation welds
    the value to its braces and a whole-result echo measures zero."""
    out = [args]
    # Quoted string literals, for arguments that are code rather than JSON:
    # `final_answer("(A -> B)")` hands the model's text in inside quotes, and
    # the quote welds it to the call around it.
    literals = [m.group(1) for pat in (_LITERAL_DQ, _LITERAL_SQ)
                for m in pat.finditer(args) if m.group(1).strip()]
    if literals:
        out.append("\n".join(literals))
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError, RecursionError):
        return out
    values = list(_values(parsed))
    if values:
        out.append("\n".join(values))
    return out


_LITERAL_DQ = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_LITERAL_SQ = re.compile(r"'((?:[^'\\\n]|\\.)*)'")


def _cover_one(result: str, args: str, min_run: int, total: int) -> float:
    r = result.split()
    a = args.split()
    if not r or not a:
        return 0.0
    where: Dict[str, List[int]] = {}
    for i, w in enumerate(a):
        where.setdefault(w, []).append(i)
    covered = 0
    i = 0
    while i < len(r):
        starts = where.get(r[i])
        if not starts:
            i += 1
            continue
        k = 1                                   # run length in words, so far
        live = starts
        while True:
            nxt = [p for p in live if p + k < len(a) and i + k < len(r)
                   and a[p + k] == r[i + k]]
            if not nxt:
                break
            live = nxt
            k += 1
        run = _alnum("".join(r[i:i + k]))
        # A run counts when it is long enough to mean something, or when it is
        # the whole result: a tool whose entire reply is "0" handed back the
        # "0" it was given, and a length floor must not excuse that.
        if run >= min_run or (i == 0 and i + k == len(r)):
            covered += run
        i += k                                  # non-overlapping, greedy
    return covered / total


def coverage(result: str, args: str, min_run: int) -> float:
    """Share of `result`'s letters and digits standing in the call's arguments
    as contiguous runs - the best any spelling of those arguments gives."""
    total = _alnum(result)
    if not total or not args:
        return 0.0
    return max(_cover_one(result, h, min_run, total) for h in haystacks(args))


def pairs(obj) -> List[Tuple[str, str, str]]:
    """(tool name, arguments, result) for every tool result of a trajectory,
    matched to its call by id."""
    messages = render(obj)
    args_of: Dict[str, Tuple[str, str]] = {}
    out: List[Tuple[str, str, str]] = []
    for m in messages:
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            args_of[c["id"]] = (str(fn.get("name") or ""), str(fn.get("arguments") or ""))
        if m.get("role") == "tool":
            name, args = args_of.get(m.get("tool_call_id"), ("", ""))
            out.append((name or str(m.get("name") or ""), args, str(m.get("content") or "")))
    return out


def main(argv: List[str]) -> int:
    root = Path(argv[0]) if argv else Path("bench/work-agenthallu/AgentHallu/AgentHallu")
    mins = [int(x) for x in argv[1:]] or [MIN_RUN_CHARS]
    files = sorted(root.rglob("*.json"))

    trajectories = []
    for p in files:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(obj, dict) and "history" in obj:
            trajectories.append((p, obj))
    n = len(trajectories)
    if not n:
        # Without the corpus this printed zeros and then divided by them:
        # ZeroDivisionError, exit 1 (review 16, 5.2). Nothing found is said.
        print(f"no AgentHallu trajectories under {root} - nothing to measure. The "
              f"corpus is not in this repository: git clone "
              f"https://github.com/liuxuannan/AgentHallu and pass the clone",
              file=sys.stderr)
        return 2

    # What already holds the exit code at 1 today, in both configurations.
    today = {"no flags": set(), "4 echo tools declared": set()}
    for p, obj in trajectories:
        messages = render(obj)
        for label, flags in (("no flags", {}),
                             ("4 echo tools declared",
                              {"model_text_tools": agenthallu.ECHO_TOOLS})):
            tr = oc.to_trace(messages, name=p.name, **flags)
            if tr["_meta"]["echo_warning_details"]:
                today[label].add(str(p))

    print(f"trajectories: {n}   tool results: "
          f"{sum(len(pairs(o)) for _p, o in trajectories)}")
    print()
    for label, flags_echo in (("no flags", frozenset()),
                              ("4 echo tools declared", frozenset(agenthallu.ECHO_TOOLS))):
        base = today[label]
        print(f"--- {label} ---")
        print(f"  requires confirmation today: {len(base):4d} / {n}  ({len(base)/n:5.1%})")
        for min_run in mins:
            print(f"  minimum matched run: {min_run} letters and digits")
            # the highest coverage any one result of the trajectory reaches,
            # computed once and then read off at each threshold
            peak = {}
            for p, obj in trajectories:
                best = 0.0
                for name, args, result in pairs(obj):
                    if name in flags_echo:
                        continue            # declared model text: no warning
                    best = max(best, coverage(result, args, min_run))
                peak[str(p)] = best
            for t in THRESHOLDS:
                hit = {k for k, v in peak.items() if v >= t}
                total = base | hit
                added = total - base
                print(f"    threshold {t:4.0%}: "
                      f"would warn {len(hit):4d}  "
                      f"total needing confirmation {len(total):4d} ({len(total)/n:5.1%})  "
                      f"ADDED {len(added):4d} ({len(added)/n:5.1%})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# ---------------------------------------------------------------------------
# Prototype of the proverka7 demotion path: exact match, no tunable value.
# Ported into tallystick/adapters/openai_chat.py once the numbers are in.
# ---------------------------------------------------------------------------

_TRAILING = ".,;:!?"


def _spellings(text: str):
    once = oc._unescape(text)
    return (text, once, oc._unescape(once))


def _norm(text: str) -> str:
    return " ".join(text.split())


def _add(found, piece: str) -> None:
    """Keep a candidate in every spelling, whitespace collapsed. A piece with no
    letter or digit is punctuation, never a value."""
    for s in _spellings(piece):
        s = _norm(s)
        if s and any(ch.isalnum() for ch in s):
            found.add(s)


def arg_values(args: str) -> frozenset:
    """The VALUES the call carried - not its words. A word-level set would make
    `the` a value and demote every English sentence."""
    found = set()
    if not args:
        return frozenset()
    try:
        parsed = json.loads(args)
    except (TypeError, ValueError, RecursionError):
        parsed = None
    if parsed is not None:
        for v in _values(parsed):
            _add(found, v)
    else:
        _add(found, args)
    for pat in (_LITERAL_DQ, _LITERAL_SQ):
        for m in pat.finditer(args):
            _add(found, m.group(1))
    for line in args.splitlines():
        _add(found, line.strip())
    # Bare atoms, for arguments that are code: `final_answer(12)` hands the
    # model's answer in unquoted, and nothing above finds it.
    for m in re.finditer(r"[^\W_]+", args):
        _add(found, m.group(0))
    return frozenset(found)


def result_values(result: str) -> frozenset:
    """What the result offers as a value: the whole reply, each of its lines -
    each also with its trailing punctuation trimmed ONE MARK AT A TIME, so a
    sentence that already ended in a period and gained another is still found -
    each quoted literal, each JSON value, and the text after a `label: `."""
    found = set()
    texts = [result]
    if "\\" in result:
        texts.append(oc._unescape(result))
    for text in texts:
        _add(found, text)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            _add(found, line)
            trimmed = line
            while trimmed and trimmed[-1] in _TRAILING:
                trimmed = trimmed[:-1]
                _add(found, trimmed)
            _label, sep, rest = line.partition(": ")
            if sep:
                _add(found, rest)
        for pat in (_LITERAL_DQ, _LITERAL_SQ):
            for m in pat.finditer(text):
                _add(found, m.group(1))
        stripped = text.strip()
        if stripped[:1] in ("{", "[", '"'):
            try:
                for v in _values(json.loads(stripped)):
                    _add(found, v)
            except (TypeError, ValueError, RecursionError):
                pass
    return frozenset(found)


def exact_match(result: str, args: str) -> bool:
    """Is the whole result, or a value inside it, exactly a value the call
    carried? No threshold, no last line, no requirement that it hold a space."""
    if not args:
        return False
    whole = _norm(result)
    hit = result_values(result) & arg_values(args)
    # One character is not a value. It was a stray trailing "e" at the end of a
    # page that once matched the letter "e" in the model's own writing and
    # demoted the only real evidence in that run. A reply that IS one character
    # is a different thing and stays caught. Structural, not a threshold.
    return any(len(h) > 1 or h == whole for h in hit)
