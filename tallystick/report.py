"""Human-readable output.

Two audiences. The terminal summary answers "did this run close?" in five lines,
which is what a CI log needs. The chain view answers "where did it go wrong?",
which is what a person debugging an agent needs, and it is the part no faithfulness
score can give them.
"""

from __future__ import annotations

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
}


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
    lines.append(f"  coverage         {balance.coverage:.1%}")
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

    verdict = "BOOKS BALANCE" if balance.books_balance else "BOOKS DO NOT BALANCE"
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
