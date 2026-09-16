"""Deterministic verification of journal entries.

Nothing in this module calls a model, reads the network, or makes a judgement about
meaning. Given the same run it returns the same verdicts, byte for byte, forever.
`tests/test_no_model_imports.py` enforces that mechanically.

An entry passes three gates, cheapest first:

  1. EXISTS       the cited artifact is in the run at all
  2. REACHABLE    the step that produced the claim actually had that artifact as an
                  input - and, for a root, that it was not made from that step's
                  own output  <- the conservation rule; this is the novel gate
  3. QUOTED       the text at the cited span really is what the proposer quoted

Gate 2 is what makes this a trace auditor rather than one more span checker. A step
can only cite what it declares it was given, no matter how good the quote looks.

There is no step order in a trace to check: the arrays can be shuffled and mean
the same run, so "step 7" and "step 9" are positions in a file, not times. What
is checked is the data flow, in one place. The chain walk in ledger.py follows a
derived artifact on to its own claims, and its cycle guard sees a loop there;
at a root it stops. So gate 2 refuses a root made, directly or through other
steps, from the citing step's own output: a tool that takes the answer and
hands it back is not a source for the answer, even when the recording declares
its result an input and calls it a root. The flow as a whole is not required
to be free of loops, and `check-trace` does not report one.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .normalize import levenshtein, normalize
from .types import AccountType, Entry, Run, Step

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


def verify_entry(run: Run, entry: Entry, _flow: Optional[_Flow] = None) -> Entry:
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

    # The same circle one step removed, at the one place the chain walk cannot
    # see it: a root. The walk follows a derived artifact on to its own claims,
    # and its cycle guard catches a loop there; at a root it stops. So a root
    # that is among this step's own outputs, or was made from them further on,
    # would fund the step's text with that text (review 13, 1.3 and 1.4).
    if artifact.kind.is_root and (_flow or _Flow(run)).made_from_own_output(
            step, account.artifact_id):
        entry.verified = False
        entry.reason = SELF_CITATION
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


def components(nodes: List[str], edges: Dict[str, List[str]]) -> List[List[str]]:
    """Strongly connected components of a directed graph (Tarjan), each one
    listed after every component it reaches. Written without recursion: the
    graphs here are chains of claims or steps, and a chain long enough to matter
    is long enough to overflow Python's stack walked recursively."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: set = set()
    stack: List[str] = []
    found: List[List[str]] = []
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            children = edges.get(node, ())
            if i < len(children):
                work.append((node, i + 1))
                child = children[i]
                if child not in index:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            for child in children:
                if child in on_stack:
                    low[node] = min(low[node], low[child])
            if low[node] == index[node]:
                members: List[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    members.append(top)
                    if top == node:
                        break
                found.append(members)
    return found


class _Flow:
    """The data flow of one run, read once: which step produced each artifact,
    and which loop of steps, if any, each step sits on.

    An input of step S was made from S's own output exactly when the step that
    produced it can be reached from S - and since it feeds S, that puts both on
    one loop: one strongly connected component. So the question costs one pass
    over the inputs per run, not a walk per step (a walk per step took 1.8 s on
    a looped 300-turn chat)."""

    def __init__(self, run: Run):
        self.producer: Dict[str, str] = {}
        for s in run.steps:
            for aid in s.outputs:
                self.producer[aid] = s.step_id
        feeds: Dict[str, List[str]] = {s.step_id: [] for s in run.steps}
        for s in run.steps:
            for aid in s.inputs:
                if aid in self.producer:
                    feeds[self.producer[aid]].append(s.step_id)
        self.loop: Dict[str, int] = {}
        for n, members in enumerate(components([s.step_id for s in run.steps], feeds)):
            for m in members:
                self.loop[m] = n

    def made_from_own_output(self, step: Step, artifact_id: str) -> bool:
        """True when `artifact_id`, an input of `step`, is one of `step`'s
        outputs or was produced from them through other steps."""
        source = self.producer.get(artifact_id)
        return source is not None and (
            source == step.step_id or self.loop[source] == self.loop[step.step_id])


def verify_run(run: Run) -> List[Entry]:
    """Verify every entry in the run, in order. Idempotent."""
    flow = _Flow(run)
    return [verify_entry(run, e, flow) for e in run.entries]
