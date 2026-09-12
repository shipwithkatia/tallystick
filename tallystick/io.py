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


def load_run_file(path: str | Path) -> Run:
    """Read a JSON trace file and build a Run. Raises TraceError or OSError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TraceError(f"{path} is not valid JSON: {exc}") from None
    return load_run(data)
