"""tallystick — provenance accounting for agent runs.

An Exchequer tally was a stick notched with an amount and split lengthwise. Payer
and payee each kept a half. To settle, the halves were fitted back together: the
notches and the grain of the wood had to line up. No clerk's opinion was involved,
and a forged half simply did not fit.

That is the whole idea here. Every claim an agent makes is one half of a stick. The
evidence behind it is the other. They either fit - the quoted text is really at that
address, the step really had that artifact in hand, and the thing quoted is itself
funded, all the way back to something outside the model - or they do not, and the
books say so without anyone being asked to judge.

    from tallystick import audit
    balance = audit("run.json")
    assert balance.books_balance
"""

from .auditability import Auditability, check_trace
from .io import load_run, load_run_file, read_json_file, read_meta
from .ledger import ChainHop, ClaimAudit, ClaimStatus, TrialBalance, close_books
from .report import chain_view, summary
from .types import (
    Account, AccountType, Artifact, ArtifactKind, Claim, Entry, Run, Step,
    TraceError,
)

__version__ = "0.9.0"

__all__ = [
    "audit", "check_trace", "Auditability", "close_books", "load_run", "load_run_file",
    "TrialBalance", "ClaimAudit", "ClaimStatus", "ChainHop",
    "summary", "chain_view",
    "Run", "Step", "Artifact", "ArtifactKind", "Claim", "Entry",
    "Account", "AccountType", "TraceError",
]


def audit(source) -> TrialBalance:
    """Close the books on a run given as a path, a dict, or a Run.

    Raises TraceError when the file cannot be read or is not a trace. The
    answer's coverage - how much of it stands under any claim - is
    `report.answer_cover(run, balance)`; `tallystick audit` exits 1 when most of
    the answer stands under none."""
    if isinstance(source, Run):
        return close_books(source)
    raw = source if isinstance(source, dict) else read_json_file(source)
    run = load_run(raw)
    read_meta(raw)
    return close_books(run)
