"""Human-readable output.

Two audiences. The terminal summary answers "did this run close?" in five lines,
which is what a CI log needs. The chain view answers "where did it go wrong?",
which is what a person debugging an agent needs, and it is the part no faithfulness
score can give them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .ledger import ClaimStatus, TrialBalance
from .types import Run

_BAR = "─" * 58

_MARK = {
    ClaimStatus.GROUNDED: "ok",
    ClaimStatus.ASSUMED: "assumed",
    ClaimStatus.LAUNDERED: "LAUNDERED",
    ClaimStatus.UNSUPPORTED: "UNSUPPORTED",
    ClaimStatus.PRIOR_ONLY: "PRIOR ONLY",
    ClaimStatus.CIRCULAR: "CIRCULAR",
    ClaimStatus.UNCHECKED: "NOT CHECKED",
}


@dataclass(frozen=True)
class AnswerCover:
    """How much of the final answer the audit speaks for, counted in letters and
    digits of the final_answer artifacts - the unit the reading uses elsewhere,
    so spaces, punctuation and markdown do not move it.

    `closed` stands under a final claim that closes, `claimed` under any final
    claim, `total` is the whole answer. A letter under two claims counts once."""

    total: int
    claimed: int
    closed: int

    @property
    def coverage(self) -> float:
        """Share of the WHOLE answer under claims that close."""
        return self.closed / self.total if self.total else 0.0

    @property
    def unclaimed(self) -> float:
        """Share of the whole answer under no claim at all: not checked."""
        return (self.total - self.claimed) / self.total if self.total else 0.0

    @property
    def mostly_unclaimed(self) -> bool:
        """More than half of the answer stands under no claim.

        The line is half, named before it was measured (round 18): below it the
        verdict speaks for most of what the user saw, above it for less than
        half, and "BOOKS BALANCE" no longer describes the answer. The count is
        exact integers, so an answer of 54 letters with 27 claimed is not
        mostly unclaimed and one with 26 is."""
        return 2 * (self.total - self.claimed) > self.total


def answer_cover(run: Run, balance: TrialBalance) -> AnswerCover:
    """The final answer's letters and digits, and how many stand under a final
    claim, and under one that closes.

    Review 16 posted one claim on the first letter of a 54-character answer, and
    the audit printed "coverage 100.0%": the old number was the share of CLAIMS
    that close, which says nothing about how much of the answer carries one.
    `balance.coverage` is still that share; this is the answer's."""
    total = claimed = closed = 0
    for art in run.final_artifacts():
        text = art.content
        spans = [(c.start, c.end, bool(balance.audits.get(c.claim_id)
                                       and balance.audits[c.claim_id].ok))
                 for c in run.claims_in(art.artifact_id)]
        # Letters and digits before each position, so a span is counted in one
        # subtraction; spans are merged first, so overlaps count once. A claim
        # posted over a long answer costs its two ends, not its length.
        before = [0]
        for ch in text:
            before.append(before[-1] + ch.isalnum())
        total += before[-1]
        claimed += _letters_under([(a, b) for a, b, _ok in spans], before)
        closed += _letters_under([(a, b) for a, b, ok in spans if ok], before)
    return AnswerCover(total=total, claimed=claimed, closed=closed)


def _letters_under(spans, before: List[int]) -> int:
    """Letters and digits under the union of `spans`, given the running count."""
    n = len(before) - 1
    count, reach = 0, 0
    for start, end in sorted(spans):
        start, end = max(start, reach, 0), min(end, n)
        if end > start:
            count += before[end] - before[start]
            reach = end
    return count


def cover_lines(cover: AnswerCover, closed: int, claims: int) -> List[str]:
    """The coverage block of the summary: the whole answer first - the share
    under claims that close, and the share under no claim at all - then the
    claims. Printed without any flag."""
    if not cover.total:
        return ["  coverage         the answer holds no letters or digits to cover",
                f"  claims closed    {closed} of {claims}"]
    return [f"  coverage         {cover.coverage:.1%} of the answer "
            f"({cover.closed} of {cover.total} letters and digits)",
            f"  under no claim   {cover.unclaimed:.1%} of the answer - not checked",
            f"  claims closed    {closed} of {claims}"]


def _bar(n: int, total: int, width: int = 18) -> str:
    filled = 0 if total == 0 else round(width * n / total)
    return "█" * filled + "░" * (width - filled)


def summary(run: Run, balance: TrialBalance) -> str:
    lines: List[str] = []
    finals = balance.final_claim_ids
    counts = balance.counts(finals)
    total = len(finals)

    lines.append("")
    if total == 0:
        lines.append("No claims found in any final_answer artifact — nothing to audit.")
        lines.append("BOOKS DO NOT BALANCE")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"Trial balance — {total} claims in the final answer")
    lines.append(_BAR)
    for status in ClaimStatus:
        n = counts[status.value]
        if n == 0 and status not in (ClaimStatus.GROUNDED, ClaimStatus.LAUNDERED):
            continue
        pct = 0 if total == 0 else round(100 * n / total)
        lines.append(f"  {status.value:<12} {n:>3}  {_bar(n, total)} {pct:>3}%")
    lines.append(_BAR)
    cover = answer_cover(run, balance)
    ok = sum(1 for c in finals if balance.audits[c].ok)
    lines.extend(cover_lines(cover, ok, total))
    lines.append(f"  laundering rate  {balance.laundering_rate:.1%}"
                 "   <- invisible to one-hop checks")
    lines.append("")

    breaks = balance.injection_points()
    if breaks:
        lines.append(f"{len(breaks)} claim(s) did not close:")
        lines.append("")
        for audit in breaks:
            lines.append(f"  [{_MARK[audit.status]}] {audit.claim_id}: {audit.text.strip()}")
            if audit.break_step_id:
                step = next((s for s in run.steps if s.step_id == audit.break_step_id), None)
                kind = f" ({step.kind})" if step else ""
                lines.append(f"      introduced at step {audit.break_step_id}{kind}"
                             f" — {audit.break_reason}")
            for i, hop in enumerate(audit.chain):
                arrow = "      " + ("└─ " if i == len(audit.chain) - 1 else "├─ ")
                lines.append(f"{arrow}{hop.account}")
            lines.append("")

    unchecked = balance.unchecked()
    if unchecked:
        # Not a break, so not listed with the breaks: nothing was found against
        # these claims, because their chains were not walked at all.
        lines.append(f"{len(unchecked)} claim(s) were not checked - {unchecked[0].break_reason}, "
                     f"which is past what this audit walks:")
        lines.append("")
        for audit in unchecked:
            lines.append(f"  [{_MARK[audit.status]}] {audit.claim_id}: {audit.text.strip()}")
        lines.append("")

    if balance.books_balance and cover.mostly_unclaimed:
        # The claims balance, and they cover less than half of what the user
        # saw. Said in the verdict line, because it is the line people read.
        verdict = (f"BOOKS BALANCE ON {cover.claimed / cover.total:.0%} OF THE ANSWER - "
                   f"{cover.unclaimed:.0%} IS UNDER NO CLAIM, NOT CHECKED")
    elif balance.books_balance:
        verdict = "BOOKS BALANCE"
    elif unchecked and not breaks:
        verdict = "BOOKS NOT CHECKED"
    else:
        verdict = "BOOKS DO NOT BALANCE"
    lines.append(verdict)
    lines.append("")
    return "\n".join(lines)


def chain_view(run: Run, balance: TrialBalance, claim_id: str) -> str:
    """Full provenance chain for one claim, root last."""
    audit = balance.audits[claim_id]
    out = [f"{claim_id} [{audit.status.value}] depth={audit.depth}",
           f"  \"{audit.text.strip()}\"", ""]
    for i, hop in enumerate(audit.chain):
        art = run.artifacts.get(hop.artifact_id)
        kind = art.kind.value if art else "?"
        out.append(f"  {i + 1}. {hop.claim_id} @ step {hop.step_id}")
        out.append(f"     credit {hop.account}  [{kind}]")
        if hop.quote:
            out.append(f"     quote  \"{hop.quote.strip()[:88]}\"")
    if audit.break_reason:
        out.append("")
        culprit = f" on claim {audit.break_claim_id}" if audit.break_claim_id else ""
        out.append(f"  chain breaks at step {audit.break_step_id}{culprit}: "
                   f"{audit.break_reason}")
        if audit.break_claim_id and audit.break_claim_id in balance.audits:
            upstream = balance.audits[audit.break_claim_id]
            out.append(f'  that claim reads: "{upstream.text.strip()}"')
            out.append("  and nothing outside the model funds it.")
    return "\n".join(out)
