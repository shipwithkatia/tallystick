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

Results do not depend on the order of arrays in the input file, nor on how the
claims are named: candidates are ranked explicitly, and the depth limit is a
property of each claim's chain, measured before the walk - not of how far the
walk had come when it met the claim (review 13, 1.2: it used to be, and renaming
a claim flipped a verdict).

Claims that cite each other in a loop are settled together, not walked. The walk
used to follow every simple path through such a loop and remember nothing it
computed there, so a hand-made trace of 11 KB kept `audit` busy for 42 seconds,
and each claim more cost nine times that (review 16, 1.1). Now each loop is a
strongly connected component, settled once, after everything it cites, its
claims closing one at a time, best verdict first (`_settle`). A claim closes
there exactly when it would on a walk that never passes the same claim twice -
a proof that loops back through a claim is never needed, because the part
after the second visit already proves that claim - so the status is that of
the old walk, reached without enumerating paths (round 17: the same status for
every claim of 40,000 random traces, 4,180 of them with a loop). What the loop
cannot fund is reported as circular at the claim being settled.

An all-of closes on its WORST member, closing members included: a quote over a
grounded sentence and an assumed one rests on the assumption (review 16, 1.2).
Taking the best closing member hid the assumption under "grounded", which
types.py promises never happens, and inside a loop it made the verdict depend
on which claim sorts first (review 16, 1.3).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .types import AccountType, Claim, Run
from .verify import components, verify_run, worst_reason

#: A claim whose provenance goes deeper than this is not checked. Real runs are
#: tens of hops deep at most. The limit was set to keep a recursive walk within
#: Python's stack; claims are no longer walked recursively (round 17), and the
#: limit stays because exit 2 on a chain this deep is what the README promises.
#: It is not a finding about the run, so hitting it is UNCHECKED - "could not
#: check" - and never LAUNDERED, which would be "checked, does not close".
MAX_DEPTH = 256

#: The reason an UNCHECKED claim carries. Stable, like verify.py's reasons.
TOO_DEEP = f"chain deeper than {MAX_DEPTH}"

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
    CIRCULAR = "circular"        # provenance loops; unfunded
    UNCHECKED = "unchecked"      # chain deeper than MAX_DEPTH; not checked, no verdict


#: Statuses that mean "the books did not close for this claim". UNCHECKED is
#: not one of them: that claim was not checked, which is not the same as a
#: claim checked and found wanting. It does not balance either.
FAILING = (ClaimStatus.LAUNDERED, ClaimStatus.UNSUPPORTED,
           ClaimStatus.PRIOR_ONLY, ClaimStatus.CIRCULAR)

#: Explicit preference when a claim has several candidate outcomes. Lower is
#: better. Ties are broken by shorter chain. This, not entry order, decides.
_RANK = {
    ClaimStatus.GROUNDED: 0,
    ClaimStatus.ASSUMED: 1,
    ClaimStatus.UNCHECKED: 2,   # never met inside a walk: see close_books
    ClaimStatus.LAUNDERED: 3,
    ClaimStatus.CIRCULAR: 4,
    ClaimStatus.PRIOR_ONLY: 5,
    ClaimStatus.UNSUPPORTED: 6,
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

    def _key(self) -> Tuple:
        # A total order. Rank and depth decide; the ids that name the break and
        # the chain break the remaining ties, so which of two equally bad
        # candidates is reported never depends on entry order in the file.
        return (_RANK[self.status], self.depth,
                self.break_step_id or "", self.break_claim_id or "",
                tuple(h.account for h in self.chain))


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

    def unchecked(self) -> List[ClaimAudit]:
        """Final claims that were not checked because their chain is deeper than
        MAX_DEPTH. The books do not balance with one of these in the answer, but
        nothing was found against it either; the CLI exits 2 when these are the
        only claims that did not close."""
        return [self.audits[c] for c in self.final_claim_ids
                if self.audits[c].status is ClaimStatus.UNCHECKED]

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


def _upstream(run: Run, entry) -> Optional[List[Claim]]:
    """The claims a verified evidence entry makes the walk resolve next, or None
    where the entry ends the walk (a root, an assumption, a span nobody vouched
    for). One function for the walk and for `_heights`, so the depth measured
    before the walk is the depth the walk goes."""
    acct = entry.account
    if not entry.verified or acct.type is not AccountType.EVIDENCE:
        return None
    cited = run.artifacts[acct.artifact_id]
    if cited.kind.is_root:
        return None
    upstream = run.claims_covering(cited.artifact_id, acct.start, acct.end)
    if not upstream or not _span_is_accounted_for(
            run, cited.artifact_id, acct.start, acct.end, upstream):
        return None
    return upstream


def _claim_graph(run: Run) -> Tuple[Dict[str, List[str]], List[List[str]]]:
    """Which claims each claim resolves next, and the strongly connected
    components of that graph, each listed after every component it reaches.
    One graph for the depth limit and for settling, so what is measured is what
    is walked."""
    edges: Dict[str, List[str]] = {cid: [] for cid in run.claims}
    for entry in run.entries:
        claim = run.claims[entry.claim_id]
        if run.artifacts[claim.artifact_id].kind.is_root:
            continue    # settled as grounded without looking at entries
        for parent in _upstream(run, entry) or ():
            edges[claim.claim_id].append(parent.claim_id)
    return edges, components(sorted(edges), edges)


def _heights(edges: Dict[str, List[str]], comps: List[List[str]]) -> Dict[str, int]:
    """How deep the walk from each claim can go: 0 for a claim whose entries end
    at roots (or that nothing funds), otherwise one more than the deepest claim
    it resolves next. Depends on the graph alone - not on names, not on file
    order, not on which claim the walk started from.

    A cycle of claims counts as deep as it is long, plus what lies beyond it:
    that bounds the walk inside it."""
    height: Dict[str, int] = {}
    for members in comps:
        # Listed after everything it reaches, so every height it needs is known.
        inside = set(members)
        out = [height[c] for m in members for c in edges[m] if c not in inside]
        h = (len(members) - 1) + (1 + max(out) if out else 0)
        for m in members:
            height[m] = h
    return height


def _resolve(
    run: Run,
    claim: Claim,
    audits: Dict[str, ClaimAudit],
    settling: Dict[str, Optional[ClaimAudit]],
) -> ClaimAudit:
    """The verdict on one claim, from the verdicts of the claims it cites.

    `audits` holds claims already settled. `settling` holds the claims of the
    loop being settled with this one: the best verdict each has closed on so
    far, or None while it has not closed. A cited claim still open in the loop
    is circular from here - on this pass nothing real stands behind it yet.
    """
    step = run.producing_step(claim.artifact_id)
    step_id = step.step_id if step else None

    def make(status: ClaimStatus, **kw) -> ClaimAudit:
        return ClaimAudit(claim_id=claim.claim_id, text=claim.text,
                          artifact_id=claim.artifact_id, status=status, **kw)

    # A claim located in a root artifact is not something the agent asserted; it is
    # the evidence itself. Nothing to fund.
    if run.artifacts[claim.artifact_id].kind.is_root:
        return make(ClaimStatus.GROUNDED)

    def cited(parent: Claim) -> ClaimAudit:
        if parent.claim_id in audits:
            return audits[parent.claim_id]
        so_far = settling[parent.claim_id]     # KeyError: settled out of order
        if so_far is not None:
            return so_far
        # Bookkeeping calls this a circular reference; either way nothing real
        # is behind it. Named at the claim being settled: its provenance comes
        # back to the loop it is in.
        return make(ClaimStatus.CIRCULAR, break_claim_id=claim.claim_id,
                    break_step_id=step_id, break_reason="circular provenance")

    entries = run.entries_for(claim.claim_id)
    verified = [e for e in entries if e.verified]

    if not verified:
        # Nothing funds this claim at its own step. This claim IS the injection
        # point: the content appears here for the first time with nothing behind it.
        only_prior = bool(entries) and all(
            e.account.type is AccountType.PRIOR for e in entries
        )
        return make(
            ClaimStatus.PRIOR_ONLY if only_prior else ClaimStatus.UNSUPPORTED,
            break_claim_id=claim.claim_id, break_step_id=step_id,
            break_reason=("model prior, no external evidence" if only_prior
                          else worst_reason(entries)),
        )

    candidates: List[ClaimAudit] = []
    groups: List[str] = []          # parallel to candidates; "" = no group

    for entry in entries:
        if not entry.verified:
            if entry.group:
                # A grouped entry that does not fund - the verifier rejected its
                # quote, or it is a PRIOR/undeclared-assumption someone put in a
                # group by hand. Its group cannot close, whatever the others say.
                groups.append(entry.group)
                candidates.append(make(ClaimStatus.UNSUPPORTED,
                                       break_claim_id=claim.claim_id,
                                       break_step_id=step_id,
                                       break_reason=entry.reason))
            continue
        groups.append(entry.group)
        acct = entry.account
        hop = ChainHop(claim.claim_id, acct.artifact_id or "", step_id,
                       str(acct), entry.quoted_span)

        # A declared assumption terminates the chain, honestly labelled.
        if acct.type is AccountType.ASSUMPTION:
            candidates.append(make(ClaimStatus.ASSUMED, chain=[hop]))
            continue

        cited_artifact = run.artifacts[acct.artifact_id]

        # Root artifact: the chain legitimately stops here.
        if cited_artifact.kind.is_root:
            candidates.append(make(ClaimStatus.GROUNDED, chain=[hop]))
            continue

        # Derived artifact: the quote is real, but the quoted text must itself be
        # funded. Every claim the cited span touches must close, and the span must
        # be fully accounted for by claims - otherwise the citation inherits the
        # worst thing it covers, or covers text nobody vouched for.
        upstream = _upstream(run, entry)
        cited_step = run.producing_step(cited_artifact.artifact_id)
        cited_step_id = cited_step.step_id if cited_step else None

        if upstream is None:
            candidates.append(make(
                ClaimStatus.LAUNDERED, chain=[hop],
                break_step_id=cited_step_id,
                break_reason="cited span of a derived artifact is not fully "
                             "accounted for by any claim",
            ))
            continue

        # All-of: the worst member decides, and among members that close the
        # worst decides too - an assumed sentence under the quote makes the quote
        # rest on an assumption, however grounded its neighbour is.
        worst: Optional[ClaimAudit] = None
        worst_ok: Optional[ClaimAudit] = None
        for parent in sorted(upstream, key=lambda c: (c.start, c.claim_id)):
            presult = cited(parent)
            if presult.ok:
                if worst_ok is None or presult._key() > worst_ok._key():
                    worst_ok = presult
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
            assert worst_ok is not None
            candidates.append(make(worst_ok.status, chain=[hop] + worst_ok.chain))

    # Between independent entries the claim closes on the best (any-of): a real
    # second source is a real second source. Within a group - entries that came
    # from one quote covering several claims - it closes on the WORST (all-of):
    # the quote vouched for all of that text at once. Without this a proposer
    # that snaps a quote to claim boundaries would bypass the all-of rule that
    # _span_is_accounted_for enforces for a single wide entry. The worst member
    # decides whether the group closes and, where it does, on what.
    merged: List[ClaimAudit] = []
    by_group: Dict[str, List[ClaimAudit]] = {}
    for g, cand in zip(groups, candidates):
        if g:
            by_group.setdefault(g, []).append(cand)
        else:
            merged.append(cand)
    for members in by_group.values():
        failing = [m for m in members if not m.ok]
        merged.append(max(failing or members, key=lambda a: a._key()))

    return min(merged, key=lambda a: a._key())


def _settle(run: Run, members: List[str], edges: Dict[str, List[str]],
            audits: Dict[str, ClaimAudit]) -> None:
    """Settle one strongly connected component of the claim graph; everything it
    cites outside itself is already in `audits`.

    Inside a loop, claims close one at a time, best verdict first (Knuth's
    generalisation of Dijkstra's shortest paths). That order is sound because a
    chain is always worse than every claim it is built from: it has the worst
    status among them and one link more. So when the best claim still open is
    taken, nothing that closes later can give it a better chain, and its verdict
    is final. Each time a claim closes, only the claims that cite it are
    recomputed.

    A first version swept the whole loop until nothing changed. It gave the same
    verdicts, but a ring of 250 claims with one document at the far end took 250
    sweeps - 7 s with 2,000-character texts, and the square of the ring's length
    (lab notes of round 17). The order of the sweeps was set by the claim names.

    Claims that never close keep what they come to once every claim that closes
    has closed; what the loop cannot fund is circular from them."""
    settling: Dict[str, Optional[ClaimAudit]] = {m: None for m in members}
    if len(members) == 1 and members[0] not in edges[members[0]]:
        audits[members[0]] = _resolve(run, run.claims[members[0]], audits, settling)
        return
    inside = set(members)
    cited_by: Dict[str, List[str]] = {m: [] for m in members}
    for m in sorted(members):
        for parent in sorted(set(edges[m])):
            if parent in inside:
                cited_by[parent].append(m)

    best: Dict[str, ClaimAudit] = {}            # best closing verdict so far, per claim
    queue: List[Tuple[Tuple, str]] = []

    def consider(m: str) -> None:
        audit = _resolve(run, run.claims[m], audits, settling)
        if audit.ok and (m not in best or audit._key() < best[m]._key()):
            best[m] = audit
            heapq.heappush(queue, (audit._key(), m))

    for m in sorted(members):
        consider(m)
    while queue:
        key, m = heapq.heappop(queue)
        if settling[m] is not None or best[m]._key() != key:
            continue                           # closed already, or a stale entry
        settling[m] = best[m]
        closed = (_RANK[best[m].status], best[m].depth)
        for d in cited_by[m]:
            if settling[d] is not None:
                continue
            # A chain through m has at least m's status and one link more, so a
            # claim that already closes no worse than m itself gains nothing.
            if d in best and (_RANK[best[d].status], best[d].depth) <= closed:
                continue
            consider(d)
    for m in sorted(members):
        audits[m] = settling[m] or _resolve(run, run.claims[m], audits, settling)


def close_books(run: Run) -> TrialBalance:
    """Verify every entry, then resolve every claim back to its roots.

    Raises TraceError if the run is not auditable, whether it came from disk or
    was assembled by hand.
    """
    run.validate()
    verify_run(run)

    balance = TrialBalance()
    edges, comps = _claim_graph(run)
    # Too deep to walk: settled first, from the graph, so the answer is the same
    # whichever claim a walk would have started from. Every claim a walk from a
    # shallower claim reaches is shallower still, so no walk below meets one.
    for claim_id, height in _heights(edges, comps).items():
        if height > MAX_DEPTH:
            claim = run.claims[claim_id]
            balance.audits[claim_id] = ClaimAudit(
                claim_id=claim_id, text=claim.text, artifact_id=claim.artifact_id,
                status=ClaimStatus.UNCHECKED, break_reason=TOO_DEEP)
    # Every component after everything it cites, so what a claim cites outside
    # its own loop is always settled before it.
    for members in comps:
        if not any(m in balance.audits for m in members):
            _settle(run, members, edges, balance.audits)

    balance.final_claim_ids = sorted(
        c.claim_id
        for a in run.final_artifacts()
        for c in run.claims_in(a.artifact_id)
    )
    return balance
