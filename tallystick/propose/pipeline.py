"""Turn a raw run (artifacts + steps) into a posted run (claims + entries).

Two model calls per derived artifact's worth of text: one to segment it into
claims, then one per claim to propose credits from the inputs of the step that
produced it. Everything the model returns is *located* in the real text by exact
substring search before it is allowed into the run. What cannot be located is
logged and dropped, never guessed.

The result is a plain dict in the same on-disk format `io.load_run` reads, plus a
`_proposal` block recording what the proposer was and what was dropped. Write it
to a file; audit the file. The proposer is not part of the verdict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..types import Artifact, Run
from .base import Proposer, parse_json
from .prompts import (
    CREDIT_SYSTEM, CREDIT_USER, SEGMENT_SYSTEM, SEGMENT_USER, fence_for,
    format_sources,
)


@dataclass
class ProposalLog:
    proposer: str
    claims_posted: int = 0
    credits_posted: int = 0
    prior_posted: int = 0
    dropped_claims: List[Dict[str, str]] = field(default_factory=list)
    dropped_credits: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _as_list(value: Any, what: str, log: ProposalLog) -> List[Any]:
    """The model promised a list. Anything else is logged and treated as empty.

    A bare string is the dangerous case: iterating it would post one claim per
    character, and nothing downstream would notice.
    """
    if isinstance(value, list):
        return value
    log.warnings.append(f"proposer returned {type(value).__name__} for {what}; expected a list")
    return []


def _split_to_claims(span: Tuple[int, int], claims: List[Tuple[int, int]],
                     ) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Resolve a credit span into a derived artifact against that artifact's claims.

    The segmenter is told to skip meta text ("In summary,"); the credit prompt is
    not, so an honest quote can straddle characters no claim owns. Left alone, the
    audit would call that laundering. But trimming freely is worse: a quote that
    clips two characters of a real claim must not be rewritten into those two
    characters and counted.

    So the rule uses containment only, no thresholds. For each claim the quote
    touches:
      * quote inside the claim  -> the model quoted part of a vouched sentence: keep
      * claim inside the quote  -> the model quoted a whole vouched sentence, maybe
                                   with meta text around it: keep the claim
      * neither                 -> a partial straddle; ambiguous, refused
    Returns (kept spans, refused claim spans). Characters of the quote outside every
    claim are counted separately by _chars_outside and logged, so trimming is
    never silent.
    """
    lo, hi = span
    kept: List[Tuple[int, int]] = []
    refused: List[Tuple[int, int]] = []
    for a, b in claims:
        if not (a < hi and lo < b):
            continue
        if a <= lo and hi <= b:          # quote inside claim
            kept.append((lo, hi))
        elif lo <= a and b <= hi:        # claim inside quote
            kept.append((a, b))
        else:
            refused.append((a, b))
    return kept, refused


def _locate(haystack: str, needle: str, taken: List[Tuple[int, int]],
            ) -> Optional[Tuple[int, int]]:
    """Find `needle` verbatim in `haystack`, skipping regions already claimed.

    Exact match first; then a casefolded match, which forgives capitalisation
    only. No fuzzier than that - fuzziness here would be a way for the model to
    "find" text that is not there.
    """
    needle = needle.strip()
    if not needle:
        return None
    attempts = [(haystack, needle)]
    hay_cf, ndl_cf = haystack.casefold(), needle.casefold()
    # Casefolding can change string length (ß -> ss); only use it when indices
    # into the folded text are still indices into the original.
    if len(hay_cf) == len(haystack) and len(ndl_cf) == len(needle):
        attempts.append((hay_cf, ndl_cf))
    for hay, ndl in attempts:
        start = 0
        while True:
            i = hay.find(ndl, start)
            if i < 0:
                break
            span = (i, i + len(ndl))
            if not any(a < span[1] and span[0] < b for a, b in taken):
                return span
            start = i + 1
    return None


def _chars_outside(content: str, span: Tuple[int, int],
                   kept: List[Tuple[int, int]]) -> int:
    """Letters and digits of `span` not inside any kept span. Only alphanumerics count: a live run showed the segmenter returning claims without their final full stop while the credit quote included it; that punctuation is not unvouched text."""
    n = 0
    for i in range(span[0], span[1]):
        if not content[i].isalnum():
            continue
        if not any(a <= i < b for a, b in kept):
            n += 1
    return n


def _ask_json(proposer: Proposer, system: str, user: str, log: ProposalLog,
              what: str) -> Dict[str, Any]:
    """One model call, parsed. A reply that is not a JSON object is asked for
    once more - a missing comma is not a verdict about the text - and the
    retry is logged. A second failure raises, as before: guessing is worse."""
    for attempt in (1, 2):
        raw = proposer.complete(system, user)
        try:
            return parse_json(raw)
        except ValueError as exc:
            if attempt == 2:
                raise
            log.warnings.append(f"{what}: unparseable reply, asked again ({exc})")
    raise AssertionError("unreachable")


def segment_artifact(art: Artifact, proposer: Proposer, log: ProposalLog,
                     ) -> List[Dict[str, Any]]:
    fence = fence_for(art.content)
    reply = _ask_json(proposer, SEGMENT_SYSTEM, SEGMENT_USER.format(
        text=art.content, fence=fence, close=fence.replace("<", ">")),
        log, f"segment {art.artifact_id}")
    proposed = _as_list(reply.get("claims", []), "claims", log)

    spans: List[Tuple[int, int]] = []
    for text in proposed:
        text = str(text)
        span = _locate(art.content, text, spans)
        if span is None:
            reason = ("overlaps a claim already located"
                      if _locate(art.content, text, [])
                      else "not a verbatim substring")
            log.dropped_claims.append(
                {"artifact_id": art.artifact_id, "text": text, "reason": reason})
            continue
        spans.append(span)

    # Ids follow text order, not the order the model happened to answer in, so
    # `summary.c3` is always the third claim a reader meets in the summary.
    spans.sort()
    claims = [
        {"claim_id": f"{art.artifact_id}.c{i + 1}", "artifact_id": art.artifact_id,
         "start": a, "end": b}
        for i, (a, b) in enumerate(spans)
    ]
    log.claims_posted += len(claims)
    return claims


def propose_credits(claim: Dict[str, Any], claim_text: str, sources: List[Artifact],
                    claim_spans: Dict[str, List[Tuple[int, int]]],
                    proposer: Proposer, log: ProposalLog) -> List[Dict[str, Any]]:
    """`claim_spans` maps derived artifact id -> its claim spans, for snapping."""
    by_id = {a.artifact_id: a for a in sources}
    fence = fence_for(claim_text)
    reply = _ask_json(
        proposer, CREDIT_SYSTEM,
        CREDIT_USER.format(claim=claim_text, fence=fence,
                           close=fence.replace("<", ">"),
                           sources=format_sources(sources)),
        log, f"credits for {claim['claim_id']}")
    credits = _as_list(reply.get("credits", []), "credits", log)

    entries: List[Dict[str, Any]] = []
    seen_quotes: set = set()
    cid = claim["claim_id"]

    def drop(aid: str, quote: str, reason: str) -> None:
        log.dropped_credits.append(
            {"claim_id": cid, "artifact_id": aid, "quote": quote, "reason": reason})

    for cr in credits:
        if not isinstance(cr, dict):
            drop("", str(cr), "credit is not an object")
            continue
        aid, quote = str(cr.get("artifact_id", "")), str(cr.get("quote", ""))
        art = by_id.get(aid)
        if art is None:
            drop(aid, quote, "artifact not among the step's inputs")
            continue
        span = _locate(art.content, quote, [])
        if span is None:
            drop(aid, quote, "quote is not a verbatim substring of the artifact")
            continue

        if art.kind.is_root:
            spans = [span]
        else:
            spans, refused = _split_to_claims(span, claim_spans.get(aid, []))
            for a, b in refused:
                drop(aid, quote, "quote only partially overlaps claim "
                     f"{aid}#{a}-{b}; refused as ambiguous")
            outside = _chars_outside(art.content, span, spans)
            if outside:
                log.warnings.append(
                    f"{cid}: {outside} characters of a quote into {aid} lie outside "
                    "any claim and were not credited")
            if not spans:
                drop(aid, quote, "quote covers no whole claim of the cited artifact")
                continue

        # One quote that covers several claims becomes several entries that share
        # a group: the ledger closes a group only if every member closes, so the
        # quote inherits the worst claim it covered - as one wide entry would.
        key = (aid, tuple(spans))
        if key in seen_quotes:
            continue  # same credit twice adds nothing to the books
        seen_quotes.add(key)
        group = f"{cid}.g{len(entries) + 1}" if len(spans) > 1 else ""
        for a, b in spans:
            account = f"EVIDENCE:{aid}#{a}-{b}"
            entry = {
                "entry_id": f"{cid}.e{len(entries) + 1}",
                "claim_id": cid,
                "account": account,
                "quoted_span": art.content[a:b],
                "proposed_by": proposer.name,
            }
            if group:
                entry["group"] = group
            entries.append(entry)

    if not entries:
        # The honest position: the model found nothing external. Recorded, never
        # funding. This is what makes an invented sentence visible downstream.
        entries.append({
            "entry_id": f"{claim['claim_id']}.e0",
            "claim_id": claim["claim_id"],
            "account": "PRIOR:model",
            "quoted_span": "",
            "proposed_by": proposer.name,
        })
        log.prior_posted += 1
    else:
        log.credits_posted += len(entries)
    return entries


def post_run(run: Run, proposer: Proposer) -> Dict[str, Any]:
    """Segment and post every derived artifact. Returns a posted-trace dict."""
    log = ProposalLog(proposer=getattr(proposer, "name", type(proposer).__name__))
    claims: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
    claim_spans: Dict[str, List[Tuple[int, int]]] = {}

    run.validate()
    if run.claims or run.entries:
        log.warnings.append(
            f"input already had {len(run.claims)} claims and {len(run.entries)} "
            "entries; they are discarded and re-posted")

    # Derived artifacts in step order (so logs read like the run), then any
    # derived artifact no step produced - those get segmented too, and their
    # claims will fail reachability in the audit, which is the correct verdict
    # for text of unknown origin.
    ordered: List[Artifact] = []
    for step in run.steps:
        for aid in step.outputs:
            art = run.artifacts[aid]
            if not art.kind.is_root and art not in ordered:
                ordered.append(art)
    for art in run.artifacts.values():
        if not art.kind.is_root and art not in ordered:
            log.warnings.append(f"artifact {art.artifact_id!r} has no producing step")
            ordered.append(art)

    # Pass 1: segment every derived artifact. Pass 2: post credits. Two passes,
    # because a credit into a derived artifact is resolved against that artifact's
    # claims, and those must all exist before any credit is judged - otherwise the
    # verdict would depend on the order steps happen to appear in the file.
    per_artifact: Dict[str, List[Dict[str, Any]]] = {}
    for art in ordered:
        art_claims = segment_artifact(art, proposer, log)
        per_artifact[art.artifact_id] = art_claims
        claims.extend(art_claims)
        claim_spans[art.artifact_id] = [(c["start"], c["end"]) for c in art_claims]

    for art in ordered:
        step = run.producing_step(art.artifact_id)
        sources = [run.artifacts[i] for i in (step.inputs if step else ())]
        for c in per_artifact[art.artifact_id]:
            text = art.content[c["start"]:c["end"]]
            entries.extend(
                propose_credits(c, text, sources, claim_spans, proposer, log))

    return {
        "artifacts": [
            {"artifact_id": a.artifact_id, "kind": a.kind.value,
             "title": a.title, "content": a.content}
            for a in run.artifacts.values()
        ],
        "steps": [
            {"step_id": s.step_id, "kind": s.kind,
             "inputs": list(s.inputs), "outputs": list(s.outputs)}
            for s in run.steps
        ],
        "assumptions": dict(run.assumptions),
        "claims": claims,
        "entries": entries,
        "_proposal": asdict(log),
    }
