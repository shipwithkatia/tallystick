"""Deterministic verification of journal entries.

Nothing in this module calls a model, reads the network, or makes a judgement about
meaning. Given the same run it returns the same verdicts, byte for byte, forever.
`tests/test_no_model_imports.py` enforces that mechanically.

An entry passes three gates, cheapest first:

  1. EXISTS       the cited artifact is in the run at all
  2. REACHABLE    the step that produced the claim actually had that artifact as an
                  input  <- the conservation rule; this is the novel gate
  3. QUOTED       the text at the cited span really is what the proposer quoted

Gate 2 is what makes this a trace auditor rather than one more span checker. A model
summarising step 7's output cannot legitimately cite a document that was only
retrieved at step 9, no matter how good the quote looks.
"""

from __future__ import annotations

from typing import List

from .normalize import levenshtein, normalize
from .types import AccountType, Entry, Run

#: Fraction of a span's length that may differ and still count as the same quote.
#: Covers trimmed edges and stray typographic characters, nothing more.
SPAN_TOLERANCE = 0.02

#: Reasons an entry can be rejected. Stable strings - they end up in reports and in
#: users' assertions, so treat them as public API.
ARTIFACT_UNKNOWN = "artifact_unknown"
NOT_REACHABLE = "not_reachable_from_step"
SPAN_OUT_OF_RANGE = "span_out_of_range"
SPAN_MISMATCH = "span_mismatch"
NO_QUOTE = "no_quoted_span"
PRIOR_NEVER_FUNDS = "prior_never_funds"
ASSUMPTION_UNDECLARED = "assumption_undeclared"
SELF_CITATION = "self_citation"
OK = "ok"


def verify_entry(run: Run, entry: Entry) -> Entry:
    """Set `entry.verified` and `entry.reason`. Returns the same entry, mutated."""
    account = entry.account

    # PRIOR is a real bookkeeping position: the model asserted something out of its
    # own weights. We record it rather than hide it, but it never funds a claim.
    if account.type is AccountType.PRIOR:
        entry.verified = False
        entry.reason = PRIOR_NEVER_FUNDS
        return entry

    # A premise the caller declared before the run. It funds, and reports mark it so
    # a reader can see which conclusions rest on assumptions rather than evidence.
    # It must be declared in the run itself; an assumption invented at posting time
    # would be a free pass for any claim.
    if account.type is AccountType.ASSUMPTION:
        if account.label in run.assumptions:
            entry.verified = True
            entry.reason = OK
        else:
            entry.verified = False
            entry.reason = ASSUMPTION_UNDECLARED
        return entry

    claim = run.claims[entry.claim_id]

    # --- gate 1: the artifact exists ------------------------------------- #
    artifact = run.artifacts.get(account.artifact_id or "")
    if artifact is None:
        entry.verified = False
        entry.reason = ARTIFACT_UNKNOWN
        return entry

    # An artifact cannot fund its own claims; that is circular and would let any
    # text bootstrap itself into being supported.
    if artifact.artifact_id == claim.artifact_id:
        entry.verified = False
        entry.reason = SELF_CITATION
        return entry

    # --- gate 2: conservation -------------------------------------------- #
    step = run.producing_step(claim.artifact_id)
    if step is None or account.artifact_id not in step.inputs:
        entry.verified = False
        entry.reason = NOT_REACHABLE
        return entry

    # --- gate 3: the quote is really there -------------------------------- #
    start, end = account.start or 0, account.end or 0
    if not (0 <= start < end <= len(artifact.content)):
        entry.verified = False
        entry.reason = SPAN_OUT_OF_RANGE
        return entry

    if not entry.quoted_span.strip():
        entry.verified = False
        entry.reason = NO_QUOTE
        return entry

    actual = normalize(artifact.slice(start, end))
    quoted = normalize(entry.quoted_span)
    if actual == quoted:
        entry.verified = True
        entry.reason = OK
        return entry

    # No tolerance floor: a quote shorter than 1/SPAN_TOLERANCE characters gets
    # no free edit at all. Otherwise "14%" could be cited as "44%" and pass.
    cap = int(len(actual) * SPAN_TOLERANCE)
    if cap and levenshtein(actual, quoted, cap) <= cap:
        entry.verified = True
        entry.reason = OK
        return entry

    entry.verified = False
    entry.reason = SPAN_MISMATCH
    return entry


def verify_run(run: Run) -> List[Entry]:
    """Verify every entry in the run, in order. Idempotent."""
    return [verify_entry(run, e) for e in run.entries]
