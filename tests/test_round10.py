"""Round 10: nothing the reading caught may leave as a silent "it balances".

The ninth review found four ways a result the reader had already caught - or
could not finish weighing - went out with exit 0. Three of them were about the
echo warnings (MAX_STARTS, the threshold-free cross-turn value, what an
external declaration cleared), and their tests were removed with the warnings
in round 18. What stays: the demotion keeps case, a non-breaking space does not
stop it, the timing bench clears every cache, and the trace-growth xfail.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from tallystick.adapters import openai_chat as oc

ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"role": "user", "content": "go"}
PAYLOAD = ("The plant recorded four point six million tonnes of output last year "
           "and the board approved the expansion without further review")


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name,
                         "arguments": args if isinstance(args, str) else json.dumps(args)}}


def _meta(messages, **kw):
    return oc.to_trace(messages, **kw)["_meta"]


# --- point 4.2: case and invisible characters, on the demotion ---------------

def test_the_demotion_keeps_case():
    # Folding case in the demotion demoted 61 more results on AgentHallu, and
    # every one read was real work - this is the commonest: a ticker lookup.
    # `ZETA` is not the model's `Zeta Corp` handed back; it is what the tool
    # looked up.
    meta = _meta(_answered({"name": "Zeta Corp"}, json.dumps({"symbol": "ZETA"})))
    assert not meta["echoed_back_tool_results"], meta["echoed_back_tool_results"]


def _answered(args, reply):
    return [QUESTION,
            {"role": "assistant", "content": "save",
             "tool_calls": [_call("notes", args, "c1")]},
            {"role": "tool", "tool_call_id": "c1", "name": "notes", "content": reply},
            {"role": "assistant", "content": "done"}]


@pytest.mark.parametrize("reply", [
    pytest.param(json.dumps({"text": PAYLOAD}, ensure_ascii=False).replace(" ", " "),
                 id="json-reply-non-breaking-spaces"),
    pytest.param(f"Saved note: {PAYLOAD}".replace(" ", " "),
                 id="label-reply-non-breaking-spaces"),
    pytest.param(json.dumps({"text": PAYLOAD}).replace(" ", " "),
                 id="json-reply-narrow-no-break-spaces"),
])
def test_a_non_breaking_space_does_not_stop_the_demotion(reply):
    meta = _meta(_answered({"text": PAYLOAD}, reply))
    assert meta["echoed_back_tool_results"], "the reply IS the value it was given"


# --- point 6: the timing bench clears every cache the reader has -------------

def _cached_functions():
    """Every function in the package decorated with a functools cache, found
    by reading the source - not by asking the module, which is how the bench
    finds them."""
    found = set()
    for path in sorted((ROOT / "tallystick").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if name in ("lru_cache", "cache"):
                    found.add(node.name)
    return found


def test_the_timing_bench_clears_every_cache():
    spec = importlib.util.spec_from_file_location("reading_time", ROOT / "bench" / "reading_time.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    cleared = {getattr(c, "__wrapped__", c).__name__ for c in bench.CACHES}
    in_code = _cached_functions()
    assert in_code, "no cached function found: the scan itself is broken"
    missing = in_code - cleared
    assert not missing, (
        f"the reader caches {sorted(missing)} and bench/reading_time.py does not "
        f"clear it: every time it prints is then a warm-cache time")


# --- point 1: the trace grows with the square of the turns -------------------

def _long(turns):
    messages = [QUESTION]
    for k in range(turns):
        messages += [{"role": "assistant", "content": f"step {k}",
                      "tool_calls": [_call("search", {"q": f"item {k}"}, f"c{k}")]},
                     {"role": "tool", "tool_call_id": f"c{k}", "name": "search",
                      "content": f"result {k}"}]
    messages.append({"role": "assistant", "content": "done"})
    return messages


@pytest.mark.xfail(strict=True, reason=(
    "not fixed in round 10: every step lists every artifact before it, because "
    "the format's `inputs` is an explicit list and the verifier checks a citation "
    "against it. Linear output needs a format change; see docs/auditable-traces.md"))
def test_the_trace_grows_linearly_with_the_number_of_turns():
    small = len(json.dumps(oc.to_trace(_long(200))))
    large = len(json.dumps(oc.to_trace(_long(400))))
    # A budget on the SIZE of the output, not only on time: doubling the turns
    # may at most double the file, with room for ids growing a digit.
    assert large < 2.3 * small, f"200 turns: {small} B, 400 turns: {large} B"
