"""Build two-hop laundering traces from RAGTruth. No model involved.

Why construct
-------------
Every hallucination dataset is one hop: context -> response, with the invented
spans annotated. tallystick's claim is about what happens *after* that hop. So we
take a RAGTruth item and make it the middle of a chain:

    documents  --(summarize)-->  RAGTruth response  --(answer)-->  final answer

The RAGTruth response becomes an intermediate artifact; its human-annotated
hallucinated spans are ground truth for "invented at this step". The final answer
is built deterministically: a few sentences of the response, quoted verbatim.
Ground truth for the answer follows without any judgement of ours:

    quoted sentence overlaps an annotated span  ->  LAUNDERED
    quoted sentence overlaps none               ->  GROUNDED

Nothing is tuned. The annotators decided what was invented; we only carried it one
step further, which is exactly what a real pipeline does when it answers from its
own summary.

Three things to know before trusting a number from this:

* The final hop is trivially verifiable - the answer quotes the summary verbatim.
  So tallystick's answer-level result is the summary-step detection carried one
  step, not new detection power. What the construction shows is WHERE a one-hop
  check breaks: a judge shown only summary + answer cannot see laundering at all,
  by construction. That row is the point, and it is scored anyway.
* Prevalence is enriched. Items with at least one annotated hallucination are
  oversampled (60% of items); within an item, up to three sentences are chosen
  uniformly at random, not "the bad ones first", so a labelled item can still
  yield no laundered sentence. The natural and constructed sentence-level rates
  are both printed in the report so precision and FPR can be read in context.
* RAGTruth annotates hallucinations, so an unannotated sentence is treated as
  supported. That is the dataset's own contract. Data2txt (a third of the test
  split, and the densest in hallucinations) is excluded because its sources are
  structured records, not prose a model could quote verbatim.

Usage
-----
    python bench/build.py --ragtruth /path/to/RAGTruth --limit 100 --out bench/work
    # or let it clone: python bench/build.py --limit 100
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

RAGTRUTH_REPO = "https://github.com/ParticleMedia/RAGTruth"
_SENT_END = re.compile(r"[.!?]+(?=\s|$)")
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
_LINE = re.compile(r"[^\n]+")


def sentences(text: str) -> List[Tuple[int, int]]:
    """Sentence spans by a plain regex. Deterministic and dependency-free.

    A line break is a boundary too: RAGTruth responses are full of numbered
    lists and headings without terminal punctuation ("(Passage 3)"), and v0.5.2
    glued such a line to the sentence after it. Fragments under 15 characters
    are dropped. Measured at v0.5.3 (before the abbreviation guard) on the
    eligible test pool that left ~4% of response text and ~2% of annotated
    spans (10 of 479) outside every sentence; they can never be quoted, never
    scored, and are excluded from the natural prevalence too, so the two
    sides stay consistent."""
    out = []
    for line in _LINE.finditer(text):
        base, ln = line.start(), line.group(0)
        # Each sentence starts where the previous one ended: a boundary is a
        # run of .!? followed by whitespace, not after an abbreviation, so
        # "3.5%" and "U.S." do not start a sentence (v0.5.3's regex let a
        # match begin after any full stop). tallystick/propose/pipeline.py
        # uses the same boundaries for coverage claims; a test keeps them equal.
        ends = [m.end() for m in _SENT_END.finditer(ln) if _ends_sentence(ln, m)]
        if not ends or ends[-1] < len(ln):
            ends.append(len(ln))
        pos = 0
        for end in ends:
            s, e = base + pos, base + end
            pos = end
            while s < e and text[s].isspace():
                s += 1
            if e - s >= 15:
                out.append((s, e))
    return out


def overlaps(a: Tuple[int, int], spans: List[Tuple[int, int]]) -> bool:
    return any(a[0] < e and s < a[1] for s, e in spans)


def load_ragtruth(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    ds = root / "dataset"
    if not ds.exists():
        print(f"cloning RAGTruth into {root} ...")
        subprocess.run(["git", "clone", "-q", "--depth", "1", RAGTRUTH_REPO, str(root)],
                       check=True)
    resp = [json.loads(l) for l in (ds / "response.jsonl").open(encoding="utf-8")]
    src = {json.loads(l)["source_id"]: json.loads(l)
           for l in (ds / "source_info.jsonl").open(encoding="utf-8")}
    return resp, src


def root_documents(source: Dict[str, Any]) -> List[Dict[str, str]]:
    """The documents the summariser saw, as root artifacts."""
    info = source["source_info"]
    if source["task_type"] == "Summary":
        return [{"artifact_id": "doc_1", "kind": "document", "title": "source",
                 "content": info}]
    if source["task_type"] == "QA":
        # The responder saw the question as well as the passages; a sentence
        # that restates it has nothing else to quote. v0.5.2 omitted it.
        docs = [{"artifact_id": "question", "kind": "document", "title": "question",
                 "content": info["question"].strip()}]
        for i, chunk in enumerate(re.split(r"\n\n(?=passage \d+:)", info["passages"]), 1):
            chunk = re.sub(r"^passage \d+:", "", chunk).strip()
            if chunk:
                docs.append({"artifact_id": f"doc_{i}", "kind": "document",
                             "title": f"passage {i}", "content": chunk})
        return docs
    raise ValueError(source["task_type"])


def build_trace(item: Dict[str, Any], source: Dict[str, Any], seed: int,
                quote_n: int = 3) -> Dict[str, Any] | None:
    response = item["response"]
    labels = [(l["start"], l["end"]) for l in item["labels"]]
    sents = sentences(response)
    if len(sents) < 2:
        return None

    # Quote up to quote_n sentences chosen uniformly at random, in text order.
    # The rng is seeded per item, so a trace is identical whatever --limit built
    # it, and no sentence is preferred for being hallucinated.
    rng = random.Random(f"{seed}:{item['id']}")
    chosen = sorted(rng.sample(sents, min(quote_n, len(sents))))

    answer_parts, truth = [], []
    cursor = 0
    for s, e in chosen:
        text = response[s:e].strip()
        answer_parts.append(text)
        truth.append({"start": cursor, "end": cursor + len(text), "text": text,
                      "laundered": overlaps((s, e), labels)})
        cursor += len(text) + 1
    answer = " ".join(answer_parts)

    docs = root_documents(source)
    return {
        "_meta": {
            "ragtruth_id": item["id"], "source_id": item["source_id"],
            "task_type": source["task_type"], "model": item["model"],
            "n_labels": len(labels),
        },
        "artifacts": docs + [
            {"artifact_id": "summary", "kind": "intermediate",
             "title": f"RAGTruth response by {item['model']}", "content": response},
            {"artifact_id": "answer", "kind": "final_answer",
             "title": "constructed: verbatim quotes of the summary", "content": answer},
        ],
        "steps": [
            {"step_id": "s1", "kind": "retrieve", "inputs": [],
             "outputs": [d["artifact_id"] for d in docs]},
            {"step_id": "s2", "kind": "summarize",
             "inputs": [d["artifact_id"] for d in docs], "outputs": ["summary"]},
            {"step_id": "s3", "kind": "answer", "inputs": ["summary"],
             "outputs": ["answer"]},
        ],
        "_truth": {
            "summary_hallucinated_spans": [{"start": s, "end": e} for s, e in labels],
            "answer_sentences": truth,
        },
    }


def natural_rate(resp, src) -> float:
    """Share of sentences in the eligible test pool that overlap an annotation:
    the prevalence a benchmark without enrichment would see."""
    n = bad = 0
    for r in resp:
        if r["split"] != "test" or src[r["source_id"]]["task_type"] not in ("Summary", "QA"):
            continue
        labels = [(l["start"], l["end"]) for l in r["labels"]]
        for sp in sentences(r["response"]):
            n += 1
            bad += overlaps(sp, labels)
    return bad / n if n else 0.0


def select(resp, src, limit: int, seed: int, with_labels_share: float = 0.6):
    """Test split, Summary + QA, stratified: 60% of items carry at least one
    annotated hallucination, 40% carry none. Shuffled once by seed; the order is
    the run order, so `--limit` on either side takes a random prefix, not an
    alphabetical one."""
    rng = random.Random(seed)
    pool = [r for r in resp if r["split"] == "test"
            and src[r["source_id"]]["task_type"] in ("Summary", "QA")]
    with_l = [r for r in pool if r["labels"]]
    without = [r for r in pool if not r["labels"]]
    rng.shuffle(with_l)
    rng.shuffle(without)
    n_with = round(limit * with_labels_share)
    chosen = with_l[:n_with] + without[:limit - n_with]
    rng.shuffle(chosen)
    return chosen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ragtruth", default="bench/work/RAGTruth")
    ap.add_argument("--out", default="bench/work")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    resp, src = load_ragtruth(Path(args.ragtruth))
    chosen = select(resp, src, args.limit, args.seed)
    out = Path(args.out) / "traces"
    out.mkdir(parents=True, exist_ok=True)
    order, n_sent, n_bad = [], 0, 0
    for item in chosen:
        trace = build_trace(item, src[item["source_id"]], args.seed)
        if trace is None:
            continue
        (out / f"{item['id']}.json").write_text(
            json.dumps(trace, indent=1, ensure_ascii=False), encoding="utf-8")
        order.append(f"{item['id']}.json")
        for t in trace["_truth"]["answer_sentences"]:
            n_sent += 1
            n_bad += t["laundered"]
    manifest = {
        "seed": args.seed, "order": order,
        "splitter": 2,  # 1: v0.5.3 regex; 2: anchored, abbreviation guard (v0.6)
        "natural_sentence_prevalence": natural_rate(resp, src),
        "constructed_sentence_prevalence": n_bad / n_sent if n_sent else 0.0,
        "task_types": ["Summary", "QA"], "excluded": ["Data2txt"],
    }
    (Path(args.out) / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"built {len(order)} two-hop traces in {out} (seed {args.seed}); "
          f"laundered sentences {n_bad}/{n_sent} = "
          f"{manifest['constructed_sentence_prevalence']:.1%} "
          f"(natural {manifest['natural_sentence_prevalence']:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
