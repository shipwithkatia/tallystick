"""One door for every log shape: read what a person has, hand back a trace.

    from tallystick.convert import read_any
    raw, source = read_any(json.loads(text), source="auto")

`tallystick` has one on-disk format, and almost nobody's agent writes it. That
is the whole reason a first-time user bounces off the tool: the file they have
is an OpenAI message list or a span export, and until now the answer was "yours
is not a trace". This module is the translation layer, and it holds three
rules.

**Nothing is converted silently on a guess.** `--from auto` will convert a file
whose shape is unmistakable, and it says in the output which reader it used.
Where two readers could both claim a file, it refuses and names them, because a
trace read the wrong way produces a confident audit of a run that did not
happen - which is worse than an error.

**A tallystick trace is never re-read by an adapter.** If the file already has
`artifacts` or `steps`, that is what it is.

**Every reader records what it dropped.** `_meta` carries the skipped, dropped
and truncated lists, and `check-trace` reports them. A reader that quietly
loses a tool result makes the recording look better than it is, which is the
one failure this project exists to catch.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .adapters import openai_chat, otel_genai
from .adapters.openai_chat import DEFAULT_MAX_TOOL_CHARS
from .types import TraceError

#: `auto` is not a format; it is the instruction to work one out.
FORMATS = ("auto", "tallystick", "openai", "otel")

_READERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "openai": openai_chat.to_trace,
    "otel": otel_genai.to_trace,
}

_LOOKS: Dict[str, Callable[[Any], bool]] = {
    "openai": openai_chat.looks_like_openai,
    "otel": otel_genai.looks_like_otel,
}

_NAMES = {
    "tallystick": "a tallystick trace (artifacts + steps)",
    "openai": "an OpenAI chat message list",
    "otel": "an OpenTelemetry GenAI span export",
}


def looks_like_trace(data: Any) -> bool:
    """The native format, by the same test `load_run` uses to reject a foreign
    file, so the two can never disagree about what a trace is.

    Deliberately the bare key test. A file with an `artifacts` or `steps` key is
    claiming to be a trace, and the most useful thing that can happen to it is
    `load_run` saying precisely how it is malformed - "artifacts[0] is missing
    'artifact_id'" beats "this is not a shape tallystick can read". An agent log
    that merely calls its own list `steps` (LangGraph, smolagents) is claimed
    here too and then fails to load; `hint_for` is what rescues it, by naming
    the reader that would have read it."""
    return isinstance(data, dict) and ("artifacts" in data or "steps" in data)


def hint_for(data: Any) -> str:
    """A sentence to append when reading a file as a trace failed, naming any
    reader that would take it. Without this a smolagents log gets "steps[0] is
    missing 'step_id'" and no way forward - the "yours is not a trace" bounce
    this release exists to remove."""
    found = [name for name in ("otel", "openai") if _LOOKS[name](data)]
    if not found:
        return ""
    if looks_like_trace(data):
        # It claims to be a trace AND an adapter would take it. Reading it as
        # the adapter would silently ignore the artifacts or steps it carries,
        # so this is a question, not a recommendation - and it outranks the
        # ambiguity below, because the trace keys are the stronger signal.
        return (f" It also parses as {describe(found)}, but it carries a trace's "
                f"own keys, so check which of the two it is before reading it "
                f"as the other.")
    if len(found) > 1:
        # The same refusal `--from auto` makes, for the same reason: a guess
        # between two readers is a confident audit of a run that did not
        # happen. Name them; do not pick one.
        return (" It could also be read as " + describe(found)
                + " - say which, with --from.")
    return (" It does look like " + describe(found)
            + f", though: try --from {found[0]}.")


def detect(data: Any) -> List[str]:
    """Every format whose shape this file could be, most specific first."""
    if looks_like_trace(data):
        return ["tallystick"]
    # OTel is tested first: a span export is a far narrower shape than a message
    # list, so a file that matches both is an OTel export whose spans happen to
    # carry a `messages` key, not the other way round.
    return [name for name in ("otel", "openai") if _LOOKS[name](data)]


def describe(names: Iterable[str]) -> str:
    return ", ".join(_NAMES.get(n, n) for n in names)


#: A trace this many times the size of the log it was read from is said out
#: loud. Named before it was measured - an order of magnitude - not fitted.
#:
#: Why a trace can be that much bigger: `inputs` in the format is an explicit
#: list, and a chat sends its whole history every turn, so every step of a
#: converted chat lists every artifact recorded before it. Text is stored once;
#: the ids are repeated, and their count grows with the square of the turns. The
#: format stays as it is - a changed format would be read by an older core with
#: no error and a wrong verdict - so the growth is reported instead of hidden.
TRACE_SIZE_NOTE_RATIO = 10


def json_bytes(obj: Any) -> int:
    """How many bytes `obj` takes written the way `tallystick convert` writes a
    file: indented, UTF-8, non-ASCII kept."""
    return len(json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"))


def trace_size(log: Any, trace: Dict[str, Any],
               trace_bytes: Optional[int] = None,
               log_disk_bytes: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """How much bigger a converted trace is than the log it was read from, or
    None when it is less than TRACE_SIZE_NOTE_RATIO times.

    The ratio that DECIDES is measured the same way on both sides, as the JSON
    `convert` writes: it then says what the reading added, and does not move
    when someone pretty-prints their log. `log_disk_bytes` never touches it. It
    is the log's size on disk, reported alongside so that every number in the
    line can be checked with `ls` - a line that called a 67 KB file "109 KB",
    its size after reformatting, sent a person looking for a number that was
    nowhere on their machine.

    None, too, when the text cannot be written as UTF-8 at all (a lone
    surrogate): there is then no file whose size the line could name, and a
    line about size is no reason for `check-trace` to fail (review 13, 6.2).

    The price of deciding on the first ratio and not the second: a log stored
    compactly - an API response, a JSONL line - can grow more than tenfold on
    disk while the reading itself added less, and then nothing is said. Measured
    on one such log of 300 turns: 11.6x on disk, 7.2x added, silence.

    `trace_bytes` is the size of a file already written, to spare a second
    serialisation of a trace that may be a hundred megabytes; `convert` writes
    with the same indentation this measures, so the two agree byte for byte."""
    try:
        log_bytes = json_bytes(log)
        if not log_bytes:
            return None
        if trace_bytes is None:
            trace_bytes = json_bytes(trace)
    except UnicodeEncodeError:
        return None
    ratio = trace_bytes / log_bytes
    if ratio < TRACE_SIZE_NOTE_RATIO:
        return None
    steps = trace.get("steps") or []
    size = {"log_bytes": log_bytes, "trace_bytes": trace_bytes, "ratio": round(ratio, 2),
            "steps": len(steps),
            "input_references": sum(len(s.get("inputs") or []) for s in steps)}
    if log_disk_bytes:
        size["log_disk_bytes"] = log_disk_bytes
        size["disk_ratio"] = round(trace_bytes / log_disk_bytes, 2)
    return size


def trace_size_line(size: Dict[str, Any]) -> str:
    """The sentence a person reads about `trace_size`.

    Where the log came from a file, the first three numbers are the two files
    and the ratio between them - all three checkable with `ls` - and what the
    reading added is said separately, named as the measure it is."""
    def amount(n: int) -> str:
        return f"{n / 1048576:,.1f} MB" if n >= 1048576 else f"{n / 1024:,.0f} KB"
    if "log_disk_bytes" in size:
        head = (f"trace size: the trace is {amount(size['trace_bytes'])} from a log of "
                f"{amount(size['log_disk_bytes'])} on disk ({size['disk_ratio']:.0f}x). "
                f"Measured as tallystick writes JSON on both sides, the reading added "
                f"{size['ratio']:.0f}x")
    else:
        head = (f"trace size: the reading added {size['ratio']:.0f}x "
                f"({amount(size['log_bytes'])} -> {amount(size['trace_bytes'])}, both "
                f"as tallystick writes JSON)")
    return (f"{head}. {size['steps']} steps hold {size['input_references']:,} references "
            f"to earlier artifacts: a chat sends its whole history every turn, and the "
            f"format records each step's inputs as a list, so the trace grows with the "
            f"square of the turns; the text itself is stored once. Nothing is wrong "
            f"with the run (docs/auditable-traces.md, 'Steps, with their real inputs').")


def read_any(data: Any, *, source: str = "auto", name: str = "",
             max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
             model_text_tools: Optional[Iterable[str]] = None,
             verbatim_tools: Optional[Iterable[str]] = None,
             external_tools: Optional[Iterable[str]] = None
             ) -> Tuple[Dict[str, Any], str]:
    """Return `(raw trace dict, the format it was read as)`.

    Raises TraceError on anything unreadable - the CLI turns that into exit 2,
    never into a verdict."""
    if source not in FORMATS:
        raise TraceError(f"unknown format {source!r}; choose from {', '.join(FORMATS)}")

    if source == "tallystick":
        if not looks_like_trace(data):
            found = detect(data)
            extra = (f" It looks like {describe(found)}; try --from {found[0]}."
                     if found else "")
            raise TraceError(
                "this file has no 'artifacts' or 'steps' key, so it is not a "
                "tallystick trace." + extra)
        return data, "tallystick"

    if source != "auto":
        return _read_as(source, data, name=name, max_tool_chars=max_tool_chars,
                        model_text_tools=model_text_tools, verbatim_tools=verbatim_tools,
                        external_tools=external_tools), source

    found = detect(data)
    if not found:
        raise TraceError(
            "this file is not a shape tallystick can read. It is not a trace "
            "(no 'artifacts' or 'steps'), not an OpenAI chat message list and "
            "not an OpenTelemetry GenAI span export. docs/auditable-traces.md "
            "says what a trace has to contain, and tallystick/adapters/ has the "
            "converters")
    if len(found) > 1:
        raise TraceError(
            f"this file could be read as {describe(found)}, and reading it the "
            f"wrong way would produce a confident audit of a run that did not "
            f"happen. Say which it is: --from {found[0]} or --from {found[1]}")
    only = found[0]
    if only == "tallystick":
        return data, "tallystick"
    return _read_as(only, data, name=name, max_tool_chars=max_tool_chars,
                    model_text_tools=model_text_tools, verbatim_tools=verbatim_tools,
                    external_tools=external_tools), only


def _read_as(source: str, data: Any, **options: Any) -> Dict[str, Any]:
    """One reader on one file, with every way it can fail to read turned into
    TraceError - exit 2 at the command line.

    Including RecursionError. A reader walks nested JSON, and a log can nest
    deeper than Python recurses: tool-call arguments nested 1,500 deep left
    `check-trace` and `convert` on a traceback, exit 1 - "not auditable" from
    one and a code the other never returns (review 20, 2.5). Guarding each walk
    one by one found six such places in six rounds; this is the door every
    reader goes through."""
    try:
        return _READERS[source](data, **options)
    except ValueError as exc:
        raise TraceError(f"cannot read this as {_NAMES[source]}: {exc}") from None
    except RecursionError:
        raise TraceError(f"cannot read this as {_NAMES[source]}: it is nested more deeply "
                         f"than this reader can follow") from None
