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
    """

    entry_id: str
    claim_id: str
    account: Account
    quoted_span: str = ""
    amount: float = 1.0
    proposed_by: str = "manual"
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
