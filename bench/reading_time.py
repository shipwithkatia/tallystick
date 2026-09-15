"""How the OpenAI reader's time grows with the size of a log.

    .venv/bin/python bench/reading_time.py            # N = 100 ... 1600
    .venv/bin/python bench/reading_time.py 50 100 200

Three shapes, each with ~21 KB of code per call and a different first line in
every call, so the per-call cache cannot make a long log look cheap:

  turns       N turns, one call each, results matched by id
  wide-ids    one turn of N parallel calls, results matched by id
  wide-none   one turn of N parallel calls, results with neither id nor name
  wide-name   one turn of N parallel calls, results with a name but no id
  wide-names  one turn of N parallel calls of N DIFFERENT tools, results
              placed by position

The last shape exists because the other four use a single tool name, and the
work this reader does per result over the set of distinct names was invisible
in them. It was quadratic: at N=4000 the same calls took 4.0 s under N names
against 0.04 s under one.

Linear growth doubles the time when N doubles; the ratio column says whether it
did. Best of three runs per size. Run it alone: another busy process skews it.

Measured in round 10 a second way as well - every reading in a fresh Python
process, where no cache can be warm - best of three processes, seconds at
N=100 / N=200. The two ways agreed within a few percent.

  shape        proverka7      proverka8      round 10
  turns        1.25 / 2.54    1.21 / 2.48    1.56 / 3.11
  wide-ids     1.25 / 2.58    1.19 / 2.42    1.54 / 3.04
  wide-none    0.11 / 0.22    0.49 / 0.97    0.69 / 1.36
  wide-name    0.12 / 0.23    0.49 / 0.96    0.67 / 1.34
  wide-names   0.11 / 0.21    0.48 / 0.96    0.66 / 1.32

proverka8 was reported as about twice as slow as proverka7 on the wide shapes.
That figure came from this script while it still missed a cache; with every
cache cleared it is 4.2 to 4.7 times as slow. Round 10 adds 1.36 to 1.40 times
on the wide shapes and 1.26 to 1.30 on the others - measured while every echo
comparison both cleaned its text and folded case. Round 11 took case folding
out and did not measure again, so these round-10 figures are not this code's.
Growth stays linear in all five.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tallystick.adapters import openai_chat  # noqa: E402
from tallystick.adapters.openai_chat import to_trace  # noqa: E402

#: The reader caches the pieces of a call's arguments and the candidates of a
#: result. A second run over the same log would hit those caches and measure
#: nothing, so every run starts with them empty: the time is a first reading's.
#:
#: Found by asking the module, not listed by hand. A hand-kept list missed three
#: caches in round 7 and a sixth one in round 8, added by the same commit that
#: fixed the list, and every time printed then was a warm reading - up to 111%
#: too fast. tests/test_round10.py finds the caches a second way, from the source.
CACHES = tuple(obj for obj in vars(openai_chat).values()
               if callable(getattr(obj, "cache_clear", None)))

BODY = 'x = compute("value")\n' * 1000       # about 21 KB


def _call(k, with_id, name="python"):
    call = {"type": "function",
            "function": {"name": name,
                         "arguments": json.dumps({"code": f"# call {k}\n{BODY}"})}}
    if with_id:
        call["id"] = f"c{k}"
    return call


def _result(k, mode):
    message = {"role": "tool", "content": f"result {k}: {k * 7}"}
    if mode == "ids":
        message.update(tool_call_id=f"c{k}", name="python")
    elif mode == "name":
        message["name"] = "python"
    return message


def log(shape, n):
    messages = [{"role": "user", "content": "q"}]
    if shape == "wide-names":
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [_call(k, False, f"tool_{k}") for k in range(n)]})
        messages += [_result(k, "none") for k in range(n)]
        messages.append({"role": "assistant", "content": "done"})
        return messages
    if shape == "turns":
        for k in range(n):
            messages.append({"role": "assistant", "content": None, "tool_calls": [_call(k, True)]})
            messages.append(_result(k, "ids"))
    else:
        mode = shape.split("-", 1)[1]
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [_call(k, mode == "ids") for k in range(n)]})
        messages += [_result(k, mode) for k in range(n)]
    messages.append({"role": "assistant", "content": "done"})
    return messages


def best_of(messages, runs=3):
    best = float("inf")
    for _ in range(runs):
        for cache in CACHES:
            cache.cache_clear()
        start = time.perf_counter()
        to_trace(messages)
        best = min(best, time.perf_counter() - start)
    return best


def main(argv):
    sizes = [int(a) for a in argv] or [100, 200, 400, 800, 1600]
    for shape in ("turns", "wide-ids", "wide-none", "wide-name", "wide-names"):
        previous = None
        for n in sizes:
            seconds = best_of(log(shape, n))
            ratio = f"x{seconds / previous:.2f}" if previous else ""
            print(f"{shape:<11} N={n:<5} {seconds:7.3f} s  {ratio}")
            previous = seconds
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
