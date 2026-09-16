"""Loading a run from JSON.

The on-disk format is deliberately plain: an agent framework, a log exporter or a
human can produce it without importing this library. Adapters for specific
frameworks belong in `adapters/` and should all funnel into `load_run`.

    {
      "artifacts": [{"artifact_id", "kind", "content", "title"?}],
      "steps":     [{"step_id", "kind", "inputs": [...], "outputs": [...]}],
      "claims":    [{"claim_id", "artifact_id", "start", "end", "text"?, "proposed_by"?}],
      "entries":   [{"entry_id", "claim_id", "account", "quoted_span"?, "proposed_by"?,
                     "group"?}],   group: entries of ONE claim that came from one
                                   quote; the ledger closes a group on its worst member
      "assumptions": {"<id>": "<premise text>"}          (optional)
    }

Malformed input - wrong shape, missing key, wrong type, bad reference - raises
TraceError with a message that names the offending object. Nothing here surfaces
as a bare KeyError, TypeError or AttributeError; the CLI turns TraceError into exit
code 2, which must never be confused with exit code 1 ("books do not balance").

If `text` is omitted from a claim it is read from the artifact span, which is the
better habit. If it is supplied, it must match the span (up to normalisation): a
claim is not allowed to say one thing and point at another.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .types import Account, Artifact, ArtifactKind, Claim, Entry, Run, Step, TraceError


def _obj(raw: Any, what: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise TraceError(f"{what} must be an object, got {type(raw).__name__}")
    return raw


def _list(data: Dict[str, Any], key: str) -> List[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise TraceError(f"{key!r} must be a list, got {type(value).__name__}")
    return value


def _str(raw: Dict[str, Any], key: str, what: str) -> str:
    if key not in raw:
        raise TraceError(f"{what} is missing {key!r}")
    value = raw[key]
    if not isinstance(value, str):
        raise TraceError(f"{what}.{key} must be a string, got {type(value).__name__}")
    return value


def _str_list(raw: Dict[str, Any], key: str, what: str) -> Tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TraceError(f"{what}.{key} must be a list of strings")
    return tuple(value)


def _int(raw: Dict[str, Any], key: str, what: str) -> int:
    if key not in raw:
        raise TraceError(f"{what} is missing {key!r}")
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceError(f"{what}.{key} must be an integer")
    return value


def load_run(data: Dict[str, Any]) -> Run:
    """Build a Run from the on-disk shape. Raises TraceError on anything off."""
    data = _obj(data, "trace")
    # A JSON object with neither key is almost always someone's own agent log -
    # an OpenAI message list, a LangSmith export - handed straight to us. Every
    # field below is optional, so without this the file would load as an empty
    # run and be reported back as "your trace records no steps and no answer":
    # a confident verdict about their recorder, when the truth is that this is
    # not our format. That reads as exit 1 ("bad trace") instead of exit 2
    # ("could not read it"), which is the one distinction the exit codes exist
    # to make.
    if "artifacts" not in data and "steps" not in data:
        raise TraceError(
            "no 'artifacts' or 'steps' key: this does not look like a tallystick "
            "trace. If it is an agent log in another shape, convert it first - "
            "docs/auditable-traces.md says what a trace has to contain, and "
            "tallystick/adapters/ has worked converters")
    run = Run()

    for i, raw in enumerate(_list(data, "artifacts")):
        what = f"artifacts[{i}]"
        raw = _obj(raw, what)
        aid = _str(raw, "artifact_id", what)
        kind_s = _str(raw, "kind", what)
        try:
            kind = ArtifactKind(kind_s)
        except ValueError:
            raise TraceError(
                f"{what}.kind {kind_s!r} is not one of "
                f"{[k.value for k in ArtifactKind]}") from None
        if aid in run.artifacts:
            raise TraceError(f"duplicate artifact id {aid!r}")
        run.artifacts[aid] = Artifact(
            artifact_id=aid, kind=kind, content=_str(raw, "content", what),
            title=str(raw.get("title", "")),
        )

    for i, raw in enumerate(_list(data, "steps")):
        what = f"steps[{i}]"
        raw = _obj(raw, what)
        run.steps.append(Step(
            step_id=_str(raw, "step_id", what),
            kind=str(raw.get("kind", "generate")),
            inputs=_str_list(raw, "inputs", what),
            outputs=_str_list(raw, "outputs", what),
        ))

    for i, raw in enumerate(_list(data, "claims")):
        what = f"claims[{i}]"
        raw = _obj(raw, what)
        cid = _str(raw, "claim_id", what)
        aid = _str(raw, "artifact_id", what)
        if aid not in run.artifacts:
            raise TraceError(f"claim {cid} is in unknown artifact {aid!r}")
        art = run.artifacts[aid]
        start, end = _int(raw, "start", what), _int(raw, "end", what)
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise TraceError(f"claim {cid}.text must be a string")
        if cid in run.claims:
            raise TraceError(f"duplicate claim id {cid!r}")
        run.claims[cid] = Claim(
            claim_id=cid, artifact_id=aid, start=start, end=end,
            text=text or art.slice(start, end),
        )

    assumptions = data.get("assumptions", {})
    if not isinstance(assumptions, dict):
        raise TraceError("'assumptions' must be an object of id -> text")
    for aid, text in assumptions.items():
        run.assumptions[str(aid)] = str(text)

    for i, raw in enumerate(_list(data, "entries")):
        what = f"entries[{i}]"
        raw = _obj(raw, what)
        eid = _str(raw, "entry_id", what)
        run.entries.append(Entry(
            entry_id=eid,
            claim_id=_str(raw, "claim_id", what),
            account=Account.parse(_str(raw, "account", what)),
            quoted_span=str(raw.get("quoted_span", "")),
            proposed_by=str(raw.get("proposed_by", "manual")),
            group=str(raw.get("group", "") or ""),
        ))

    run.validate()
    return run


def read_json_file(path: str | Path):
    """Read a JSON file the way people's files actually arrive.

    `utf-8-sig` rather than `utf-8`: Windows Notepad, Excel exports and several
    logging libraries write a byte-order mark, and plain utf-8 then fails on the
    first character with a message about BOMs that tells a non-programmer
    nothing. utf-8-sig reads both, and strips the mark if it is there.

    Any other encoding is refused rather than guessed. Guessing would decode
    someone's text into different characters, and a tool whose whole claim is
    "this quote is verbatim in that source" cannot afford to silently change
    the letters it is comparing.

    A path that cannot be opened at all - missing, a directory, no permission -
    is a TraceError too. The terminal already exited 2 on it; `audit()` let
    FileNotFoundError out, which `except TraceError`, the documented contract,
    does not catch (review 13, 4.3). One place decides "this file cannot be
    read", for the command and the function alike."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise TraceError(f"{path} cannot be read: {exc.strerror or exc}") from None
    except UnicodeDecodeError:
        raise TraceError(
            f"{path} is not saved as UTF-8, so its text cannot be read without "
            f"guessing what the characters are - and guessing would change the "
            f"letters this tool compares. Re-save the file as UTF-8 "
            f"(in most editors: Save As, and choose UTF-8) and try again"
        ) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TraceError(f"{path} is not valid JSON: {exc}") from None
    except ValueError as exc:
        # Valid JSON that Python refuses to turn into a value: an integer longer
        # than its 4,300-digit limit. Not a JSONDecodeError, so it used to leave
        # `audit()` as a bare ValueError, which `except TraceError`, the
        # documented contract, does not catch (review 16, 3.2).
        raise TraceError(f"{path} holds a value that cannot be read: {exc}") from None
    except RecursionError:
        # Valid JSON grammar, nested deeper than the parser's stack. Not a
        # verdict about anything: uncaught, it left the interpreter with exit 1,
        # the code for "the books do not balance" (review 13, 2.2).
        raise TraceError(
            f"{path} is nested too deeply to read: its arrays or objects go "
            f"deeper than the JSON reader can follow. No agent trace needs that "
            f"depth; the file is probably not one") from None


#: The `_meta` fields a command reads, by the shape it reads them in. A reading
#: writes them; a person or another recorder can write them too, and a field of
#: the wrong type used to escape as TypeError - exit 1, a verdict (review 13, 2.4).
_META_LISTS = (
    "model_text_tools", "verbatim_tools", "external_tools", "declarations_not_in_log",
    "skipped_empty", "dropped_messages", "truncated", "guessed_tool_names",
    "unmatched_tool_results", "unresolved_tool_results", "echoed_back_tool_results",
    "echoes_from_earlier_turns", "echo_warning_details",
    "echo_warnings_cleared_by_declaration", "notes",
)
_LIST_LIKE = (list, tuple, set, frozenset)


def read_meta(raw: Any) -> Dict[str, Any] | None:
    """The `_meta` block of a raw trace, or None where there is none.

    Raises TraceError when the block, or a field a command reads from it, has
    the wrong type. Refused rather than skipped: `_meta` is where a reading
    leaves its echo warnings, and a warnings field that cannot be read, passed
    over in silence, is a gate that opens on malformed input."""
    if not isinstance(raw, dict) or raw.get("_meta") is None:
        return None
    meta = raw["_meta"]
    if not isinstance(meta, dict):
        raise TraceError(f"'_meta' must be an object, got {type(meta).__name__}")
    for key in _META_LISTS:
        value = meta.get(key)
        if value is not None and not isinstance(value, _LIST_LIKE):
            raise TraceError(f"_meta.{key} must be a list, got {type(value).__name__}")
    otel = meta.get("otel")
    if otel is not None:
        if not isinstance(otel, dict):
            raise TraceError(f"_meta.otel must be an object, got {type(otel).__name__}")
        notes = otel.get("notes")
        if notes is not None and not isinstance(notes, _LIST_LIKE):
            raise TraceError(f"_meta.otel.notes must be a list, got {type(notes).__name__}")
    return meta


def load_run_file(path: str | Path) -> Run:
    """Read a JSON trace file and build a Run. Raises TraceError."""
    return load_run(read_json_file(path))
