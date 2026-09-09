"""Command line entry point.

The exit code is the product decision here: 0 when every claim in the final answer
traces back to a root, 1 otherwise, 2 when the trace itself is malformed. That is
what turns this from a report into a gate you can put in CI and fail a build on.
"""

from __future__ import annotations

import argparse
import json
import sys

from .io import load_run_file
from .ledger import close_books
from .report import chain_view, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tallystick",
        description="Audit an agent trace: does every claim in the answer trace "
                    "back to something outside the model?",
    )
    parser.add_argument("trace", help="path to a run JSON file")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="write the machine-readable balance here")
    parser.add_argument("--chain", metavar="CLAIM_ID",
                        help="print the full provenance chain for one claim")
    parser.add_argument("--quiet", action="store_true",
                        help="exit code only, no report")
    args = parser.parse_args(argv)

    try:
        run = load_run_file(args.trace)
    except (OSError, ValueError) as exc:
        # TraceError is a ValueError; so is json.JSONDecodeError. Either way the
        # input is unusable, and that is a different failure from "books don't
        # balance" - hence exit code 2, not 1.
        print(f"tallystick: cannot audit this trace: {exc}", file=sys.stderr)
        return 2
    balance = close_books(run)

    if args.chain:
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
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    return 0 if balance.books_balance else 1


if __name__ == "__main__":
    sys.exit(main())
