"""Notes: tool results the reading kept as evidence that may be the model's own text.

The OpenAI reader records, in `_meta.echo_warning_details`, a tool result it kept
as evidence although at least half of it stands in the text of a call the model
wrote - the one it answered, an earlier one, another of the same turn - or a
result it could not match to any call (see `adapters/openai_chat.py`).
`check-trace` and `audit` list those results under the report and in `--json`.

**A note, not an accusation.** It never moves an exit code, raises nothing, and
asks nobody to confirm anything. The reason is its measured worth: review 16
drew 20 such records at random on AgentHallu (four echo tools declared) and read
them by hand - 6 were the model's own text handed back, 7 a tool doing honest
work, 7 disputable. A signal wrong about two times in three has no right to fail
a build, but what it noticed is still worth showing. The labels are kept outside
this repository, so the count is quoted, not recomputable here.

Until round 18 these records held the exit at 1 until a person confirmed each
tool by name (`--accept-echo-warning`, `UnreviewedEchoWarnings`); round 18
removed the records and the gate; round 19 brought the records back as this
note, and not the gate. The `_meta` field names are the old ones, so a trace
written by any of those readers is read the same way.

What a person does with a note: look at the named results. If a tool hands the
model's text back, read the log again with `--tool-returns-model-text NAME`,
which demotes its results - that changes the verdict, a note never does.
"""

from __future__ import annotations

import textwrap
from typing import List

FIELDS = ("result", "tool", "line")

#: Extra fields a record may carry, passed through to `--json` when present.
_EXTRA = ("kind", "message", "call_id")

#: The measured worth of a note, said wherever the notes are listed.
SAMPLE_REAL, SAMPLE_SIZE = 6, 20
WORTH = (f"in a hand-read random sample of {SAMPLE_SIZE} such notes on AgentHallu "
         f"(review 16), {SAMPLE_REAL} were the model's own text; the other "
         f"{SAMPLE_SIZE - SAMPLE_REAL} were a tool doing honest work or disputable")


def echo_warnings(meta) -> List[dict]:
    """The notes the reading recorded, one dict each: `result`, `tool`, `line`,
    `text` for the terminal, and `kind`, `message`, `call_id` where recorded.
    The name is older than the note and stays for code that imports it."""
    if not isinstance(meta, dict):
        return []
    texts = [str(t) for t in meta.get("echoes_from_earlier_turns") or []]
    details = meta.get("echo_warning_details")
    details = details if isinstance(details, list) else []
    warnings: List[dict] = []
    for i in range(max(len(texts), len(details))):
        record = details[i] if i < len(details) and isinstance(details[i], dict) else {}
        result, tool, line = (str(record.get(k) or "") for k in FIELDS)
        text = texts[i] if i < len(texts) else f"{result} ({tool}): {line}"
        note = {"result": result, "tool": tool, "line": line or text, "text": text}
        note.update({k: record[k] for k in _EXTRA if k in record})
        warnings.append(note)
    return warnings


def cleared_echo_warnings(meta) -> List[dict]:
    """Notes the reading worked out and did not list, because the operator
    declared the tool external: `result`, `tool`, `line`, `kind`, `text` and
    `cleared_by`. Said anyway, so a declaration never removes one out of sight."""
    if not isinstance(meta, dict):
        return []
    items = meta.get("echo_warnings_cleared_by_declaration")
    if not isinstance(items, list):
        return []
    return [{k: str(w.get(k) or "") for k in (*FIELDS, "kind", "text", "cleared_by")}
            for w in items if isinstance(w, dict)]


def note_json(meta) -> dict:
    """The `--json` block: every noted result, the ones a declaration cleared,
    and what a note is worth. Not part of `gate`: it decides nothing."""
    notes = echo_warnings(meta)
    cleared = cleared_echo_warnings(meta)
    return {
        "count": len(notes),
        "results": [{k: v for k, v in n.items() if k != "text"} for n in notes],
        "cleared_by_declaration": [{k: v for k, v in c.items() if k != "text"}
                                   for c in cleared],
        "exit_code_effect": "none",
        "worth": WORTH,
    }


def note_lines(meta, *, short: bool) -> List[str]:
    """What the terminal says. `short` is the `--quiet` form, for stderr, so a CI
    log keeps every noted result while stdout stays empty."""
    notes = echo_warnings(meta)
    cleared = cleared_echo_warnings(meta)
    if not notes and not cleared:
        return []
    indent = "  " if short else "    "

    def listed(items, suffix=lambda item: ""):
        lines = [f"{indent}{item['text']}{suffix(item)}" for item in items[:10]]
        if len(items) > 10:
            lines.append(f"{indent}(+{len(items) - 10} more, all of them in --json)")
        return lines

    lines: List[str] = []
    if notes:
        if short:
            lines.append(f"tallystick: note, exit code unchanged: {len(notes)} tool result(s) "
                         f"may be the model's own text - {WORTH}:")
        else:
            lines.append(f"NOTE - {len(notes)} tool result(s) may be the model's own text "
                         f"(the exit code does not change)")
            lines += textwrap.wrap(
                "Each was kept as evidence, but at least half of it stands in text the model "
                "wrote into a call, or the reading matched it to no call or could not weigh "
                f"it to the end. That is a reason to look, not a finding: {WORTH}. If a tool "
                "hands the model's text back, read the log again with "
                "--tool-returns-model-text NAME; that changes the verdict, this note does not.",
                width=76, initial_indent="  ", subsequent_indent="  ")
        lines += listed(notes)
    if cleared:
        lines.append(f"tallystick: note: {len(cleared)} result(s) not listed because a "
                     f"declaration cleared them, not a review:" if short else
                     f"Notes cleared by a declaration, not by review: {len(cleared)} tool "
                     f"result(s).")
        lines += listed(cleared, lambda c: f" - cleared by {c['cleared_by']}")
    return lines
