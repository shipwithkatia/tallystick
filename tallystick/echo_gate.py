"""Echo warnings a trace written by an EARLIER reader still carries.

Until round 18 the OpenAI reader warned about tool results that might hand back
the model's own text - a share of the reply found in another call, a result
matched to no call - and `check-trace`, `audit` and `tallystick.audit()` held
the exit at 1 until a person confirmed each tool by name. The warnings were
removed: of 20 drawn at random from AgentHallu and read by hand, 6 were real
echoes, 7 honest work and 7 disputed (review 16), and a signal that is mostly
wrong teaches the person reading it to pass it without looking. What stayed is
the demotion - a result the log shows to be the model's text is not a root -
and it never needed anyone's confirmation.

A reader no longer writes these records. A trace converted or posted before it
may still hold them in `_meta.echo_warning_details` and
`_meta.echoes_from_earlier_turns`, and the commands say so in one note instead
of passing over them in silence: the results they name were kept as evidence
by a reading that is no longer the current one, so the note tells the person
to read the log again. The note does not move the exit code.
"""

from __future__ import annotations

from typing import List

FIELDS = ("result", "tool", "line")


def echo_warnings(meta) -> List[dict]:
    """The warnings an earlier reading recorded, one dict each: `result`, `tool`,
    `line`, and `text` for the terminal. Empty for every trace the current
    reader writes."""
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
        warnings.append({"result": result, "tool": tool, "line": line or text, "text": text})
    return warnings


def legacy_note(meta) -> str:
    """One line about the warnings an earlier reading left in this trace, or ""."""
    warnings = echo_warnings(meta)
    if not warnings:
        return ""
    return (f"this trace carries {len(warnings)} echo warning(s) written by a reader "
            f"before round 18. Echo warnings are no longer raised and do not hold the "
            f"exit code; the results they name were kept as evidence by that reading. "
            f"Read the log again to apply the current one")
