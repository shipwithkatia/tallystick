"""The echo gate: warnings the reading could not settle, and who reviewed them.

`tallystick check-trace`, `tallystick audit` and `tallystick.audit()` apply the
same gate. The OpenAI reader keeps a tool result as evidence when it cannot tell
whether the tool handed back the model's own text - a line the model wrote in an
earlier call or in another call of the same turn, or a result it could not match
to any call - and records each such result in `_meta.echo_warning_details`.
Until the operator confirms each tool by name, that is not a checked result: the
CLI exits 1 with `unreviewed_echo_warnings`, and `audit()` raises
`UnreviewedEchoWarnings`. It raises rather than returning books_balance False,
because False says "the books do not balance", and the truth is "they were not
checked" - the distinction the CLI's exit codes 1 and 2 exist for.

The tool name is taken from the structured record, never parsed out of the
warning text: a tool named `read_note): x` writes a warning that reads like one
about `read_note`. A warning with no known tool - a trace written before the
records existed, a log that names no tool, a result placed by position between
several tools - is confirmed by no name.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

#: The reason for exit 1, and the word `audit()`'s error carries. Named once:
#: the terminal, `--quiet`, `--json`, the exception and the tests say the same.
UNREVIEWED_ECHO = "unreviewed_echo_warnings"
FIELDS = ("result", "tool", "line")


def echo_warnings(meta) -> List[dict]:
    """The reading's warnings, one dict each: `result`, `tool`, `line`, and
    `text` for the terminal."""
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


def split_echo_warnings(warnings: List[dict],
                        names: Optional[Iterable[str]]) -> Tuple[List[dict], List[dict]]:
    """(unreviewed, accepted). A warning is accepted only when its own tool was
    named; a warning whose tool is unknown never is. One pass, so ten thousand
    warnings cost ten thousand steps, not a hundred million."""
    confirmed = set(names or ())
    unreviewed: List[dict] = []
    accepted: List[dict] = []
    for w in warnings:
        (accepted if w["tool"] and w["tool"] in confirmed else unreviewed).append(w)
    return unreviewed, accepted


class UnreviewedEchoWarnings(Exception):
    """Raised by `tallystick.audit()` on a trace that carries an echo warning
    nobody confirmed - where `tallystick audit` exits 1. Not a verdict: the
    books were not closed. `.warnings` holds every unreviewed warning."""

    def __init__(self, unreviewed: List[dict]):
        self.warnings = list(unreviewed)
        names = sorted({w["tool"] for w in self.warnings if w["tool"]})
        lines = [f"{UNREVIEWED_ECHO}: {len(self.warnings)} tool result(s) in this trace may "
                 f"hand back the model's own text, and nobody has reviewed them, so the "
                 f"books were not closed (`tallystick audit` exits 1 here)."]
        lines += [f"  {w['text']}" for w in self.warnings[:10]]
        if len(self.warnings) > 10:
            lines.append(f"  (+{len(self.warnings) - 10} more, in .warnings)")
        lines.append("  If a tool hands the model's text back, read the log again naming it "
                     "as such (--tool-returns-model-text, model_text_tools).")
        if names:
            lines.append(f"  If its results are real, confirm after review: "
                         f"audit(..., accept_echo_warnings={names!r})")
        if any(not w["tool"] for w in self.warnings):
            lines.append("  A warning with no tool known by name cannot be confirmed by name.")
        super().__init__("\n".join(lines))
