"""How the OpenAI reader's time grows with the size of a log.

    .venv/bin/python bench/reading_time.py            # N = 100 ... 1600
    .venv/bin/python bench/reading_time.py 50 100 200

Three shapes, each with ~21 KB of code per call and a different first line in
every call, so the per-call cache cannot make a long log look cheap:

  turns      N turns, one call each, results matched by id
  wide-ids   one turn of N parallel calls, results matched by id
  wide-none  one turn of N parallel calls, results with neither id nor name
  wide-name  one turn of N parallel calls, results with a name but no id

Linear growth doubles the time when N doubles; the ratio column says whether it
did. Best of three runs per size. Run it alone: another busy process skews it.
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
CACHES = (openai_chat._pieces, openai_chat._echo_candidates)

BODY = 'x = compute("value")\n' * 1000       # about 21 KB


def _call(k, with_id):
    call = {"type": "function",
            "function": {"name": "python",
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
    for shape in ("turns", "wide-ids", "wide-none", "wide-name"):
        previous = None
        for n in sizes:
            seconds = best_of(log(shape, n))
            ratio = f"x{seconds / previous:.2f}" if previous else ""
            print(f"{shape:<10} N={n:<5} {seconds:7.3f} s  {ratio}")
            previous = seconds
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
