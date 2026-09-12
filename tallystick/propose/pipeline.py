"""Turn a raw run (artifacts + steps) into a posted run (claims + entries).

Two model calls per derived artifact's worth of text: one to segment it into
claims, then one per claim to propose credits from the inputs of the step that
produced it. Everything the model returns is *located* in the real text - exact substring
search, then a match on words that forgives punctuation and case and never a
changed character - before it is allowed into the run. What cannot be located
is logged and dropped, never guessed.

The result is a plain dict in the same on-disk format `io.load_run` reads, plus a
`_proposal` block recording what the proposer was and what was dropped. Write it
to a file; audit the file. The proposer is not part of the verdict.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..types import Artifact, ArtifactKind, Run
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
    unasked_claims: int = 0       # on_demand: claims no chain reached, never asked
    coverage_claims: int = 0      # sentences the segmenter skipped, posted anyway
    tolerant_locates: int = 0     # texts found only by the tolerant locate
    self_evident_credits: int = 0  # credits posted because the claim text is in a source
    whole_answer_claims: int = 0  # short final answers posted whole when the segmenter returned nothing


def _as_list(value: Any, what: str, log: ProposalLog) -> List[Any]:
    """The model promised a list. Anything else is logged and treated as empty.

    A bare string is the dangerous case: iterating it would post one claim per
    character, and nothing downstream would notice.
    """
    if isinstance(value, list):
        return value
    log.warnings.append(f"proposer returned {type(value).__name__} for {what}; expected a list")
    return []


_SEP_CATS = frozenset({"Pd", "Ps", "Pe", "Pi", "Pf", "Pc", "Zs", "Zl", "Zp"})
_SEP_CHARS = frozenset(".,;:!?'\"`\u2026\u00a1\u00bf\u00b7")


def _is_sep(ch: str) -> bool:
    """Characters a word match may disagree on: whitespace, dashes, brackets,
    quotation marks, connectors and sentence punctuation. Everything else is
    part of a word - a "%" or "$" changes what a number means and is kept."""
    return ch.isspace() or unicodedata.category(ch) in _SEP_CATS or ch in _SEP_CHARS


def _words(text: str) -> List[Tuple[int, int, str]]:
    """(start, end, folded word) for every maximal run of non-separator
    characters in `text`, with three extensions for numbers: a mark between
    two digits ("3.5", "1,000"), a dash directly before a number that starts
    a word ("-5%") and a bracket pair hugging a single token with a digit in
    it ("(5%)", "(2024)") belong to the word. "-5%" and "(5%)" are not "5%",
    and "3-5" is not "3.5". Folding is NFC + casefold of the word
    alone, so the span is still read from the original text whatever the fold
    does to the word's length."""
    n = len(text)

    def sep(k: int) -> bool:
        # "3.5", "1,000", "10:30", "2020-2021": a mark between two digits is
        # part of the number, not a place where "3-5" may stand in for "3.5".
        ch = text[k]
        if not _is_sep(ch):
            return False
        return ch.isspace() or not (
            0 < k < n - 1 and text[k - 1].isdigit() and text[k + 1].isdigit())

    out: List[Tuple[int, int, str]] = []
    i = 0
    while i < n:
        if sep(i):
            i += 1
            continue
        j = i
        while j < n and not sep(j):
            j += 1
        a, b = i, j
        if text[a].isdigit() and a > 0 and unicodedata.category(text[a - 1]) == "Pd" \
                and (a == 1 or _is_sep(text[a - 2])):
            a -= 1
        if any(ch.isdigit() for ch in text[i:j]) and a > 0 and b < n \
                and text[a - 1] == "(" and text[b] == ")" \
                and (a == 1 or _is_sep(text[a - 2])) and (b + 1 == n or _is_sep(text[b + 1])):
            a, b = a - 1, b + 1
        out.append((a, b, unicodedata.normalize("NFC", text[a:b]).casefold()))
        i = j
    return out


def _core(content: str, span: Tuple[int, int]) -> Tuple[int, int]:
    """`span` shrunk to its first and last word character. Punctuation and
    spacing at the edges are not text anybody vouched for or quoted."""
    a, b = span
    while a < b and _is_sep(content[a]):
        a += 1
    while b > a and _is_sep(content[b - 1]):
        b -= 1
    return a, b


def _split_to_claims(content: str, span: Tuple[int, int],
                     claims: List[Tuple[int, int]],
                     ) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Resolve a credit span into a derived artifact against that artifact's claims.

    The segmenter is told to skip meta text ("In summary,"); the credit prompt is
    not, so an honest quote can straddle characters no claim owns. Left alone, the
    audit would call that laundering. But trimming freely is worse: a quote that
    clips two characters of a real claim must not be rewritten into those two
    characters and counted.

    So the rule uses containment only, no thresholds, and it looks at letters and
    digits: a full stop the segmenter left out of a claim, or the model left out
    of a quote, is not a straddle. For each claim the quote touches:
      * quote inside the claim  -> the model quoted part of a vouched sentence: keep
                                   the quote, clipped to the claim (punctuation only)
      * claim inside the quote  -> the model quoted a whole vouched sentence, maybe
                                   with meta text around it: keep the claim
      * neither                 -> a partial straddle; ambiguous, refused
    Returns (kept spans, refused claim spans). Characters of the quote outside every
    claim are counted separately by _chars_outside and logged, so trimming is
    never silent.
    """
    lo, hi = span
    ql, qh = _core(content, span)
    kept: List[Tuple[int, int]] = []
    refused: List[Tuple[int, int]] = []
    for a, b in claims:
        ca, cb = _core(content, (a, b))
        if ql >= qh or ca >= cb or not (ca < qh and ql < cb):
            continue
        if ca <= ql and qh <= cb:        # quote inside claim
            kept.append((max(a, lo), min(b, hi)))
        elif ql <= ca and cb <= qh:      # claim inside quote
            kept.append((a, b))
        else:
            refused.append((a, b))
    return kept, refused


def _locate(haystack: str, needle: str, taken: List[Tuple[int, int]],
            ) -> Optional[Tuple[int, int]]:
    """Find `needle` verbatim in `haystack`, skipping regions already claimed.

    Exact match first; then a casefolded match, which forgives capitalisation
    only. _locate_tolerant adds one more step, on words; nothing forgives a
    changed letter or digit - fuzziness here would be a way for the model to
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


_SENT_END_RE = re.compile(r"[.!?]+(?=\s|$)")
_ABBREV_RE = re.compile(
    r"(?:^|\s)(?:(?:[A-Za-z]\.)+[A-Za-z]"
    r"|Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|Inc|Ltd|Co|Corp|No|Fig|vs|approx)$")


def _ends_sentence(line: str, m: "re.Match[str]") -> bool:
    """A lone full stop after an initialism ("U.S.", "e.g.") or a listed
    abbreviation ("Dr.", "Inc.", "No.") is not a boundary. Runs ("...",
    "?!") always end a sentence. Only the token before the stop is read."""
    if m.group(0) != ".":
        return True
    start = max(0, m.start() - 24)
    while start > 0 and not line[start - 1].isspace():
        start -= 1
    return not _ABBREV_RE.search(line[start:m.start()])
def _cuts_number(text: str, a: int, b: int) -> bool:
    """Does the span [a, b) start or end inside a number, as _words reads
    numbers? Inside means: a digit at the edge of the span continued on the
    outside by a digit; by a dash, a sign or a currency mark before it
    ("-5%", "$100"); by a percent sign after it; by a mark between two digits
    ("3.5", "1,000", "2020-2021", "10:30", "3/4"); or by a bracket hugging a
    token with a digit in it ("(5%)")."""
    if a >= b:
        return False
    n = len(text)
    if text[a].isdigit() and a > 0:
        left = text[a - 1]
        if left.isdigit() or left in "+$\u20ac\u00a3" or unicodedata.category(left) == "Pd":
            return True
        if _is_sep(left) and not left.isspace() and a > 1 and text[a - 2].isdigit():
            return True
    if text[b - 1].isdigit() and b < n:
        right = text[b]
        if right.isdigit() or right in "%+$\u20ac\u00a3":   # "65+", "5€"
            return True
        if _is_sep(right) and not right.isspace() and b + 1 < n and text[b + 1].isdigit():
            return True
    # "(5%)": a bracket pair hugging the token, as _words keeps it whole
    if a > 0 and b < n and text[a - 1] == "(" and text[b] == ")" \
            and any(ch.isdigit() for ch in text[a:b]) and not any(ch.isspace() for ch in text[a:b]):
        return True
    return False


def _locate_tolerant(haystack: str, needle: str, taken: List[Tuple[int, int]],
                     ) -> Optional[Tuple[int, int]]:
    """`_locate` on word boundaries, then a match on the words alone.

    Models add a full stop the source does not have, wrap a quote in quotation
    marks, drop a comma, or leave out a "(Passage 1)" tail. Every one of those
    was a dropped claim or credit at the v0.5.3 run and a false flag downstream.
    The fallback compares the sequence of words (runs of anything but
    separators, case-folded) and returns the span from the first matched word to the last.
    Punctuation, spacing, case and quote or dash variants are forgiven; a
    changed letter or digit is not, and words are never merged or split. That
    is stricter than the verifier's 2% edit tolerance on purpose: a locate
    that forgave one character would let "14" find "15".
    """
    ndl = [w for _, _, w in _words(needle)]
    if not ndl:
        return None
    hay = _words(haystack)
    span = _locate(haystack, needle, taken)
    if span is not None:
        # An exact hit that cuts a number ("5%" inside "-5%", "14" inside
        # "14.5%", "4%" inside "14%") is not the text the model was shown; it
        # is a smaller one. Letters glued to the hit are allowed: a scraped
        # page reads "87,700 resultsEtta Cone commissioned", and the quote
        # "Etta Cone commissioned" is there word for word. (The v0.7.0 rule
        # required a word boundary on both sides and refused 177 verbatim
        # quotes in one AgentHallu run for that reason.)
        blocked = list(taken)
        while span is not None:
            if not _cuts_number(haystack, *_core(haystack, span)):
                return span
            blocked.append(span)
            span = _locate(haystack, needle, blocked)
    n = len(ndl)
    for i in range(len(hay) - n + 1):
        if hay[i][2] != ndl[0]:
            continue
        if [w for _, _, w in hay[i:i + n]] != ndl:
            continue
        span = (hay[i][0], hay[i + n - 1][1])
        if not any(x < span[1] and span[0] < y for x, y in taken):
            return span
    return None


def _find(haystack: str, needle: str, taken: List[Tuple[int, int]],
          log: "ProposalLog") -> Optional[Tuple[int, int]]:
    """_locate_tolerant, counting in `log` the finds the strict locate missed."""
    span = _locate_tolerant(haystack, needle, taken)
    if span is not None and haystack[span[0]:span[1]].casefold() != needle.strip().casefold():
        log.tolerant_locates += 1   # the word path found it, not an exact hit
    return span


# Whitespace plus the characters that are invisible but are not whitespace to
# `str.strip`: byte-order mark, zero-width space/non-joiner/joiner, word joiner.
_PADDING = (" \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
            "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
            "\ufeff\u200b\u200c\u200d\u2060")


def _sentences(text: str) -> List[Tuple[int, int]]:
    """Deterministic sentence spans: a line break is a boundary, then a run of
    .!? followed by whitespace or the end of the line, except a lone full stop
    after an abbreviation ("U.S.", "Dr."). Each sentence starts where the
    previous one ended, so "3.5%" does not start one.
    Fragments under 15 characters or under two words (list numbers, rules,
    one-word headings) and markdown headings or table rows are dropped. The same boundaries bench/build.py uses,
    so a benchmark answer sentence and a coverage claim over the same text
    get the same span."""
    out = []
    for line in re.finditer(r"[^\n]+", text):
        base, s = line.start(), line.group(0)
        if s.lstrip()[:1] in ("#", "|"):
            continue  # a markdown heading or table row is not a sentence
        ends = [m.end() for m in _SENT_END_RE.finditer(s) if _ends_sentence(s, m)]
        if not ends or ends[-1] < len(s):
            ends.append(len(s))
        pos = 0
        for end in ends:
            a, b = base + pos, base + end
            pos = end
            while a < b and text[a].isspace():
                a += 1
            if b - a >= 15 and len(_words(text[a:b])) >= 2:
                out.append((a, b))
    return out


def _chars_outside(content: str, span: Tuple[int, int],
                   kept: List[Tuple[int, int]]) -> int:
    """Word characters of `span` not inside any kept span. Separators do not
    count: a live run showed the segmenter returning claims without their
    final full stop while the credit quote included it; that punctuation is
    not unvouched text."""
    n = 0
    for i in range(span[0], span[1]):
        if _is_sep(content[i]):
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
    by: Dict[Tuple[int, int], str] = {}   # span -> who proposed it
    # A proposer that replays an earlier run's file can say so (`claim_tag`)
    # when it cannot tell which of the replayed claims the model returned.
    tag = getattr(proposer, "claim_tag", None) or proposer.name
    for text in proposed:
        text = str(text)
        span = _find(art.content, text, spans, log)
        if span is None:
            reason = ("overlaps a claim already located"
                      if _locate_tolerant(art.content, text, [])
                      else "not a verbatim substring")
            log.dropped_claims.append(
                {"artifact_id": art.artifact_id, "text": text, "reason": reason})
            continue
        spans.append(span)
        by[span] = tag

    # Coverage: a sentence of an intermediate artifact the segmenter did not
    # return is posted as a claim anyway. At the v0.5.3 run the segmenter
    # skipped whole sentences of a summary (a generic closing line, the tail of
    # a list) and every answer sentence quoting them was flagged for citing
    # text nobody vouched for. A sentence a later step may quote must have an
    # account; whether it is funded is the credit step's job, not the
    # segmenter's silence. The final answer is left alone on purpose: its
    # segmenter decides what the audit checks, and a hedge it skipped ("it is
    # difficult to give an exact answer") is not a claim to fund.
    added = 0
    for a, b in (_sentences(art.content) if art.kind is ArtifactKind.INTERMEDIATE else []):
        if any(x < b and a < y for x, y in spans):
            continue
        spans.append((a, b))
        by[(a, b)] = "coverage"
        added += 1
    if added:
        log.coverage_claims += added
        log.warnings.append(
            f"{art.artifact_id}: {added} sentence(s) the segmenter did not return "
            "were posted as claims (coverage)")

    # A bare-value final answer the segmenter returned no locatable claim for
    # ("1898", "360,573,1200", "Saint Petersburg") is still the answer: it is
    # posted whole as one claim, so that an answer consisting of a number is
    # audited rather than passed for having nothing to audit. On the v0.7.2
    # AgentHallu run 18 of 224 answers had no claim, 7 of them labelled
    # hallucinations; 15 of the 18 are a bare value (4 labelled).
    #
    # "Bare value" is one line, at most 4 words and at most 24 characters. The
    # longest of those 15 is "The Cradle Will Rock." (21 characters, 4 words)
    # and the shortest answer above the bounds is 148 characters, so the corpus
    # itself does not choose between 24 and 147: the bounds are set at the
    # observed maximum plus a small margin on purpose, against a case the corpus
    # does not contain - a short sentence the segmenter skipped deliberately
    # ("Task completed successfully.", 28 characters, hypothetical here). Such a
    # sentence is not a claim to fund, and a wider cap would turn it into a false
    # alarm. Calibrated on one corpus; a stated limit, not a law.
    #
    # A one- or two-token answer that occurs anywhere in any input closes on a
    # self-evident credit, so the rule mostly buys an honest `grounded` rather
    # than a new flag; what it removes is the silent pass of an answer with
    # nothing to audit. Everything longer is left to the segmenter's judgement,
    # and the harness reports the answers that were left without a claim.
    # One strip, not three: alternating spaces and zero-width characters would
    # survive a strip(whitespace) -> strip(invisible) -> strip(whitespace) pass
    # and end up inside the claim, where no source can ever match them.
    whole = art.content.strip(_PADDING)
    if (art.kind is ArtifactKind.FINAL_ANSWER and not spans and whole
            and len(whole) <= 24 and len(_words(whole)) <= 4
            and len(whole.splitlines()) == 1):
        a = art.content.index(whole)
        spans.append((a, a + len(whole)))
        by[(a, a + len(whole))] = "whole-answer"
        log.whole_answer_claims += 1
        log.warnings.append(f"{art.artifact_id}: the segmenter returned no claim that could "
                            "be located; the short answer was posted whole as one claim")

    # Ids follow text order, not the order the model happened to answer in, so
    # `summary.c3` is always the third claim a reader meets in the summary.
    # `proposed_by` says who put the claim there: the proposer's name, or
    # `coverage` (a sentence of a model step the segmenter skipped),
    # `whole-answer` (a short answer it returned nothing locatable for), or
    # `replay` (an earlier untagged file was replayed and could not say).
    spans.sort()
    claims = [
        {"claim_id": f"{art.artifact_id}.c{i + 1}", "artifact_id": art.artifact_id,
         "start": a, "end": b, "proposed_by": by[(a, b)]}
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

    # Self-evident credits first: if the claim's own text sits word for word
    # in a source the step received, that is the strongest
    # support there is, and at the v0.5.3 run the model returned no credit at
    # all for a fifth of such claims. Posting it is a substring search, not a
    # judgement. The model's credits are still asked for and added after.
    self_evident = [
        {"artifact_id": src.artifact_id, "quote": claim_text, "_self_evident": True}
        for src in sources
        if _locate_tolerant(src.content, claim_text, []) is not None
    ]
    credits = self_evident + credits

    entries: List[Dict[str, Any]] = []
    seen_quotes: set = set()
    cid = claim["claim_id"]

    def drop(aid: str, quote: str, reason: str, verbatim: bool = False) -> None:
        rec = {"claim_id": cid, "artifact_id": aid, "quote": quote, "reason": reason}
        if verbatim:
            rec["proposed_by"] = "verbatim"
        log.dropped_credits.append(rec)

    for cr in credits:
        if not isinstance(cr, dict):
            drop("", str(cr), "credit is not an object")
            continue
        verbatim = bool(cr.get("_self_evident"))
        aid, quote = str(cr.get("artifact_id", "")), str(cr.get("quote", ""))
        art = by_id.get(aid)
        if art is None:
            drop(aid, quote, "artifact not among the step's inputs")
            continue
        span = _find(art.content, quote, [], log)
        if span is None:
            drop(aid, quote, "quote is not a verbatim substring of the artifact")
            continue

        if art.kind.is_root:
            spans = [span]
        else:
            spans, refused = _split_to_claims(art.content, span, claim_spans.get(aid, []))
            for a, b in refused:
                drop(aid, quote, "quote only partially overlaps claim "
                     f"{aid}#{a}-{b}; refused as ambiguous", verbatim)
            if not spans:
                drop(aid, quote, "quote covers no whole claim of the cited artifact",
                     verbatim)
                continue

        key = (aid, tuple(spans))
        if key in seen_quotes:
            continue  # same credit twice adds nothing to the books
        seen_quotes.add(key)
        if not art.kind.is_root:
            outside = _chars_outside(art.content, span, spans)
            if outside:
                log.warnings.append(
                    f"{cid}: {outside} characters of a quote into {aid} lie outside "
                    "any claim and were not credited")

        # One quote that covers several claims becomes several entries that share
        # a group: the ledger closes a group only if every member closes, so the
        # quote inherits the worst claim it covered - as one wide entry would.
        if cr.get("_self_evident"):
            log.self_evident_credits += 1
        group = f"{cid}.g{len(entries) + 1}" if len(spans) > 1 else ""
        for a, b in spans:
            account = f"EVIDENCE:{aid}#{a}-{b}"
            entry = {
                "entry_id": f"{cid}.e{len(entries) + 1}",
                "claim_id": cid,
                "account": account,
                "quoted_span": art.content[a:b],
                "proposed_by": "verbatim" if cr.get("_self_evident") else proposer.name,
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


def post_run(run: Run, proposer: Proposer, *, on_demand: bool = False) -> Dict[str, Any]:
    """Segment and post every derived artifact. Returns a posted-trace dict.

    With `on_demand`, credits are proposed only for claims a chain from the
    final answer reaches: the answer's claims first, then every claim under a
    span one of their credits cites, and so on. A claim nothing reaches is
    posted without entries - the audit reports it as unsupported, "no entry
    posted", which is literally true and never touches a final claim's verdict.
    On a long agent trace this is the difference between one credit call per
    claim the answer rests on and one per sentence the agent ever wrote."""
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

    def sources_of(art: Artifact) -> List[Artifact]:
        step = run.producing_step(art.artifact_id)
        return [run.artifacts[i] for i in (step.inputs if step else ())]

    if not on_demand:
        for art in ordered:
            sources = sources_of(art)
            for c in per_artifact[art.artifact_id]:
                text = art.content[c["start"]:c["end"]]
                entries.extend(
                    propose_credits(c, text, sources, claim_spans, proposer, log))
    else:
        by_id = {a.artifact_id: a for a in ordered}
        queue = [c for a in ordered if a.kind is ArtifactKind.FINAL_ANSWER
                 for c in per_artifact[a.artifact_id]]
        asked: set = set()
        while queue:
            c = queue.pop(0)
            if c["claim_id"] in asked:
                continue
            asked.add(c["claim_id"])
            art = by_id[c["artifact_id"]]
            text = art.content[c["start"]:c["end"]]
            new = propose_credits(c, text, sources_of(art), claim_spans, proposer, log)
            entries.extend(new)
            for e in new:
                acct = e["account"]
                if not acct.startswith("EVIDENCE:"):
                    continue
                aid, _, span = acct[len("EVIDENCE:"):].rpartition("#")
                if aid not in by_id:
                    continue  # a root: the chain stops there
                a, b = (int(x) for x in span.split("-"))
                queue.extend(cc for cc in per_artifact[aid]
                             if cc["start"] < b and a < cc["end"])
        log.unasked_claims = len(claims) - len(asked)
        if log.unasked_claims:
            log.warnings.append(
                f"{log.unasked_claims} claim(s) no chain from the final answer "
                "reached were posted without entries (on_demand)")

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
