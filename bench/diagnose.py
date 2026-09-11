"""Why did tallystick flag a clean sentence? No API, no model.

    python bench/diagnose.py bench/work/posted bench/work/traces

Re-audits every posted file from a run (deterministic, offline), finds each
final-answer sentence that tallystick flagged but the annotators left alone
(a false positive) and each laundered sentence it missed (a false negative),
and sorts the false positives by the reason the chain broke. The precision
problem is only worth a v0.6 if most false positives share a cause that can
be fixed without putting a judgement call back into the verdict.

Reasons for a false positive, in the order they are tested:

  quote_near_verbatim   the summary claim went prior-only because every quote the
                        proposer offered was dropped as "not a verbatim substring",
                        but one of them is found by normalize() plus the verifier's
                        2% edit tolerance (case, whitespace, quote and dash
                        variants, a one-character typo, punctuation the model wrapped
                        around the quote). A locate that forgives exactly what
                        verify.py forgives would recover these.
  quote_edited          not recoverable that way, but >= 90% of the quote's words
                        line up in order with the source: one word changed, a
                        negation, a flipped number, words elided. Look at these
                        by eye; they are not a locate problem.
  quote_partial         60-90% of the words line up: the model rewrote the source.
  quote_fabricated      under 60%: the quote is not in the source.
  no_credit_offered     the proposer returned no credits for the summary claim.
  answer_hop:<cause>    the break is at the answer step itself: every credit the
                        proposer offered for the answer claim was dropped. The
                        cause is one of the quote_* buckets above (against the
                        summary), no_credit_offered, quote_straddles_claims (the
                        quote cut across the segmenter's claim boundaries) or
                        wrong_artifact_cited (a document cited from the answer
                        step, which the conservation rule rejects).
  group_member:<cause>  the sentence is flagged through an entry group: the group
                        member that broke did so for <cause> (one of the above).
  upstream_unsupported  the summary claim the answer cites is UNSUPPORTED (its own
                        quote failed a verifier gate).
  unaccounted_span      "cited span ... not fully accounted for by any claim".
  answer_hop:<status>   an answer claim that is UNSUPPORTED or CIRCULAR itself.
  dropped_other         every offered credit was dropped for a reason not above.
  other                 anything else, with the break reason printed.

Match quality is the share of the dropped quote's words that line up, in
order, with the source inside a window anchored on the longest common run of
words. It is a diagnostic, not a verifier rule.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tallystick import audit, load_run  # noqa: E402
from tallystick.ledger import FAILING, ClaimStatus  # noqa: E402
from tallystick.normalize import levenshtein, normalize  # noqa: E402
from tallystick.verify import SPAN_TOLERANCE  # noqa: E402


_WORD = re.compile(r"[^\W_]+")


def matched_words(quote: str, source: str) -> Tuple[int, int]:
    """(words of the quote that line up in order with the source, words in the
    quote). Anchored on the longest common run of words, then every in-order
    matching word within a window of the source around that anchor is counted.
    One changed word costs one word, a stray comma costs nothing, a paraphrase
    or an invention scores low. A diagnostic, not a verifier rule."""
    q, s = _WORD.findall(normalize(quote)), _WORD.findall(normalize(source))
    if not q:
        return 0, 0
    m = difflib.SequenceMatcher(None, s, q, autojunk=False).find_longest_match(0, len(s), 0, len(q))
    if m.size == 0:
        return 0, len(q)
    lo = max(0, m.a - m.b - len(q) // 2)
    hi = min(len(s), m.a + (len(q) - m.b) + len(q) // 2)
    blocks = difflib.SequenceMatcher(None, s[lo:hi], q, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks), len(q)


def match_quality(quote: str, source: str) -> float:
    hit, n = matched_words(quote, source)
    return hit / n if n else 0.0


def recoverable(quote: str, source: str) -> bool:
    """Would a locate that forgives exactly what verify.py forgives find it?
    normalize() (case, whitespace, quote and dash variants) plus SPAN_TOLERANCE
    edits against the best-aligned substring. Nothing semantic."""
    # The pipeline strips whitespace only; a quote wrapped in punctuation the
    # model added (quotation marks, a trailing period) is the same locate fix.
    q, s = normalize(quote).strip(" \"'.,;:()[]"), normalize(source)
    if not q:
        return False
    if q in s:
        return True
    cap = int(len(q) * SPAN_TOLERANCE)
    if not cap:
        return False
    m = difflib.SequenceMatcher(None, s, q, autojunk=False).find_longest_match(0, len(s), 0, len(q))
    if m.size == 0:
        return False
    start = m.a - m.b
    for d in range(-cap, cap + 1):
        for e in range(-cap, cap + 1):
            a, b = start + d, start + d + len(q) + e
            if 0 <= a < b <= len(s) and levenshtein(s[a:b], q, cap) <= cap:
                return True
    return False


def drop_bucket(posted: Dict[str, Any], claim_id: str,
                artifacts: Dict[str, str]) -> Tuple[str, str]:
    """Why every credit the proposer offered for `claim_id` was dropped, as a
    bucket name and a detail string. Only meaningful for a PRIOR_ONLY claim:
    there, everything offered is in dropped_credits."""
    dropped = posted.get("_proposal", {}).get("dropped_credits", [])
    offered = [d for d in dropped if d["claim_id"] == claim_id]
    if not offered:
        return "no_credit_offered", "proposer returned no credits"
    best, rec, one_word = None, False, False
    for d in offered:
        if "verbatim" in d["reason"]:
            src = artifacts.get(d["artifact_id"], "")
            hit, n = matched_words(d["quote"], src)
            q = hit / n if n else 0.0
            best = q if best is None else max(best, q)
            rec = rec or recoverable(d["quote"], src)
            # A short quote with one word off is still "one word changed"; the
            # ratio alone would call a 7-word quote with one edit a rewrite.
            one_word = one_word or (n >= 5 and n - hit <= 1)
    if best is not None:
        bucket = ("quote_near_verbatim" if rec else
                  "quote_edited" if (best >= 0.9 or one_word) else
                  "quote_partial" if best >= 0.6 else "quote_fabricated")
        return bucket, f"words {best:.2f}, recoverable {rec}"
    raw = [d["reason"] for d in offered]
    reasons = Counter(r.split(" claim ")[0] for r in raw)
    detail = "; ".join(f"{r} x{n}" for r, n in reasons.items())
    if any("partially overlaps" in r or "no whole claim" in r for r in raw):
        return "quote_straddles_claims", detail
    if any("not among the step" in r for r in raw):
        return "wrong_artifact_cited", detail
    return "dropped_other", detail


def classify(posted: Dict[str, Any], balance, claim_id: str) -> Tuple[str, str]:
    a = balance.audits[claim_id]
    artifacts = {x["artifact_id"]: x["content"] for x in posted["artifacts"]}
    reason = a.break_reason or ""
    if "not fully accounted" in reason:
        return "unaccounted_span", reason
    if a.break_claim_id == claim_id:
        if a.status is ClaimStatus.PRIOR_ONLY:
            bucket, detail = drop_bucket(posted, claim_id, artifacts)
            return f"answer_hop:{bucket}", detail or reason
        return f"answer_hop:{a.status.value}", reason
    # The entry that carried the verdict: the first hop's account names it, and
    # its group flag says whether the sentence is flagged through a group.
    hop_account = a.chain[0].account if a.chain else None
    matching = [e for e in posted["entries"]
                if e["claim_id"] == claim_id and e["account"] == hop_account]
    grouped = bool(matching) and all(e.get("group") for e in matching)
    broke = a.break_claim_id
    if broke and a.status is ClaimStatus.LAUNDERED:
        b = balance.audits.get(broke)
        if b is not None and b.status is ClaimStatus.PRIOR_ONLY:
            bucket, detail = drop_bucket(posted, broke, artifacts)
            return (f"group_member:{bucket}" if grouped else bucket), detail or reason
        if b is not None and b.status is ClaimStatus.UNSUPPORTED:
            return "upstream_unsupported", b.break_reason or ""
    return "other", f"{a.status.value}: {reason}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("posted", help="directory of posted files from bench/run.py")
    ap.add_argument("traces", help="directory of built traces (for _truth)")
    ap.add_argument("--examples", type=int, default=3, help="examples to print per bucket")
    args = ap.parse_args(argv)

    posted_dir, traces_dir = Path(args.posted), Path(args.traces)
    if not posted_dir.is_dir():
        print(f"not a directory: {posted_dir}", file=sys.stderr)
        return 2
    fp_buckets: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    fn_buckets: Counter = Counter()
    n_files = n_sent = n_fp = n_fn = n_tp = n_skipped = 0
    for path in sorted(posted_dir.glob("*.json")):
        posted = json.loads(path.read_text(encoding="utf-8"))
        truth = posted.get("_truth")
        if truth is None:
            tpath = traces_dir / re.sub(r"\.run\d+(?=\.json$)", "", path.name)
            if not tpath.exists():
                n_skipped += 1
                continue
            truth = json.loads(tpath.read_text(encoding="utf-8"))["_truth"]
        n_files += 1
        balance = audit(load_run(posted))
        claims = [c for c in posted["claims"] if c["artifact_id"] == "answer"]
        for s in truth["answer_sentences"]:
            n_sent += 1
            overlapping = [c for c in claims if c["start"] < s["end"] and s["start"] < c["end"]]
            failing = [c for c in overlapping if balance.audits[c["claim_id"]].status in FAILING]
            flagged = bool(failing)
            if flagged and not s["laundered"]:
                n_fp += 1
                bucket, detail = classify(posted, balance, failing[0]["claim_id"])
                fp_buckets[bucket].append((path.name, s["text"][:90], detail))
            elif flagged and s["laundered"]:
                n_tp += 1
            elif not flagged and s["laundered"]:
                n_fn += 1
                if not overlapping:
                    fn_buckets["no_claim_over_sentence"] += 1
                else:
                    st = balance.audits[overlapping[0]["claim_id"]]
                    fn_buckets[f"closed_as_{st.status.value}_depth{st.depth}"] += 1

    print(f"{n_files} posted files, {n_sent} answer sentences: "
          f"{n_tp} true flags, {n_fp} false flags, {n_fn} misses"
          + (f"  ({n_skipped} file(s) skipped: no _truth and no trace)" if n_skipped else ""))
    print()
    print("False flags by cause (answer_hop: broke at the answer's own citation; "
          "group_member: broke in a neighbouring clause the same quote covered):")
    for bucket, items in sorted(fp_buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {bucket:<36} {len(items):>4}  ({len(items) / max(1, n_fp):.0%})")
    print()
    print("Misses by how the sentence closed:")
    for k, v in fn_buckets.most_common():
        print(f"  {k:<36} {v:>4}")
    print()
    for bucket, items in sorted(fp_buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"--- {bucket}: examples")
        for name, text, detail in items[: args.examples]:
            print(f"  [{name}] {text!r}  -> {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
