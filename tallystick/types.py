"""Core data model.

The whole library rests on four ideas:

  Artifact  - a piece of text that exists somewhere in an agent run: a retrieved
              document, a tool result, an intermediate summary, the final answer.
  Step      - one action in the run. It consumes artifacts and produces artifacts.
  Claim     - one atomic assertion located inside an artifact, by character span.
  Entry     - a journal entry: this claim (debit) is funded by this evidence (credit).

An artifact is either a ROOT (it entered the run from outside: a document, a tool
result) or DERIVED (the agent wrote it: a summary, a plan, the answer). Roots
terminate a provenance chain. Derived artifacts do not - their own claims must be
funded in turn. That recursion is the entire point of this project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

_EVIDENCE_RE = re.compile(r"([^#]+)#(\d+)-(\d+)")


class TraceError(ValueError):
    """The run as supplied cannot be audited. Says where, so the fix is obvious."""

# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #


class ArtifactKind(str, Enum):
    """Where an artifact came from.

    ROOT kinds are external to the agent's reasoning: whatever they say, the agent
    did not make it up, it was handed to it. They are where a provenance chain is
    allowed to stop.

    DERIVED kinds were written by a model. A citation pointing at one of these is
    not evidence yet - it is a promissory note that must itself be redeemed.
    """

    DOCUMENT = "document"          # root: retrieved / supplied text
    TOOL_RESULT = "tool_result"    # root: output of a tool call
    INTERMEDIATE = "intermediate"  # derived: summary, plan, scratchpad, memory write
    FINAL_ANSWER = "final_answer"  # derived: what the user sees

    @property
    def is_root(self) -> bool:
        return self in (ArtifactKind.DOCUMENT, ArtifactKind.TOOL_RESULT)


@dataclass(frozen=True)
class Artifact:
    """A piece of text observed in the run, addressed by id, located by kind."""

    artifact_id: str
    kind: ArtifactKind
    content: str
    title: str = ""

    def slice(self, start: int, end: int) -> str:
        return self.content[start:end]


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Step:
    """One action in the run.

    `inputs` is the honest record of what this step could actually see. It is the
    basis of the conservation rule in verify.py: a claim produced by this step may
    only be credited against artifacts this step had in hand. A citation to a
    document the step never received is not a weak citation - it is impossible,
    and we can say so without asking a model.
    """

    step_id: str
    kind: str                      # retrieve | tool | summarize | plan | generate | answer
    inputs: Tuple[str, ...] = ()   # artifact ids consumed
    outputs: Tuple[str, ...] = ()  # artifact ids produced


# --------------------------------------------------------------------------- #
# Claims and entries
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    """One atomic assertion, located by character span inside its artifact.

    Spans, not sentence indices: spans survive re-chunking, and they let a claim in
    a summary be matched against the region of that summary another claim cites.
    """

    claim_id: str
    artifact_id: str
    start: int
    end: int
    text: str

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


class AccountType(str, Enum):
    EVIDENCE = "EVIDENCE"      # a span of another artifact
    PRIOR = "PRIOR"            # model's own knowledge; never funds a claim
    ASSUMPTION = "ASSUMPTION"  # user-declared premise; funds, but is marked


@dataclass(frozen=True)
class Account:
    """The credit side address.

    EVIDENCE:<artifact_id>#<start>-<end>   a span of some artifact
    PRIOR:model                            no external evidence at all
    ASSUMPTION:<id>                        a premise the caller declared up front
    """

    type: AccountType
    artifact_id: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    label: str = ""

    @classmethod
    def parse(cls, s: str) -> "Account":
        head, _, rest = s.partition(":")
        try:
            atype = AccountType(head)
        except ValueError:
            raise TraceError(f"unknown account type in {s!r}") from None
        if atype is not AccountType.EVIDENCE:
            if not rest:
                raise TraceError(f"account {s!r} needs a label after the colon")
            return cls(type=atype, label=rest)
        m = _EVIDENCE_RE.fullmatch(rest)
        if not m:
            raise TraceError(
                f"malformed evidence account {s!r}; "
                "expected EVIDENCE:<artifact_id>#<start>-<end>"
            )
        return cls(type=atype, artifact_id=m.group(1),
                   start=int(m.group(2)), end=int(m.group(3)))

    def __str__(self) -> str:
        if self.type is AccountType.EVIDENCE:
            return f"EVIDENCE:{self.artifact_id}#{self.start}-{self.end}"
        return f"{self.type.value}:{self.label}"


@dataclass
class Entry:
    """A journal entry. Debit the claim 1.00, credit an evidence account 1.00.

    `quoted_span` is what the proposer says it read at that address. The verifier
    compares it against what is actually there. A model can be wrong about what a
    quote proves; it cannot be wrong about whether the quote exists.

    `verified` and `reason` are written only by the verifier, never by a proposer.

    `group`: entries that came from ONE quote which covered several claims of a
    derived artifact share a group id. The ledger closes a group only if every
    member closes - the quote vouched for all of that text at once, so it must
    inherit the worst thing it covered, exactly as a single wide entry would.
    Without groups, a proposer that snaps a quote to claim boundaries would post
    independent entries and the claim would close on the best of them.
    """

    entry_id: str
    claim_id: str
    account: Account
    quoted_span: str = ""
    amount: float = 1.0
    proposed_by: str = "manual"
    group: str = ""
    verified: Optional[bool] = None
    reason: str = ""


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


@dataclass
class Run:
    """Everything observed in one agent execution, plus the entries posted for it."""

    artifacts: Dict[str, Artifact] = field(default_factory=dict)
    steps: List[Step] = field(default_factory=list)
    claims: Dict[str, Claim] = field(default_factory=dict)
    entries: List[Entry] = field(default_factory=list)
    #: Premises declared up front, id -> text. Only these may be cited as
    #: ASSUMPTION:<id>; anything else is rejected at verification.
    assumptions: Dict[str, str] = field(default_factory=dict)

    # -- lookups ----------------------------------------------------------- #

    def producing_step(self, artifact_id: str) -> Optional[Step]:
        for step in self.steps:
            if artifact_id in step.outputs:
                return step
        return None

    def entries_for(self, claim_id: str) -> List[Entry]:
        return [e for e in self.entries if e.claim_id == claim_id]

    def claims_in(self, artifact_id: str) -> List[Claim]:
        return [c for c in self.claims.values() if c.artifact_id == artifact_id]

    def claims_covering(self, artifact_id: str, start: int, end: int) -> List[Claim]:
        """Claims of `artifact_id` that overlap the span someone cited.

        This is the hinge of chain resolution. If a later step quotes bytes 40-95 of
        a summary, the claims of that summary overlapping 40-95 are what must be
        funded for the quote to mean anything.
        """
        return [c for c in self.claims_in(artifact_id) if c.overlaps(start, end)]

    def final_artifacts(self) -> List[Artifact]:
        return [a for a in self.artifacts.values() if a.kind is ArtifactKind.FINAL_ANSWER]

    # -- invariants ---------------------------------------------------------- #

    def validate(self) -> None:
        """Raise TraceError if the run cannot be audited.

        Called by the loader, and again by close_books so that a Run assembled
        by hand in Python gets the same checks as one read from disk.
        """
        from .normalize import normalize  # local import keeps types.py dependency-free

        seen_out: Dict[str, str] = {}
        seen_steps: set = set()
        for step in self.steps:
            # Two steps under one id are refused, not resolved. Everything that
            # asks "which step produced this" would otherwise take whichever came
            # first in the file, and the order of an array would decide the
            # verdict. Nothing in the trace says which of the two is the real
            # one, and guessing is the thing this tool does not do.
            if step.step_id in seen_steps:
                raise TraceError(
                    f"duplicate step id {step.step_id!r}: two steps under one id, "
                    f"so nothing can tell which of them produced what")
            seen_steps.add(step.step_id)
            for aid in (*step.inputs, *step.outputs):
                if aid not in self.artifacts:
                    raise TraceError(
                        f"step {step.step_id} references unknown artifact {aid!r}")
            for aid in step.outputs:
                if aid in seen_out and seen_out[aid] != step.step_id:
                    raise TraceError(
                        f"artifact {aid!r} is produced by both {seen_out[aid]} "
                        f"and {step.step_id}")
                seen_out[aid] = step.step_id

        for cid, claim in self.claims.items():
            if cid != claim.claim_id:
                raise TraceError(f"claim keyed {cid!r} has claim_id {claim.claim_id!r}")
            art = self.artifacts.get(claim.artifact_id)
            if art is None:
                raise TraceError(f"claim {cid} is in unknown artifact {claim.artifact_id!r}")
            if not (0 <= claim.start < claim.end <= len(art.content)):
                raise TraceError(
                    f"claim {cid} span {claim.start}-{claim.end} is outside artifact "
                    f"{art.artifact_id!r} (length {len(art.content)})")
            if normalize(claim.text) != normalize(art.slice(claim.start, claim.end)):
                raise TraceError(
                    f"claim {cid}.text does not match the text at its span")

        seen_entries: set = set()
        group_owner: Dict[str, str] = {}
        for entry in self.entries:
            if entry.entry_id in seen_entries:
                raise TraceError(f"duplicate entry id {entry.entry_id!r}")
            seen_entries.add(entry.entry_id)
            if entry.claim_id not in self.claims:
                raise TraceError(
                    f"entry {entry.entry_id} refers to unknown claim {entry.claim_id!r}")
            if entry.group:
                owner = group_owner.setdefault(entry.group, entry.claim_id)
                if owner != entry.claim_id:
                    raise TraceError(f"group {entry.group!r} spans claims {owner} "
                                     f"and {entry.claim_id}; a group belongs to one claim")
