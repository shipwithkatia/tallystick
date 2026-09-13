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
unconfirmed, 1 otherwise, 2 when the audit could not run at all (a
malformed trace, a missing SDK or key, a proposer failure). A bad API key must never
read as "books do not balance". That is what turns this from a report into a gate
you can put in CI and fail a build on.

`check-trace` comes before either: it reads a raw trace and reports how much of
the run a provenance audit can look at, and what would have to be recorded for
the rest. No model, no claims, no cost - and a `partial` verdict there is why a
later clean audit may mean less than it looks. It also exits 1 while the reading
reports an echo from an earlier turn that nobody has reviewed
(`unreviewed_echo_warnings`, cleared tool by tool with `--accept-echo-warning NAME`).

`propose` is the only place the verdict path touches the model side, and it does so
lazily, inside the subcommand, so `tallystick audit` never imports an SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auditability import DEFAULT_MIN_REACHABLE, check_trace, report
from .convert import FORMATS, hint_for, read_any
from .adapters.openai_chat import DEFAULT_MAX_TOOL_CHARS
from .io import load_run, read_json_file
from .ledger import close_books
from .report import chain_view, summary
from .types import TraceError

SUBCOMMANDS = ("audit", "check-trace", "convert", "propose")

#: The reason for exit 1 when an echo warning is unreviewed, and the flag that
#: confirms one tool's warnings. Named once, because the terminal, `--quiet`,
#: `--json` and the tests all have to say exactly the same thing.
UNREVIEWED_ECHO = "unreviewed_echo_warnings"
ACCEPT_ECHO_FLAG = "--accept-echo-warning"
_ECHO_FIELDS = ("result", "tool", "line")


def _add_echo_gate_args(parser: argparse.ArgumentParser) -> None:
    """The flag that confirms echo warnings, one tool at a time. There is no
    flag that confirms them all: accepted once in CI, it would pass next
    month's new warning about a different tool without a word."""
    parser.add_argument(
        ACCEPT_ECHO_FLAG, dest="accept_echo_warnings", action="append", default=[],
        metavar="NAME",
        help="confirm, for one tool, that you reviewed the results the reading "
             "kept as evidence although they end in a line the model wrote in an "
             "earlier call. Repeatable, one tool each time. A warning about any "
             f"tool not named keeps the exit at 1 ({UNREVIEWED_ECHO}); a "
             "confirmation clears that reason and no other")


def _echo_warnings(meta) -> list[dict]:
    """The reader's warnings about echoes from an earlier turn, one dict each:
    `result`, `tool`, `line`, and `text` for the terminal.

    The tool name is taken from the reader's structured record, never from the
    warning text: a tool named `read_note): x` writes a warning that reads like
    one about `read_note`, and a confirmation by name must not be foolable by a
    name. A warning with no structured record - a trace written before it
    existed, or edited by hand - has no known tool, and no name confirms it."""
    if not isinstance(meta, dict):
        return []
    texts = [str(t) for t in meta.get("echoes_from_earlier_turns") or []]
    details = meta.get("echo_warning_details")
    details = details if isinstance(details, list) else []
    warnings: list[dict] = []
    for i in range(max(len(texts), len(details))):
        record = details[i] if i < len(details) and isinstance(details[i], dict) else {}
        result, tool, line = (str(record.get(k) or "") for k in _ECHO_FIELDS)
        text = texts[i] if i < len(texts) else f"{result} ({tool}): {line}"
        warnings.append({"result": result, "tool": tool, "line": line or text,
                         "text": text})
    return warnings


def _split_echo_warnings(warnings: list[dict], names) -> tuple[list[dict], list[dict]]:
    """(unreviewed, accepted). A warning is accepted only when its own tool was
    named; a warning whose tool is unknown never is."""
    confirmed = set(names or ())
    accepted = [w for w in warnings if w["tool"] and w["tool"] in confirmed]
    unreviewed = [w for w in warnings if w not in accepted]
    return unreviewed, accepted


def _gate_block(code: int, reasons: list[str], unreviewed: list[dict],
                accepted: list[dict]) -> dict:
    """The exit code and why, for --json. `verdict` alone would read "auditable"
    on a run that exits 1 for an unreviewed echo."""
    def plain(ws):
        return [{k: w[k] for k in _ECHO_FIELDS} for w in ws]
    return {"exit_code": code, "reasons": reasons,
            "echo_warnings": {"accepted": plain(accepted), "unreviewed": plain(unreviewed)}}


def _echo_gate_lines(unreviewed: list[dict], accepted: list[dict], *,
                     short: bool) -> list[str]:
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
            lines.append(f"tallystick: exit 1 - {UNREVIEWED_ECHO}: {n} tool result(s) end "
                         f"in a line the model wrote in an earlier call; review them, "
                         f"then pass --tool-returns-model-text NAME or "
                         f"{ACCEPT_ECHO_FLAG} NAME")
        else:
            lines += [f"UNREVIEWED ECHO WARNINGS - exit 1 ({UNREVIEWED_ECHO})",
                      f"  {n} tool result(s) end in a line the model wrote in an earlier",
                      "  call. The reading kept them as evidence, so the verdict above",
                      "  counts them as evidence: a clean result here is not yet a checked",
                      "  one. For each tool: if it hands the model's own text back, pass",
                      "  --tool-returns-model-text NAME; if its result is a real",
                      "  confirmation, confirm that tool by name."]
        lines += listed(unreviewed, "  " if short else "    ")
        names = sorted({w["tool"] for w in unreviewed if w["tool"]})
        if names:
            lines.append("  to confirm: " + " ".join(
                f"{ACCEPT_ECHO_FLAG} {shlex.quote(name)}" for name in names))
        if any(not w["tool"] for w in unreviewed):
            lines.append("  a warning with no recorded tool name cannot be confirmed; "
                         "read the log again with this version")
    if accepted:
        lines.append(f"tallystick: {len(accepted)} echo warning(s) accepted by name with "
                     f"{ACCEPT_ECHO_FLAG}, kept as evidence:" if short else
                     f"Echo warnings accepted by name with {ACCEPT_ECHO_FLAG}: "
                     f"{len(accepted)} tool result(s) kept as evidence.")
        lines += listed(accepted, "  " if short else "    ")
    return lines


def _say_echo_gate(unreviewed: list[dict], accepted: list[dict], *, quiet: bool) -> None:
    """Under the report - or, with --quiet, on stderr, which keeps stdout empty
    for scripts while every CI log still records the warning."""
    lines = _echo_gate_lines(unreviewed, accepted, short=quiet)
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


def _reading_notes(meta: dict) -> list[str]:
    """Everything the reading itself left out or had to decide, wrapped for a
    terminal. A reader that lost a message and said nothing turns its own bug
    into a finding against the user's recorder, which is the failure this whole
    project is about."""
    import textwrap

    lines: list[str] = []
    if meta.get("reader_confidence"):
        lines.append(f"reader: {meta['reader_confidence']}")
    for key, label in (("model_text_tools", "read as the model's own text"),
                       ("verbatim_tools", "read as external text, verbatim")):
        named = meta.get(key) or []
        if named:
            lines.append(f"tools {label}: {', '.join(map(str, named))}")
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
                        "result(s) ending in a line the model wrote in an "
                        "earlier call, kept as evidence")):
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
    )


def _write_json(path: str, payload) -> None:
    """Write a JSON output file. OSError here is a "could not run" failure, not a
    verdict, so callers turn it into exit 2 - never into exit 1."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _audit(args: argparse.Namespace) -> int:
    try:
        # What `load_run_file` does, in two steps, so the reading's `_meta` that
        # `propose` carried into the posted trace is at hand for the echo gate.
        raw = read_json_file(args.trace)
        run = load_run(raw)
    except (OSError, ValueError) as exc:
        # TraceError is a ValueError; so is json.JSONDecodeError. Either way the
        # input is unusable, and that is a different failure from "books don't
        # balance" - hence exit code 2, not 1.
        print(f"tallystick: cannot audit this trace: {exc}", file=sys.stderr)
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
    reasons: list[str] = []
    if not balance.books_balance:
        reasons.append("books_do_not_balance")
    if unreviewed:
        reasons.append(UNREVIEWED_ECHO)
    code = 1 if reasons else 0

    if args.chain:
        if args.chain not in balance.audits:
            known = ", ".join(sorted(balance.audits)) or "(none)"
            print(f"tallystick: no claim {args.chain!r}; claims are: {known}",
                  file=sys.stderr)
            return 2
        print(chain_view(run, balance, args.chain))
    elif not args.quiet:
        print(summary(run, balance))
    _say_echo_gate(unreviewed, accepted, quiet=args.quiet)

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
        payload["gate"] = _gate_block(code, reasons, unreviewed, accepted)
        try:
            _write_json(args.json_out, payload)
        except OSError as exc:
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
    except (OSError, ValueError) as exc:
        print(f"tallystick: cannot read this trace: {exc}.{_hint(args, seen)}",
              file=sys.stderr)
        return 2
    meta = raw.get("_meta") if isinstance(raw, dict) else None
    result = check_trace(run, min_reachable=args.min_reachable,
                   meta=meta if isinstance(meta, dict) else None)
    notes = _reading_notes(meta if isinstance(meta, dict) else {})
    # A tool result ending in a line the model wrote in an earlier call is kept
    # as evidence, because the log cannot tell a value handed back from a value
    # confirmed - so the verdict counts it as evidence. Exiting 0 on that, with
    # nobody having looked, certifies a place nobody checked. It exits 1 until
    # the operator confirms each tool by name; a confirmation clears that
    # reason only, and only for the tool it names.
    unreviewed, accepted = _split_echo_warnings(
        _echo_warnings(meta), getattr(args, "accept_echo_warnings", None))
    reasons: list[str] = []
    if result.verdict != "auditable":
        reasons.append(f"verdict:{result.verdict}")
    if unreviewed:
        reasons.append(UNREVIEWED_ECHO)
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
        for line in notes:
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
                        "echo_warning_details",
                        "model_text_tools", "verbatim_tools",
                        "notes", "otel") if k in meta}
            if reading:
                payload["reading"] = reading
        payload["gate"] = _gate_block(code, reasons, unreviewed, accepted)
        try:
            _write_json(args.json_out, payload)
        except OSError as exc:
            print(f"tallystick: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2
    if not args.quiet:
        print(report(result))
    _say_echo_gate(unreviewed, accepted, quiet=args.quiet)
    return code


def _convert(args: argparse.Namespace) -> int:
    """Read someone else's log and write a tallystick trace. Exit 0 when the
    file was written, 2 when it could not be read or written. There is no
    verdict here and so no exit 1: converting is reading, not judging."""
    seen: dict = {}
    try:
        raw, source = _read_raw(args, seen)
        run = load_run(raw)            # a converter that writes an unloadable
    except (OSError, ValueError) as exc:   # file is worse than one that refuses
        print(f"tallystick: cannot read this log: {exc}.{_hint(args, seen)}",
              file=sys.stderr)
        return 2

    try:
        _write_json(args.out, raw)
    except OSError as exc:
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
    for line in _reading_notes(meta):
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
    except OSError as exc:
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
        accept_echo_warnings=list(getattr(args, "accept_echo_warnings", None) or []))
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
