"""Command line entry point.

    tallystick audit       run.json      close the books on a posted trace
    tallystick check-trace raw.json      can this trace be audited at all?
    tallystick propose     raw.json -o run.json
                                         let a model post claims and credits,
                                         then audit the file it wrote

`audit` is the default: `tallystick run.json` works.

The exit code is the product decision here: 0 when every claim in the final answer
traces back to a root, 1 otherwise, 2 when the audit could not run at all (a
malformed trace, a missing SDK or key, a proposer failure). A bad API key must never
read as "books do not balance". That is what turns this from a report into a gate
you can put in CI and fail a build on.

`check-trace` comes before either: it reads a raw trace and reports how much of
the run a provenance audit can look at, and what would have to be recorded for
the rest. No model, no claims, no cost - and a `partial` verdict there is why a
later clean audit may mean less than it looks.

`propose` is the only place the verdict path touches the model side, and it does so
lazily, inside the subcommand, so `tallystick audit` never imports an SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auditability import DEFAULT_MIN_REACHABLE, check_trace, report
from .io import load_run, load_run_file, read_json_file
from .ledger import close_books
from .report import chain_view, summary

SUBCOMMANDS = ("audit", "check-trace", "propose")


def _write_json(path: str, payload) -> None:
    """Write a JSON output file. OSError here is a "could not run" failure, not a
    verdict, so callers turn it into exit 2 - never into exit 1."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _audit(args: argparse.Namespace) -> int:
    try:
        run = load_run_file(args.trace)
    except (OSError, ValueError) as exc:
        # TraceError is a ValueError; so is json.JSONDecodeError. Either way the
        # input is unusable, and that is a different failure from "books don't
        # balance" - hence exit code 2, not 1.
        print(f"tallystick: cannot audit this trace: {exc}", file=sys.stderr)
        return 2
    balance = close_books(run)

    if not balance.final_claim_ids:
        # Nothing was audited. That must not leave as exit 1: "the books do not
        # balance" is a verdict about the run, and the likeliest way to meet
        # this is to audit a raw trace before posting anything to it. Reporting
        # an unposted file as a failed audit is the exact confusion the exit
        # codes exist to prevent.
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

    if args.chain:
        if args.chain not in balance.audits:
            known = ", ".join(sorted(balance.audits)) or "(none)"
            print(f"tallystick: no claim {args.chain!r}; claims are: {known}",
                  file=sys.stderr)
            return 2
        print(chain_view(run, balance, args.chain))
    elif not args.quiet:
        print(summary(run, balance))

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
        try:
            _write_json(args.json_out, payload)
        except OSError as exc:
            print(f"tallystick: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2

    return 0 if balance.books_balance else 1


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
    try:
        raw = read_json_file(args.trace)
        run = load_run(raw)          # one read, one parse; `_meta` comes from `raw`
    except (OSError, ValueError) as exc:
        print(f"tallystick: cannot read this trace: {exc}", file=sys.stderr)
        return 2
    meta = raw.get("_meta") if isinstance(raw, dict) else None
    result = check_trace(run, min_reachable=args.min_reachable,
                   meta=meta if isinstance(meta, dict) else None)
    if args.json_out:
        try:
            _write_json(args.json_out, result.as_dict())
        except OSError as exc:
            print(f"tallystick: cannot write {args.json_out}: {exc}", file=sys.stderr)
            return 2
    if not args.quiet:
        print(report(result))
    return 0 if result.verdict == "auditable" else 1


def _propose(args: argparse.Namespace) -> int:
    # The model side is imported here and nowhere else in the verdict path, so
    # `tallystick audit` never loads an SDK.
    from .propose import FakeProposer, post_run

    try:
        run = load_run_file(args.trace)
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
    audit_args = argparse.Namespace(trace=args.out, chain=None, quiet=False,
                                    json_out=None)
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
    a.add_argument("--quiet", action="store_true", help="exit code only")
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
    c.add_argument("--quiet", action="store_true", help="exit code only")
    c.set_defaults(func=_check_trace)

    p = sub.add_parser("propose", help="let a model post claims and credits")
    p.add_argument("trace", help="path to a raw run JSON (artifacts + steps)")
    p.add_argument("-o", "--out", required=True, metavar="PATH",
                   help="where to write the posted trace")
    p.add_argument("--proposer", default="anthropic", choices=["anthropic", "fake"],
                   help="'fake' replays canned answers from --script; no network")
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--script", metavar="PATH",
                   help="JSON list of canned proposer answers (for --proposer fake)")
    p.add_argument("--no-audit", action="store_true",
                   help="write the posted trace without auditing it")
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
