"""Trial balance and provenance chain resolution.

Two things happen here.

The **trial balance** is the local, one-hop view: for each claim, did anything fund
it at the step that produced it? This is roughly what existing faithfulness tools
measure, and on its own it is not enough.

The **chain** is the transitive view, and it is why this library exists. A claim in
the final answer may quote an intermediate summary perfectly - the books balance
locally - while the sentence it quotes was itself invented three steps earlier. The
citation is real; the evidence is not. Following every credit back until it lands on
a root artifact (a document, a tool result) is the only way to see that, and it is
what the trial balance alone will always miss.

A claim whose chain breaks upstream is LAUNDERED: unsupported content that acquired
the appearance of support by passing through a step that copied it faithfully.

Two rules keep the walk sound:

  * A citation into a derived artifact is only as good as *every* claim it covers.
    Citing a whole summary does not launder the one bad sentence in it - it
    inherits it. Any-of would let a sloppy, wide citation walk straight through.
  * The cited span must actually be accounted for. Text nobody took responsibility
    for (no claim covers it) cannot fund anything.

Results do not depend on the order of arrays in the input file: candidates are
ranked explicitly, and nothing computed while a cycle was open is memoised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .types import AccountType, Claim, Run
from .verify import verify_run

#: Chains longer than this are treated as broken. Real runs are tens of hops deep
#: at most; anything past this is a loop the cycle guard could not see or a bug.
MAX_DEPTH = 256

# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #


class ClaimStatus(str, Enum):
    """Where a claim's provenance chain ended up."""

    GROUNDED = "grounded"        # chain reaches a root artifact
    ASSUMED = "assumed"          # chain terminates on a declared assumption
    LAUNDERED = "laundered"      # funded locally, chain breaks further back
    UNSUPPORTED = "unsupported"  # nothing funds it at its own step
    PRIOR_ONLY = "prior_only"    # only the model's own knowledge was offered
    CIRCULAR = "circular"        # provenance loops or runs too deep; unfunded


#: Statuses that mean "the books did not close for this claim".
FAILING = (ClaimStatus.LAUNDERED, ClaimStatus.UNSUPPORTED,
           ClaimStatus.PRIOR_ONLY, ClaimStatus.CIRCULAR)

#: Explicit preference when a claim has several candidate outcomes. Lower is
#: better. Ties are broken by shorter chain. This, not entry order, decides.
_RANK = {
    ClaimStatus.GROUNDED: 0,
    ClaimStatus.ASSUMED: 1,
    ClaimStatus.LAUNDERED: 2,
    ClaimStatus.CIRCULAR: 3,
    ClaimStatus.PRIOR_ONLY: 4,
    ClaimStatus.UNSUPPORTED: 5,
}


@dataclass
class ChainHop:
    """One link: this claim was funded by this span of this artifact."""

    claim_id: str
    artifact_id: str
    step_id: Optional[str]
    account: str
    quote: str


@dataclass
class ClaimAudit:
    """The verdict on one claim, with the chain that produced it."""

    claim_id: str
    text: str
    artifact_id: str
    status: ClaimStatus
    chain: List[ChainHop] = field(default_factory=list)
    break_claim_id: Optional[str] = None    # where the chain stopped
    break_step_id: Optional[str] = None     # the step that introduced unfunded content
    break_reason: str = ""

    @property
    def depth(self) -> int:
        """Hops from this claim to where its chain ends. Always len(chain)."""
        return len(self.chain)

    @property
    def ok(self) -> bool:
        return self.status in (ClaimStatus.GROUNDED, ClaimStatus.ASSUMED)

    def _key(self) -> Tuple[int, int]:
        return (_RANK[self.status], self.depth)


@dataclass
class TrialBalance:
    """The whole run, closed."""

    audits: Dict[str, ClaimAudit] = field(default_factory=dict)
    final_claim_ids: List[str] = field(default_factory=list)

    # -- headline numbers --------------------------------------------------- #

    def counts(self, claim_ids: List[str]) -> Dict[str, int]:
        """Status histogram over the given claims. Pass `final_claim_ids` for the
        headline numbers; `audits` also holds every intermediate claim and the
        claims of root artifacts (always grounded), which are not the headline."""
        out = {s.value: 0 for s in ClaimStatus}
        for cid in claim_ids:
            out[self.audits[cid].status.value] += 1
        return out

    @property
    def books_balance(self) -> bool:
        """True only if there is a final answer and every claim in it closes.

        An empty or truncated trace does NOT balance. A gate that passes on
        missing input is not a gate.
        """
        if not self.final_claim_ids:
            return False
        return all(self.audits[c].ok for c in self.final_claim_ids)

    @property
    def coverage(self) -> float:
        """Share of final-answer claims that close (grounded or on a declared
        assumption)."""
        if not self.final_claim_ids:
            return 0.0
        ok = sum(1 for c in self.final_claim_ids if self.audits[c].ok)
        return ok / len(self.final_claim_ids)

    @property
    def laundering_rate(self) -> float:
        """Share of final claims that look funded locally but are not grounded.

        This is the number the one-hop tools cannot produce, and the reason to run
        this library at all: it is the gap between "cites something" and "is
        supported by something".
        """
        if not self.final_claim_ids:
            return 0.0
        n = sum(1 for c in self.final_claim_ids
                if self.audits[c].status is ClaimStatus.LAUNDERED)
        return n / len(self.final_claim_ids)

    def injection_points(self) -> List[ClaimAudit]:
        """Failing final claims, each carrying the step where its chain broke.

        The practical output of the whole library: not "your answer is 74% faithful"
        but "step s3 (summarize) introduced a sentence nothing supports, and it
        reached the user through claim ans_3".
        """
        return [self.audits[c] for c in self.final_claim_ids
                if self.audits[c].status in FAILING]


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _span_is_accounted_for(run: Run, artifact_id: str, start: int, end: int,
                           claims: List[Claim]) -> bool:
    """Every non-whitespace character of [start, end) lies inside some claim."""
    content = run.artifacts[artifact_id].content
    covered = bytearray(end - start)
    for c in claims:
        lo, hi = max(c.start, start), min(c.end, end)
        for i in range(lo, hi):
            covered[i - start] = 1
    for i, ch in enumerate(content[start:end]):
        if not ch.isspace() and not covered[i]:
            return False
    return True


def _resolve(
    run: Run,
    claim: Claim,
    audits: Dict[str, ClaimAudit],
    visiting: Set[str],
    depth: int,
) -> Tuple[ClaimAudit, bool]:
    """Depth-first walk from a claim back towards root artifacts.

    Returns (audit, tainted). `tainted` is True when the result was computed while
    a cycle involving an ancestor was open; such results depend on where the walk
    entered the cycle and are therefore not memoised.
    """
    if claim.claim_id in audits:
        return audits[claim.claim_id], False

    step = run.producing_step(claim.artifact_id)
    step_id = step.step_id if step else None

    def make(status: ClaimStatus, **kw) -> ClaimAudit:
        return ClaimAudit(claim_id=claim.claim_id, text=claim.text,
                          artifact_id=claim.artifact_id, status=status, **kw)

    # A claim located in a root artifact is not something the agent asserted; it is
    # the evidence itself. Nothing to fund.
    if run.artifacts[claim.artifact_id].kind.is_root:
        audits[claim.claim_id] = make(ClaimStatus.GROUNDED)
        return audits[claim.claim_id], False

    # Cycle guard. Bookkeeping calls this a circular reference; either way nothing
    # real is behind it. Not cached: the verdict depends on the entry point.
    if claim.claim_id in visiting or depth > MAX_DEPTH:
        return make(ClaimStatus.CIRCULAR, break_claim_id=claim.claim_id,
                    break_step_id=step_id,
                    break_reason="circular provenance" if depth <= MAX_DEPTH
                    else f"chain deeper than {MAX_DEPTH}"), True

    entries = run.entries_for(claim.claim_id)
    verified = [e for e in entries if e.verified]

    if not verified:
        # Nothing funds this claim at its own step. This claim IS the injection
        # point: the content appears here for the first time with nothing behind it.
        only_prior = bool(entries) and all(
            e.account.type is AccountType.PRIOR for e in entries
        )
        audit = make(
            ClaimStatus.PRIOR_ONLY if only_prior else ClaimStatus.UNSUPPORTED,
            break_claim_id=claim.claim_id, break_step_id=step_id,
            break_reason=("model prior, no external evidence" if only_prior
                          else (entries[0].reason if entries else "no entry posted")),
        )
        audits[claim.claim_id] = audit
        return audit, False

    visiting.add(claim.claim_id)
    candidates: List[ClaimAudit] = []
    tainted = False

    for entry in verified:
        acct = entry.account
        hop = ChainHop(claim.claim_id, acct.artifact_id or "", step_id,
                       str(acct), entry.quoted_span)

        # A declared assumption terminates the chain, honestly labelled.
        if acct.type is AccountType.ASSUMPTION:
            candidates.append(make(ClaimStatus.ASSUMED, chain=[hop]))
            continue

        cited = run.artifacts[acct.artifact_id]

        # Root artifact: the chain legitimately stops here.
        if cited.kind.is_root:
            candidates.append(make(ClaimStatus.GROUNDED, chain=[hop]))
            continue

        # Derived artifact: the quote is real, but the quoted text must itself be
        # funded. Every claim the cited span touches must close, and the span must
        # be fully accounted for by claims - otherwise the citation inherits the
        # worst thing it covers, or covers text nobody vouched for.
        upstream = run.claims_covering(cited.artifact_id, acct.start, acct.end)
        cited_step = run.producing_step(cited.artifact_id)
        cited_step_id = cited_step.step_id if cited_step else None

        if not upstream or not _span_is_accounted_for(
                run, cited.artifact_id, acct.start, acct.end, upstream):
            candidates.append(make(
                ClaimStatus.LAUNDERED, chain=[hop],
                break_step_id=cited_step_id,
                break_reason="cited span of a derived artifact is not fully "
                             "accounted for by any claim",
            ))
            continue

        worst: Optional[ClaimAudit] = None
        best_ok: Optional[ClaimAudit] = None
        for parent in sorted(upstream, key=lambda c: (c.start, c.claim_id)):
            presult, ptaint = _resolve(run, parent, audits, visiting, depth + 1)
            tainted = tainted or ptaint
            if presult.ok:
                if best_ok is None or presult._key() < best_ok._key():
                    best_ok = presult
            else:
                if worst is None or presult._key() > worst._key():
                    worst = presult

        if worst is not None:
            candidates.append(make(
                ClaimStatus.LAUNDERED, chain=[hop] + worst.chain,
                break_claim_id=worst.break_claim_id,
                break_step_id=worst.break_step_id,
                break_reason=worst.break_reason,
            ))
        else:
            assert best_ok is not None
            candidates.append(make(best_ok.status, chain=[hop] + best_ok.chain))

    visiting.discard(claim.claim_id)

    result = min(candidates, key=lambda a: a._key())
    if not tainted:
        audits[claim.claim_id] = result
    return result, tainted


def close_books(run: Run) -> TrialBalance:
    """Verify every entry, then resolve every claim back to its roots.

    Raises TraceError if the run is not auditable, whether it came from disk or
    was assembled by hand.
    """
    run.validate()
    verify_run(run)

    balance = TrialBalance()
    # Resolve in a fixed order so that anything cycle-dependent is at least
    # reproducible; sorted ids, not file order.
    for claim_id in sorted(run.claims):
        audit, _ = _resolve(run, run.claims[claim_id], balance.audits, set(), 0)
        balance.audits.setdefault(claim_id, audit)

    balance.final_claim_ids = sorted(
        c.claim_id
        for a in run.final_artifacts()
        for c in run.claims_in(a.artifact_id)
    )
    return balance
