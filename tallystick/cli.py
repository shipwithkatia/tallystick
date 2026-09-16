"""Command line entry point.

    tallystick audit       run.json      close the books on a posted trace
    tallystick check-trace raw.json      can this trace be audited at all?
    tallystick convert     log.json -o raw.json
                                         read someone else's log as a trace
    tallystick propose     raw.json -o run.json
                                         let a model post claims and credits,
                                         then audit the file it wrote

`audit` is the default: `tallystick run.json` works.

`check-trace`, `convert` and `propose` take `--from openai|otel` and will read
a log that was never written for this tool. `audit` does not: it needs a posted
trace, and a converted log has no claims on it yet. That is the boundary -
converting is reading, posting is a separate act, and neither is a verdict.

The exit code is the product decision here: 0 when every claim in the final answer
traces back to a root and no echo warning carried over from the reading is left
unconfirmed, and exactly one artifact is marked as the answer; 1 otherwise; 2 when
the audit could not run at all (a malformed or unreadable file, a missing SDK or
key, a proposer failure) or could not finish (a claim's chain deeper than the
ledger walks, and nothing else wrong). A bad API key must never
read as "books do not balance". That is what turns this from a report into a gate
you can put in CI and fail a build on.

`check-trace` comes before either: it reads a raw trace and reports how much of
the run a provenance audit can look at, and what would have to be recorded for
the rest. No model, no claims, no cost - and a `partial` verdict there is why a
later clean audit may mean less than it looks. It also exits 1 while the reading
reports a result that may hand back the model's own text - a line from another
call, or a result matched to no call - that nobody has reviewed
(`unreviewed_echo_warnings`, cleared tool by tool with `--accept-echo-warning NAME`).
`tallystick.audit()` raises `UnreviewedEchoWarnings` in the same place.

`propose` is the only place the verdict path touches the model side, and it does so
lazily, inside the subcommand, so `tallystick audit` never imports an SDK.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .auditability import (DEFAULT_MIN_REACHABLE, MULTIPLE_FINAL_ANSWERS, check_trace,
                           multiple_final_answers, report)
from .convert import (FORMATS, describe, detect, hint_for, looks_like_trace, read_any,
                      trace_size, trace_size_line)
from .echo_gate import FIELDS as _ECHO_FIELDS, UNREVIEWED_ECHO
from .echo_gate import cleared_echo_warnings as _cleared_echo_warnings
from .echo_gate import echo_warnings as _echo_warnings
from .echo_gate import split_echo_warnings as _split_echo_warnings
from .adapters.openai_chat import DEFAULT_MAX_TOOL_CHARS
from .io import load_run, read_json_file, read_meta
from .ledger import TOO_DEEP, close_books
from .report import chain_view, summary
from .types import TraceError

SUBCOMMANDS = ("audit", "check-trace", "convert", "propose")

#: The reason for exit 2 from `audit` when the only claims that did not close
#: are ones whose chain is deeper than the ledger walks. Could not check.
CHAIN_TOO_DEEP = "chain_too_deep"

#: The flag that confirms one tool's echo warnings. The reason it clears,
#: UNREVIEWED_ECHO, lives in echo_gate with the rest of the gate, because
#: `tallystick.audit()` applies the same gate from Python.
ACCEPT_ECHO_FLAG = "--accept-echo-warning"


def _add_echo_gate_args(parser: argparse.ArgumentParser) -> None:
    """The flag that confirms echo warnings, one tool at a time. There is no
    flag that confirms them all: accepted once in CI, it would pass next
    month's new warning about a different tool without a word."""
    parser.add_argument(
        ACCEPT_ECHO_FLAG, dest="accept_echo_warnings", action="append", default=[],
        metavar="NAME",
        help="confirm, for one tool, that you reviewed the results the reading "
             "kept as evidence although they may hand back the model's own text "
             "(a line it wrote in another call, or a result matched to no call). "
             "Repeatable, one tool each time. A warning about any "
             f"tool not named keeps the exit at 1 ({UNREVIEWED_ECHO}); a "
             "confirmation clears that reason and no other")


#: The reason for exit 1 in strict mode: a tool in the log was declared neither
#: as returning the model's text, nor text verbatim, nor external evidence.
UNDECLARED_TOOLS = "undeclared_tools"
_DECLARATIONS = ("model_text_tools", "verbatim_tools", "external_tools")


def _add_strict_args(parser: argparse.ArgumentParser) -> None:
    """Strict mode, off by default. A tool result is a root by design - the
    audit stops there on purpose - so a tool nobody declared is not a suspicious
    place, and blocking on it would block every real trace: on AgentHallu 95.5%
    of trajectories, with the corpus's four echo tools declared. It is for an
    operator whose tool set is fixed and who wants every tool in it named."""
    parser.add_argument(
        "--require-declared-tools", dest="require_declared_tools", action="store_true",
        help=f"strict mode, for a fixed tool set: exit 1 ({UNDECLARED_TOOLS}) while "
             "any tool in the log is declared neither with --tool-returns-model-text, "
             "--tool-returns-verbatim nor --tool-returns-external. Off by default: "
             "a tool result is a root by design, and the report counts undeclared "
             "tools either way")


def _undeclared_tool_results(raw):
    """How many tool results came from tools the operator declared nothing
    about, and their names: `(results, sorted names)`. None when the trace
    carries no reading of a log - a native trace has no declarations to count
    against. Counted over the reading's tool artifacts (ids `t<k>`), by the name
    each carries, whatever the reading made of it."""
    if not isinstance(raw, dict):
        return None
    meta = raw.get("_meta")
    if not isinstance(meta, dict) or "model_text_tools" not in meta:
        return None
    declared = {str(t) for key in _DECLARATIONS for t in meta.get(key) or []}
    names = [str(a.get("title") or "") for a in raw.get("artifacts") or []
             if isinstance(a, dict) and str(a.get("artifact_id", "")).startswith("t")]
    undeclared = [name for name in names if name not in declared]
    return len(undeclared), sorted(set(undeclared))


def _strict_blocks(args: argparse.Namespace, undeclared) -> bool:
    return bool(getattr(args, "require_declared_tools", False)
                and undeclared and undeclared[0])


def _say_strict(args: argparse.Namespace, undeclared, blocked: bool) -> None:
    """Why strict mode failed - under the report, or on stderr with --quiet -
    and, where the trace has no reading, that the flag had nothing to check."""
    if not getattr(args, "require_declared_tools", False):
        return
    if undeclared is None:
        print("tallystick: --require-declared-tools: this trace carries no reading "
              "of a log, so it has no tool declarations to check; nothing blocked",
              file=sys.stderr)
        return
    if not blocked:
        return
    results, names = undeclared
    shown = ", ".join(names)
    if args.quiet:
        print(f"tallystick: exit 1 - {UNDECLARED_TOOLS}: {results} tool result(s) from "
              f"{len(names)} tool name(s) nobody declared: {shown}; declare each with "
              f"--tool-returns-model-text, --tool-returns-verbatim or "
              f"--tool-returns-external", file=sys.stderr)
        return
    print()
    for line in (f"UNDECLARED TOOLS - exit 1 ({UNDECLARED_TOOLS}, --require-declared-tools)",
                 f"  {results} tool result(s) from {len(names)} tool name(s) nobody "
                 f"declared: {shown}",
                 "  Declare each: --tool-returns-model-text NAME if it hands the model's",
                 "  text back, --tool-returns-verbatim NAME if it returns text as fetched,",
                 "  --tool-returns-external NAME if its result is external evidence."):
        print(line)


def _gate_block(code: int, reasons: list[str], unreviewed: list[dict],
                accepted: list[dict], cleared: list[dict] = ()) -> dict:
    """The exit code and why, for --json. `verdict` alone would read "auditable"
    on a run that exits 1 for an unreviewed echo."""
    def plain(ws):
        return [{k: w[k] for k in _ECHO_FIELDS} for w in ws]
    warnings = {"accepted": plain(accepted), "unreviewed": plain(unreviewed)}
    if cleared:
        # Only when a declaration cleared something, so a file with nothing
        # cleared is the file a CI job already parses.
        warnings["cleared_by_declaration"] = [
            {**{k: w[k] for k in _ECHO_FIELDS}, "kind": w["kind"], "cleared_by": w["cleared_by"]}
            for w in cleared]
    return {"exit_code": code, "reasons": reasons, "echo_warnings": warnings}


def _echo_gate_lines(unreviewed: list[dict], accepted: list[dict], *,
                     short: bool, cleared: list[dict] = ()) -> list[str]:
    """What is said about echo warnings. `short` is the `--quiet` form, for
    stderr: a CI job that never shows the terminal must still leave every
    warning in its log."""
    import shlex

    def listed(ws, indent):
        lines = [f"{indent}{w['text']}" for w in ws[:10]]
        if len(ws) > 10:
            lines.append(f"{indent}(+{len(ws) - 10} more, all of them in --json)")
        return lines

    lines: list[str] = []
    if unreviewed:
        n = len(unreviewed)
        if short:
            lines.append(f"tallystick: exit 1 - {UNREVIEWED_ECHO}: {n} tool result(s) may "
                         f"hand back the model's own text; review them, "
                         f"then pass --tool-returns-model-text NAME or "
                         f"{ACCEPT_ECHO_FLAG} NAME")
        else:
            lines += [f"UNREVIEWED ECHO WARNINGS - exit 1 ({UNREVIEWED_ECHO})",
                      f"  {n} tool result(s) may hand back text the model wrote: a line",
                      "  from another call, earlier or in the same turn, or a result the",
                      "  reading matched to no call. They were kept as evidence, so the",
                      "  verdict above counts them as evidence: a clean result here is not",
                      "  yet a checked one. Each warning names the message in your file",
                      "  and the call id. For each tool: if it hands the model's own text",
                      "  back, pass --tool-returns-model-text NAME; if its result is a",
                      "  real confirmation, confirm that tool by name."]
        lines += listed(unreviewed, "  " if short else "    ")
        names = sorted({w["tool"] for w in unreviewed if w["tool"]})
        if names:
            lines.append("  to confirm: " + " ".join(
                f"{ACCEPT_ECHO_FLAG} {shlex.quote(name)}" for name in names))
        if any(not w["tool"] for w in unreviewed):
            lines.append("  a warning with no tool known by name - the log names none, the "
                         "result was placed by position between several tools, or the trace "
                         "predates these records - cannot be confirmed by name; declare the "
                         "tool with --tool-returns-model-text, or record names and call ids")
    if accepted:
        lines.append(f"tallystick: {len(accepted)} echo warning(s) accepted by name with "
                     f"{ACCEPT_ECHO_FLAG}, kept as evidence:" if short else
                     f"Echo warnings accepted by name with {ACCEPT_ECHO_FLAG}: "
                     f"{len(accepted)} tool result(s) kept as evidence.")
        lines += listed(accepted, "  " if short else "    ")
    if cleared:
        # Not a block: the operator declared these tools external and took on
        # what the reading cannot know. But a warning removed without a word is
        # a silent pass, so each one is named with the declaration that did it.
        lines.append(f"tallystick: {len(cleared)} echo warning(s) cleared by a declaration, "
                     f"not by review:" if short else
                     f"Echo warnings cleared by a declaration, not by review: "
                     f"{len(cleared)} tool result(s) kept as evidence with no warning.")
        indent = "  " if short else "    "
        lines += [f"{indent}{w['text']} - cleared by {w['cleared_by']}" for w in cleared[:10]]
        if len(cleared) > 10:
            lines.append(f"{indent}(+{len(cleared) - 10} more, all of them in --json)")
    return lines


def _say_echo_gate(unreviewed: list[dict], accepted: list[dict], *, quiet: bool,
                   cleared: list[dict] = ()) -> None:
    """Under the report - or, with --quiet, on stderr, which keeps stdout empty
    for scripts while every CI log still records the warning."""
    lines = _echo_gate_lines(unreviewed, accepted, short=quiet, cleared=cleared)
    if not lines:
        return
    if quiet:
        for line in lines:
            print(line, file=sys.stderr)
    else:
        print()
        for line in lines:
            print(line)


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    """The flags that let a command read a log this project did not write."""
    parser.add_argument(
        "--from", dest="source", default="auto", choices=FORMATS,
        help="what the input file is. 'auto' (the default) reads a tallystick "
             "trace as one and converts an unmistakable OpenAI chat log or "
             "OpenTelemetry GenAI span export; where two readers could both "
             "claim the file it refuses and asks, because reading it the wrong "
             "way would produce a confident audit of a run that did not happen")
    parser.add_argument(
        "--tool-returns-model-text", dest="model_text_tools", action="append",
        default=[], metavar="NAME",
        help="a tool that hands the model's own words back (a final_answer "
             "tool, a note store, a scratchpad): recorded as the model's text, "
             "not as evidence. Repeatable")
    parser.add_argument(
        "--tool-returns-verbatim", dest="verbatim_tools", action="append",
        default=[], metavar="NAME",
        help="a tool that returns external text exactly as fetched (a file "
             "reader, a retriever handing back the passage): recorded as a "
             "document. Repeatable. A search API that answers with its own "
             "summary is not one of these")
    parser.add_argument(
        "--tool-returns-external", dest="external_tools", action="append",
        default=[], metavar="NAME",
        help="a tool whose result is external evidence in its own words (a "
             "search API's summary, an API response): counted as declared, and "
             "its echo warnings are cleared - you vouch for it. A result found in "
             "the arguments of the very call it answers is still read as the "
             "model's own text. Repeatable")
    parser.add_argument(
        "--max-tool-chars", type=int, default=DEFAULT_MAX_TOOL_CHARS,
        metavar="N",
        help=f"cut a tool result longer than this and record the cut "
             f"(default {DEFAULT_MAX_TOOL_CHARS})")


def _hint(args: argparse.Namespace, seen: dict) -> str:
    """When a file claimed to be a trace and would not load, say what would
    read it. `load_run` is right to report the malformed field; it just has no
    idea the file was never a trace to begin with.

    Uses the copy already parsed, never a second read of the path: re-reading
    doubled the cost of every failure and hung outright on a process
    substitution, which has no second reader. Exit 2 means "could not run" -
    it must not mean "stopped running"."""
    if getattr(args, "source", "auto") not in ("auto", "tallystick"):
        return ""
    raw = seen.get("raw")
    return hint_for(raw) if raw is not None else ""


def _reading_notes(meta: dict, undeclared=None) -> list[str]:
    """Everything the reading itself left out or had to decide, wrapped for a
    terminal. A reader that lost a message and said nothing turns its own bug
    into a finding against the user's recorder, which is the failure this whole
    project is about."""
    import textwrap

    lines: list[str] = []
    if meta.get("reader_confidence"):
        lines.append(f"reader: {meta['reader_confidence']}")
    for key, label in (("model_text_tools", "read as the model's own text"),
                       ("verbatim_tools", "read as external text, verbatim"),
                       ("external_tools", "declared as external evidence")):
        named = meta.get(key) or []
        if named:
            lines.append(f"tools {label}: {', '.join(map(str, named))}")
    unknown = meta.get("declarations_not_in_log") or []
    if unknown:
        # Said out loud, because the alternative is a declaration that does
        # nothing in silence: the names are compared case folded and trimmed,
        # so a name that still matches nothing is a name that is not in the log.
        lines.append(f"declared but not in this log: {', '.join(map(str, unknown))} "
                     f"- no tool of that name was called, so the declaration "
                     f"changed nothing")
    if undeclared and undeclared[0]:
        # One line and no verdict: a tool result is a root by design, so a tool
        # nobody declared is not a suspicious place. Counted so it is visible;
        # --require-declared-tools is the mode that blocks on it.
        results, names = undeclared
        shown = ", ".join(names[:6]) + (f" (+{len(names) - 6} more)" if len(names) > 6 else "")
        lines.append(f"undeclared tools: {results} result(s) from {len(names)} tool "
                     f"name(s) nobody declared ({shown}) - counted, not blocked")
    for key, label in (("skipped_empty", "message(s) held no text"),
                       ("dropped_messages", "not placed by this reader"),
                       ("truncated", "cut to --max-tool-chars"),
                       ("guessed_tool_names", "tool name(s) matched by position"),
                       ("unmatched_tool_results",
                        "result(s) whose call id matched nothing open"),
                       ("unresolved_tool_results",
                        "result(s) read as the model's own text, unresolved"),
                       # The one decision the reader makes without being told
                       # anything, and the one that can override an explicit
                       # --tool-returns-verbatim. It was not reported at all
                       # until review asked why.
                       ("echoed_back_tool_results",
                        "result(s) that quoted their own call back, so read as "
                        "the model's text"),
                       # Not a demotion: the log cannot tell a value handed back
                       # from an earlier turn from one a tool confirmed. Said
                       # here so the operator can decide with
                       # --tool-returns-model-text.
                       ("echoes_from_earlier_turns",
                        "result(s) kept as evidence that may hand back the "
                        "model's own text")):
        items = meta.get(key) or []
        if items:
            shown = ", ".join(str(i) for i in items[:6])
            more = f" (+{len(items) - 6} more)" if len(items) > 6 else ""
            lines.append(f"left out - {label}: {shown}{more}")
    notes = list(meta.get("notes") or [])
    notes += list((meta.get("otel") or {}).get("notes") or [])
    for note in notes:
        wrapped = textwrap.wrap(f"note: {note}", width=74,
                                subsequent_indent="      ")
        lines.extend(wrapped)
    return lines


def _read_raw(args: argparse.Namespace, seen: dict | None = None):
    """Read the input file in whatever shape it is. Returns (raw, source).

    `seen` keeps the parsed input, so a later error message can look at what
    the file actually was without reading the path a second time."""
    source = getattr(args, "source", "auto")
    raw = read_json_file(args.trace)
    if seen is not None:
        seen["raw"] = raw
    return read_any(
        raw, source=source, name=str(args.trace),
        max_tool_chars=getattr(args, "max_tool_chars", DEFAULT_MAX_TOOL_CHARS),
        model_text_tools=getattr(args, "model_text_tools", None) or None,
        verbatim_tools=getattr(args, "verbatim_tools", None) or None,
        external_tools=getattr(args, "external_tools", None) or None,
    )


class CannotWrite(Exception):
    """An output file could not be written. Exit 2, never 1."""


def _write_json(path: str, payload) -> None:
    """Write a JSON output file, or raise CannotWrite and leave no file behind.

    The whole file is encoded before the path is opened. `json.dump` into an
    open file wrote chunk by chunk, so text UTF-8 cannot hold - a lone surrogate
    such as \\ud83d, what a UTF-16 string cut inside an emoji serialises to -
    failed halfway and left a cut-off file that the next command would read as
    a trace (review 13, 2.3). A write that fails after opening removes what it
    started."""
    try:
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CannotWrite(
            f"its text holds a character UTF-8 cannot store ({exc.reason}: "
            f"{exc.object[exc.start:exc.end]!r}) - usually half of an emoji cut "
            f"off in the log; nothing was written") from None
    except RecursionError:
        raise CannotWrite("it is nested too deeply to write; nothing was written") from None
    try:
        fh = open(path, "wb")
    except OSError as exc:
        raise CannotWrite(str(exc)) from None    # nothing opened, nothing to remove
    try:
        with fh:
            fh.write(data)
    except OSError as exc:
        try:
            Path(path).unlink()                  # only a file this call opened
        except OSError:
            pass
        raise CannotWrite(str(exc)) from None


_TRACE_SHAPE = ("`audit` needs a posted tallystick trace: a JSON object with 'artifacts'\n"
                "  and 'steps', and the claims posted on them. docs/auditable-traces.md\n"
                "  describes the format.")


def _cannot_audit(path, raw, exc: Exception) -> str:
    """Why `audit` refused its input, in words the person holding the wrong file
    can act on.

    The likeliest wrong file is the one most people have: a chat log. "trace
    must be an object, got list" is true and ends their attempt there. So a
    file another reader would take is named for what it looks like, with the
    shape `audit` wants and the two commands that get there. A file that claims
    to be a trace keeps the loader's precise error - "artifacts[0] is missing
    'artifact_id'" is the help for that one."""
    if raw is None or looks_like_trace(raw):
        return f"cannot audit this trace: {exc}"
    found = detect(raw)
    if not found:
        return f"cannot audit this file: {exc}. It is not a tallystick trace.\n  {_TRACE_SHAPE}"
    what = "an OpenAI chat log" if found == ["openai"] else describe(found)
    quoted = shlex.quote(str(path))
    return (f"cannot audit this file: it looks like {what}, not a tallystick trace.\n"
            f"  {_TRACE_SHAPE}\n"
            f"  A log like this is read first, with no model and no cost:\n"
            f"    tallystick check-trace {quoted}\n"
            f"  and the claims are posted on it with:\n"
            f"    tallystick propose {quoted} -o posted.json")


def _audit(args: argparse.Namespace) -> int:
    raw = None
    try:
        # What `load_run_file` does, in two steps, so the reading's `_meta` that
        # `propose` carried into the posted trace is at hand for the echo gate.
        raw = read_json_file(args.trace)
        run = load_run(raw)
        read_meta(raw)
    except (OSError, ValueError) as exc:
        # TraceError is a ValueError; so is json.JSONDecodeError. Either way the
        # input is unusable, and that is a different failure from "books don't
        # balance" - hence exit code 2, not 1.
        print(f"tallystick: {_cannot_audit(args.trace, raw, exc)}", file=sys.stderr)
        return 2
    balance = close_books(run)

    if not balance.final_claim_ids:
        # Nothing was audited. That must not leave as exit 1: "the books do not
        # balance" is a verdict about the run, and a beginner meets this by
        # auditing a raw trace before posting anything to it - the likeliest
        # first mistake there is. Reporting their unposted file as a failed
        # audit is the exact confusion the exit codes exist to prevent.
        if not run.claims:
            print("tallystick: this trace has no claims posted on it yet, so "
                  "there is nothing to audit.\n"
                  "  A raw trace records what the run produced; the claims say "
                  "which sentences of the answer\n"
                  "  are being checked and what each one rests on. Post them "
                  "first:\n"
                  f"    tallystick propose {args.trace} -o posted.json\n"
                  "  Or, to ask whether this trace can be audited at all, with "
                  "no model and no cost:\n"
                  f"    tallystick check-trace {args.trace}", file=sys.stderr)
        else:
            print("tallystick: this trace has claims, but none of them is in a "
                  "final answer, so there is\n"
                  "  nothing to audit. An audit works backwards from what the "
                  "user saw; without a claim\n"
                  "  there, it has no question to ask.", file=sys.stderr)
        return 2

    # The echo gate `check-trace` applies, on the posted copy. Books that
    # balance on a note the model wrote itself are not a checked result until
    # someone has looked, and a gate the second command walks around is not a
    # gate. Exit 2 above stays 2: this decides between 0 and 1 only.
    unreviewed, accepted = _split_echo_warnings(
        _echo_warnings(raw.get("_meta") if isinstance(raw, dict) else None),
        getattr(args, "accept_echo_warnings", None))
    cleared = _cleared_echo_warnings(raw.get("_meta") if isinstance(raw, dict) else None)
    undeclared = _undeclared_tool_results(raw)
    strict = _strict_blocks(args, undeclared)
    reasons: list[str] = []
    if balance.injection_points():
        reasons.append("books_do_not_balance")
    elif not balance.books_balance:
        # Every claim that did not close is one whose chain is too deep to walk.
        # Nothing was found against the run, so this is not exit 1: the audit
        # could not finish, which is exit 2 - whatever else is said below.
        reasons.append(CHAIN_TOO_DEEP)
    # The measure `check-trace` applies, from the same function. With two
    # answers the trace does not say which one the user saw, and claims posted
    # on one of them do not say it either: a second answer nobody posted a
    # claim on would otherwise leave as exit 0, checked by nothing.
    finals = multiple_final_answers(run)
    if finals:
        reasons.append(MULTIPLE_FINAL_ANSWERS)
    if unreviewed:
        reasons.append(UNREVIEWED_ECHO)
    if strict:
        reasons.append(UNDECLARED_TOOLS)
    code = 2 if CHAIN_TOO_DEEP in reasons else 1 if reasons else 0

    if args.chain:
        if args.chain not in balance.audits:
            known = ", ".join(sorted(balance.audits)) or "(none)"
            print(f"tallystick: no claim {args.chain!r}; claims are: {known}",
                  file=sys.stderr)
            return 2
        print(chain_view(run, balance, args.chain))
    elif not args.quiet:
        print(summary(run, balance))
    if finals:
        said = (f"tallystick: exit {code} - {MULTIPLE_FINAL_ANSWERS}: {len(finals)} artifacts "
                f"are marked final_answer ({', '.join(finals)}); the trace does not say "
                f"which one the user saw, so a clean result on one is not a checked run. "
                f"Mark one answer, or record the others as intermediate")
        print(said if args.quiet else f"\n{said[len('tallystick: '):]}",
              file=sys.stderr if args.quiet else sys.stdout)
    if CHAIN_TOO_DEEP in reasons and (args.quiet or args.chain):
        print(f"tallystick: exit 2 - {CHAIN_TOO_DEEP}: {len(balance.unchecked())} claim(s) "
              f"in the final answer rest on a {TOO_DEEP}; they were not checked, and "
              f"nothing was found against them", file=sys.stderr)
    _say_echo_gate(unreviewed, accepted, quiet=args.quiet, cleared=cleared)
    _say_strict(args, undeclared, strict)

    if args.json_out:
        payload = {
            "coverage": balance.coverage,
            "laundering_rate": balance.laundering_rate,
            "books_balance": balance.books_balance,
            "counts": balance.counts(balance.final_claim_ids),
            "claims": [
                {
                    "claim_id": a.claim_id,
                    "status": a.status.value,
                    "depth": a.depth,
                    "text": a.text,
                    "break_step_id": a.break_step_id,
                    "break_reason": a.break_reason,
                    "chain": [h.account for h in a.chain],
                }
                for a in balance.audits.values()
            ],
        }
        payload["gate"] = _gate_block(code, reasons, unreviewed, accepted, cleared)
        try:
            _write_json(args.json_out, payload)
        except CannotWrite as exc:
            print(f"tallystick: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2

    return code


def _check_trace(args: argparse.Namespace) -> int:
    """Exit 0 when the trace is auditable, 1 when it is only partly so or not at
    all, 2 when it cannot be read. Same shape as `audit`: 1 is a verdict about
    the trace, 2 is a failure to run - so a CI job can fail a build on a
    recording that has stopped keeping what an audit needs."""
    # Checked before the file is touched: a bad flag is not worth reading a
    # 700 MB trace to discover. `nan` fails every comparison, so it would be a
    # gate the caller asked for that silently never fires.
    if args.min_reachable is not None and not 0.0 <= args.min_reachable <= 1.0:
        print(f"tallystick: --min-reachable must be a share between 0 and 1, "
              f"got {args.min_reachable}", file=sys.stderr)
        return 2
    seen: dict = {}
    try:
        raw, source = _read_raw(args, seen)  # one read; `_meta` comes from `raw`
        run = load_run(raw)
        read_meta(raw)
    except (OSError, ValueError) as exc:
        print(f"tallystick: cannot read this trace: {exc}.{_hint(args, seen)}",
              file=sys.stderr)
        return 2
    meta = raw.get("_meta") if isinstance(raw, dict) else None
    result = check_trace(run, min_reachable=args.min_reachable,
                   meta=meta if isinstance(meta, dict) else None)
    undeclared = _undeclared_tool_results(raw)
    notes = _reading_notes(meta if isinstance(meta, dict) else {}, undeclared)
    # A converted chat can come out many times the size of the log. Said, not
    # blocked: it is how the format records a whole history sent every turn.
    size = _trace_size(source, seen.get("raw"), raw, None, _file_bytes(args.trace))
    size_lines = _wrapped(trace_size_line(size)) if size else []
    # A tool result ending in a line the model wrote in an earlier call is kept
    # as evidence, because the log cannot tell a value handed back from a value
    # confirmed - so the verdict counts it as evidence. Exiting 0 on that, with
    # nobody having looked, certifies a place nobody checked. It exits 1 until
    # the operator confirms each tool by name; a confirmation clears that
    # reason only, and only for the tool it names.
    unreviewed, accepted = _split_echo_warnings(
        _echo_warnings(meta), getattr(args, "accept_echo_warnings", None))
    cleared = _cleared_echo_warnings(meta)
    reasons: list[str] = []
    if result.verdict != "auditable":
        reasons.append(f"verdict:{result.verdict}")
    if unreviewed:
        reasons.append(UNREVIEWED_ECHO)
    strict = _strict_blocks(args, undeclared)
    if strict:
        reasons.append(UNDECLARED_TOOLS)
    code = 1 if reasons else 0
    if not args.quiet and (source != "tallystick" or notes):
        # Said before the report, because every number below is a number about
        # the reading as much as about the run - and because a reading that
        # lost something must not reach the user as a defect in their recorder.
        # This holds for a file converted earlier too: `convert` writes its
        # `_meta` into the trace precisely so the next command can say it again.
        read_by = meta.get("source") if isinstance(meta, dict) else None
        by = source if source != "tallystick" else (read_by or "tallystick")
        print(f"Read as {by}: {len(run.artifacts)} artifact(s) from "
              f"{args.trace}. The report below judges that reading.")
        for line in notes + size_lines:
            print(f"  {line}")
        print()
    if args.json_out:
        payload = result.as_dict()
        # A CI job runs --quiet --json and never sees the terminal. Without
        # this it records a verdict with no trace of what the reading dropped,
        # guessed, or could not vouch for - which is the verdict meaning less
        # than it says, in the one place nobody is watching.
        if isinstance(meta, dict):
            reading = {k: meta[k] for k in
                       ("source", "reader_confidence", "skipped_empty",
                        "dropped_messages", "truncated", "guessed_tool_names",
                        "unmatched_tool_results", "unresolved_tool_results",
                        "echoed_back_tool_results", "echoes_from_earlier_turns",
                        "echo_warning_details", "echo_warnings_cleared_by_declaration",
                        "model_text_tools", "verbatim_tools", "external_tools",
                        "notes", "otel") if k in meta}
            if undeclared is not None:
                reading["undeclared_tool_results"] = {
                    "results": undeclared[0], "tools": undeclared[1]}
            if size:
                reading["trace_size"] = size
            if reading:
                payload["reading"] = reading
        payload["gate"] = _gate_block(code, reasons, unreviewed, accepted, cleared)
        try:
            _write_json(args.json_out, payload)
        except CannotWrite as exc:
            print(f"tallystick: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2
    if not args.quiet:
        print(report(result))
    _say_echo_gate(unreviewed, accepted, quiet=args.quiet, cleared=cleared)
    _say_strict(args, undeclared, strict)
    if args.quiet and size:
        # Not a verdict and not in the exit code, but a CI job that runs
        # --quiet must still find in its log why the step took so long.
        print(f"tallystick: {trace_size_line(size)}", file=sys.stderr)
    return code


def _file_bytes(path) -> int | None:
    """The size of the input on disk, or None where it was not a plain file - a
    pipe, a process substitution, a device. A size nobody can check with `ls` is
    worse than no size at all, so it is not guessed.

    The guard is not decoration: `<(gunzip -c log.json.gz)` hands this a path
    like /dev/fd/63, whose `st_size` is 65536 here - the pipe's buffer, which
    has nothing to do with the log. A FIFO reports 0, which `trace_size` then
    treats as no size at all."""
    try:
        p = Path(path)
        return p.stat().st_size if p.is_file() else None
    except OSError:
        return None


def _trace_size(source: str, log, trace, trace_bytes=None, log_bytes=None):
    """`trace_size` for what was read. A file read as a trace needs no guard:
    the "log" and the trace are then the same object, the ratio is 1, and
    nothing is said - a mutation test showed a separate check here changed
    nothing, so there is none.

    `log_bytes` is the log's size on disk, taken by the caller BEFORE anything
    is written: `convert log.json -o log.json` replaces the log with the trace,
    and a size taken afterwards by the same path named a log of 2.9 MB that was
    119 KB (review 13, 6.1)."""
    if log is None or not isinstance(trace, dict):
        return None
    return trace_size(log, trace, trace_bytes, log_bytes)


def _wrapped(text: str) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=74, subsequent_indent="      ")


def _convert(args: argparse.Namespace) -> int:
    """Read someone else's log and write a tallystick trace. Exit 0 when the
    file was written, 2 when it could not be read or written. There is no
    verdict here and so no exit 1: converting is reading, not judging."""
    seen: dict = {}
    try:
        raw, source = _read_raw(args, seen)
        run = load_run(raw)            # a converter that writes an unloadable
        read_meta(raw)                 # file is worse than one that refuses
    except (OSError, ValueError) as exc:
        print(f"tallystick: cannot read this log: {exc}.{_hint(args, seen)}",
              file=sys.stderr)
        return 2

    # Before the write: the output may be the log itself.
    log_bytes = _file_bytes(args.trace)
    try:
        _write_json(args.out, raw)
    except CannotWrite as exc:
        print(f"tallystick: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2

    meta = raw.get("_meta") if isinstance(raw, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    kinds = {}
    for art in run.artifacts.values():
        kinds[art.kind.value] = kinds.get(art.kind.value, 0) + 1
    print(f"read as {source} -> {args.out}")
    print(f"  {len(run.artifacts)} artifact(s): " +
          ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) +
          f"; {len(run.steps)} step(s)")

    # Everything the reading left out, before anyone draws a conclusion from
    # what it kept.
    for line in _reading_notes(meta, _undeclared_tool_results(raw)):
        print(f"  {line}")
    # The size of the file just written, so a trace of a hundred megabytes is
    # not serialised a second time to be measured. `os` is kept out of the
    # verdict path by tests/test_no_model_imports.py; pathlib is already here.
    size = _trace_size(source, seen.get("raw"), raw, Path(args.out).stat().st_size,
                       log_bytes)
    for line in _wrapped(trace_size_line(size)) if size else []:
        print(f"  {line}")
    if not args.quiet:
        print(f"\nNext: tallystick check-trace {args.out}")
    return 0


def _propose(args: argparse.Namespace) -> int:
    # The model side is imported here and nowhere else in the verdict path, so
    # `tallystick audit` never loads an SDK.
    from .propose import FakeProposer, post_run

    try:
        raw, _source = _read_raw(args)
        run = load_run(raw)
        read_meta(raw)
    except (OSError, ValueError) as exc:
        print(f"tallystick: cannot read this trace: {exc}", file=sys.stderr)
        return 2

    try:
        if args.proposer == "fake":
            if not args.script:
                print("tallystick: --proposer fake needs --script answers.json",
                      file=sys.stderr)
                return 2
            with open(args.script, encoding="utf-8") as fh:
                proposer = FakeProposer(json.load(fh))
        else:
            from .propose import AnthropicProposer
            proposer = AnthropicProposer(model=args.model)
        posted = post_run(run, proposer)
    except Exception as exc:  # noqa: BLE001 - any proposer failure is exit 2
        print(f"tallystick: proposer failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    # The reading's `_meta` goes into the posted trace: it holds the echo
    # warnings `audit` gates on. Without it a warning ended at this file
    # boundary, and the posted copy of a laundering log audited clean.
    meta = raw.get("_meta") if isinstance(raw, dict) else None
    if isinstance(meta, dict):
        posted["_meta"] = meta

    try:
        _write_json(args.out, posted)
    except CannotWrite as exc:
        print(f"tallystick: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2

    log = posted["_proposal"]
    print(f"posted {log['claims_posted']} claims, {log['credits_posted']} credits, "
          f"{log['prior_posted']} prior-only -> {args.out}")
    if log["dropped_claims"] or log["dropped_credits"]:
        print(f"dropped {len(log['dropped_claims'])} claims and "
              f"{len(log['dropped_credits'])} credits the model could not locate "
              f"verbatim (see _proposal in the file)")
    for w in log["warnings"]:
        print(f"warning: {w}")

    if args.no_audit:
        return 0
    print()
    audit_args = argparse.Namespace(
        trace=args.out, chain=None, quiet=False, json_out=None,
        accept_echo_warnings=list(getattr(args, "accept_echo_warnings", None) or []),
        require_declared_tools=getattr(args, "require_declared_tools", False))
    return _audit(audit_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tallystick",
        description="Audit an agent trace: does every claim in the answer trace "
                    "back to something outside the model?",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("audit", help="close the books on a posted trace")
    a.add_argument("trace", help="path to a posted run JSON file")
    a.add_argument("--json", dest="json_out", metavar="PATH",
                   help="write the machine-readable balance here")
    a.add_argument("--chain", metavar="CLAIM_ID",
                   help="print the full provenance chain for one claim")
    a.add_argument("--quiet", action="store_true",
                   help="exit code only - except echo warnings carried over from "
                        "the reading, which are still printed on stderr")
    _add_echo_gate_args(a)
    _add_strict_args(a)
    a.set_defaults(func=_audit)

    c = sub.add_parser("check-trace",
                       help="can this trace be audited at all? (no model, no cost)")
    c.add_argument("trace", help="path to a raw run JSON (artifacts + steps)")
    c.add_argument("--min-reachable", type=float, nargs="?", default=None,
                   const=DEFAULT_MIN_REACHABLE, metavar="SHARE",
                   help="also fail when fewer than this share of the artifacts a chain "
                        "passes through hold the model's own text rather than a tool's "
                        f"output (bare flag means {DEFAULT_MIN_REACHABLE:g}, read off the "
                        "AgentHallu run, not a constant). Off by default: the share is "
                        "reported either way, but only defects decide the verdict")
    c.add_argument("--json", dest="json_out", metavar="PATH",
                   help="write the machine-readable report here")
    c.add_argument("--quiet", action="store_true",
                   help="exit code only - except echo warnings, which are still "
                        "printed on stderr, one line each")
    _add_echo_gate_args(c)
    _add_strict_args(c)
    _add_source_args(c)
    c.set_defaults(func=_check_trace)

    v = sub.add_parser("convert",
                       help="read an OpenAI chat log or OTel span export as a trace")
    v.add_argument("trace", metavar="LOG", help="path to the log to read")
    v.add_argument("-o", "--out", required=True, metavar="PATH",
                   help="where to write the tallystick trace")
    v.add_argument("--quiet", action="store_true",
                   help="counts only, without the next-step line")
    _add_source_args(v)
    v.set_defaults(func=_convert)

    p = sub.add_parser("propose", help="let a model post claims and credits")
    p.add_argument("trace", help="path to a raw run JSON (artifacts + steps)")
    _add_source_args(p)
    p.add_argument("-o", "--out", required=True, metavar="PATH",
                   help="where to write the posted trace")
    p.add_argument("--proposer", default="anthropic", choices=["anthropic", "fake"],
                   help="'fake' replays canned answers from --script; no network")
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--script", metavar="PATH",
                   help="JSON list of canned proposer answers (for --proposer fake)")
    p.add_argument("--no-audit", action="store_true",
                   help="write the posted trace without auditing it")
    # For the audit `propose` runs on the file it wrote.
    _add_echo_gate_args(p)
    _add_strict_args(p)
    p.set_defaults(func=_propose)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `tallystick run.json` means `tallystick audit run.json`.
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "audit")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
