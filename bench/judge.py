"""The baselines: two LLM judges.

ONE-HOP judge - what the industry does today. It sees only the immediate context
(the summary) and the answer, and is asked whether the answer is supported by
its context. On this construction it scores ~0 recall on laundered sentences BY
CONSTRUCTION: the answer quotes the summary verbatim, so it is perfectly
faithful to its context. That row is not a competition; it is the reason the
benchmark exists, and it is scored so the reader sees it rather than takes it on
trust.

FULL-HISTORY judge - the fair opponent. Same model, same information tallystick
gets (every document, the summary, the numbered answer), asked which answer
sentences the documents do not support. The strongest thing a team can do today
without a provenance tool. Its instruction is strict on purpose ("repeating an
invented summary sentence is still unsupported"), which raises its recall and
costs it precision; that is disclosed, not hidden.

Neither judge is deterministic. The benchmark reruns them and reports the spread.
A judge call that fails for any reason - unparseable output, a rate limit, a
network error - is retried once, then recorded as a failure; a proposer failure
is handled the same way in run.py. No failure on either side stops the run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tallystick.propose.base import Proposer, parse_json

ONEHOP_SYSTEM = """\
You are checking an AI answer for faithfulness to its context. You are given the
CONTEXT the model was answering from and its FINAL ANSWER, split into numbered
sentences. For each sentence, decide whether the CONTEXT supports it.

Return JSON only: {"unsupported": [<sentence numbers>]}
"""

ONEHOP_USER = """\
CONTEXT:
{summary}

FINAL ANSWER, numbered:
{numbered}

Return {{"unsupported": [...]}} with the numbers of sentences the CONTEXT does not support."""


JUDGE_SYSTEM = """\
You are auditing an AI pipeline. You are given the DOCUMENTS the pipeline
retrieved, the SUMMARY a model wrote from them, and the FINAL ANSWER a model wrote
from the summary. The final answer is split into numbered sentences.

For each numbered sentence, decide whether it is supported by the DOCUMENTS. A
sentence that faithfully repeats the SUMMARY is still unsupported if the summary
itself invented it. Be strict: supported means the documents state it.

Return JSON only: {"unsupported": [<sentence numbers>]}
"""

JUDGE_USER = """\
DOCUMENTS:
{docs}

SUMMARY:
{summary}

FINAL ANSWER, numbered:
{numbered}

Return {{"unsupported": [...]}} with the numbers of sentences the DOCUMENTS do not support."""


class JudgeFailed(Exception):
    """The judge returned nothing parseable twice."""


def _ask(proposer: Proposer, system: str, user: str, n_sents: int) -> List[int]:
    last: Optional[BaseException] = None
    for _ in range(2):
        try:
            raw = proposer.complete(system, user)
            out = parse_json(raw).get("unsupported")
        except Exception as exc:  # noqa: BLE001 - API errors and bad JSON alike
            last = exc
            continue
        if not isinstance(out, list):
            # A JSON object without a list under "unsupported" is not an answer
            # of "nothing"; scoring it as one would deflate the judge's recall.
            last = ValueError(f"no 'unsupported' list in judge reply: {raw[:80]!r}")
            continue
        picks = []
        for x in out:
            try:
                n = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= n_sents:
                picks.append(n - 1)
        return sorted(set(picks))
    raise JudgeFailed(str(last))


def _numbered(trace: Dict[str, Any]) -> str:
    sents = trace["_truth"]["answer_sentences"]
    return "\n".join(f"{i + 1}. {s['text']}" for i, s in enumerate(sents))


def judge_full_history(trace: Dict[str, Any], proposer: Proposer) -> List[int]:
    docs = "\n\n".join(f"[{a['artifact_id']}]\n{a['content']}"
                       for a in trace["artifacts"] if a["kind"] == "document")
    summary = next(a["content"] for a in trace["artifacts"] if a["artifact_id"] == "summary")
    n = len(trace["_truth"]["answer_sentences"])
    return _ask(proposer, JUDGE_SYSTEM,
                JUDGE_USER.format(docs=docs, summary=summary, numbered=_numbered(trace)), n)


def judge_one_hop(trace: Dict[str, Any], proposer: Proposer) -> List[int]:
    summary = next(a["content"] for a in trace["artifacts"] if a["artifact_id"] == "summary")
    n = len(trace["_truth"]["answer_sentences"])
    return _ask(proposer, ONEHOP_SYSTEM,
                ONEHOP_USER.format(summary=summary, numbered=_numbered(trace)), n)
