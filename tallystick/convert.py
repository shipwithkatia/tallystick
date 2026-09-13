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


def read_any(data: Any, *, source: str = "auto", name: str = "",
             max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
             model_text_tools: Optional[Iterable[str]] = None,
             verbatim_tools: Optional[Iterable[str]] = None
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
        try:
            return _READERS[source](data, name=name, max_tool_chars=max_tool_chars,
                                    model_text_tools=model_text_tools,
                                    verbatim_tools=verbatim_tools), source
        except ValueError as exc:
            raise TraceError(f"cannot read this as {_NAMES[source]}: {exc}") from None

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
    try:
        return _READERS[only](data, name=name, max_tool_chars=max_tool_chars,
                              model_text_tools=model_text_tools,
                              verbatim_tools=verbatim_tools), only
    except ValueError as exc:
        raise TraceError(f"cannot read this as {_NAMES[only]}: {exc}") from None
