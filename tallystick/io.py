"""Loading a run from JSON.

The on-disk format is deliberately plain: an agent framework, a log exporter or a
human can produce it without importing this library. Adapters for specific
frameworks belong in `adapters/` and should all funnel into `load_run`.

    {
      "artifacts": [{"artifact_id", "kind", "content", "title"?}],
      "steps":     [{"step_id", "kind", "inputs": [...], "outputs": [...]}],
      "claims":    [{"claim_id", "artifact_id", "start", "end", "text"?}],
      "entries":   [{"entry_id", "claim_id", "account", "quoted_span"?, "proposed_by"?}],
      "assumptions": {"<id>": "<premise text>"}          (optional)
    }

Malformed input raises TraceError with a message that names the offending
object; it never surfaces as a bare KeyError or int() failure.

If `text` is omitted from a claim it is read from the artifact span, which is the
better habit: it keeps the claim and the artifact from drifting apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .types import Account, Artifact, ArtifactKind, Claim, Entry, Run, Step, TraceError


def load_run(data: Dict[str, Any]) -> Run:
    run = Run()

    for raw in data.get("artifacts", []):
        art = Artifact(
            artifact_id=raw["artifact_id"],
            kind=ArtifactKind(raw["kind"]),
            content=raw["content"],
            title=raw.get("title", ""),
        )
        run.artifacts[art.artifact_id] = art

    for raw in data.get("steps", []):
        run.steps.append(Step(
            step_id=raw["step_id"],
            kind=raw.get("kind", "generate"),
            inputs=tuple(raw.get("inputs", [])),
            outputs=tuple(raw.get("outputs", [])),
        ))

    for step in run.steps:
        for aid in (*step.inputs, *step.outputs):
            if aid not in run.artifacts:
                raise TraceError(f"step {step.step_id} references unknown artifact {aid!r}")

    for raw in data.get("claims", []):
        cid = raw["claim_id"]
        if raw["artifact_id"] not in run.artifacts:
            raise TraceError(f"claim {cid} is in unknown artifact {raw['artifact_id']!r}")
        art = run.artifacts[raw["artifact_id"]]
        start, end = int(raw["start"]), int(raw["end"])
        if not (0 <= start < end <= len(art.content)):
            raise TraceError(
                f"claim {cid} span {start}-{end} is outside artifact "
                f"{art.artifact_id!r} (length {len(art.content)})"
            )
        if cid in run.claims:
            raise TraceError(f"duplicate claim id {cid!r}")
        run.claims[cid] = Claim(
            claim_id=raw["claim_id"],
            artifact_id=raw["artifact_id"],
            start=start,
            end=end,
            text=raw.get("text") or art.slice(start, end),
        )

    for aid, text in data.get("assumptions", {}).items():
        run.assumptions[aid] = text

    for raw in data.get("entries", []):
        if raw["claim_id"] not in run.claims:
            raise TraceError(
                f"entry {raw['entry_id']} refers to unknown claim {raw['claim_id']!r}"
            )
        run.entries.append(Entry(
            entry_id=raw["entry_id"],
            claim_id=raw["claim_id"],
            account=Account.parse(raw["account"]),
            quoted_span=raw.get("quoted_span", ""),
            proposed_by=raw.get("proposed_by", "manual"),
        ))

    return run


def load_run_file(path: str | Path) -> Run:
    return load_run(json.loads(Path(path).read_text(encoding="utf-8")))
