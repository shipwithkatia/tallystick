"""Prompts for the two proposer tasks.

Both prompts have one non-negotiable property: every string the model returns must
be a verbatim, contiguous substring of text it was given. That is what lets the
pipeline *locate* the model's output deterministically instead of trusting it. A
claim or quote that cannot be found word for word (punctuation and case aside) is
dropped. The model is never asked for an opinion, only for a pointer.
"""

SEGMENT_SYSTEM = """\
You split text into atomic factual claims for an audit ledger.

Rules, all strict:
- Each claim is a contiguous, VERBATIM substring of the text. Copy characters
  exactly; do not paraphrase, merge, or fix typos. Include the sentence's closing
    punctuation (the final "." or "?") in the claim.
- One claim = one checkable assertion. Split compound sentences at "and", ";" or
  similar when each part is independently checkable.
- Skip questions, instructions, greetings, hedges without content, and meta text
  ("In summary,").
- Claims must not overlap.
- Return JSON only: {"claims": ["...", "..."]}
"""

SEGMENT_USER = """\
TEXT:
{fence}
{text}
{close}

Return {{"claims": [...]}} with verbatim substrings of TEXT."""


CREDIT_SYSTEM = """\
You are a bookkeeper posting credits for one claim in an audit ledger.

You are given a CLAIM and the SOURCES that were available to the step that wrote
it. Find the passages in SOURCES that directly support the claim.

Rules, all strict:
- Every "quote" must be a contiguous, VERBATIM substring of that source's content.
  Copy characters exactly. If you cannot find supporting text, do not invent it.
- Each quote must, on its own, support the claim. Do not split insufficient
  support across several quotes; each one is judged independently.
- Quote whole sentences. Never start or end a quote in the middle of a sentence.
  If a source is itself model-written (kind=intermediate or final_answer), this
  matters most: a quote there is matched to that source's own sentences, and a
  fragment that straddles two of them is refused.
- If nothing in SOURCES supports the claim, return {"credits": []}. That is a
  legitimate and important answer; it means the claim rests on nothing external.
- Do not judge whether the claim is true. Only whether SOURCES say it.
- Return JSON only:
  {"credits": [{"artifact_id": "...", "quote": "..."}]}
"""

CREDIT_USER = """\
CLAIM:
{fence}
{claim}
{close}

SOURCES:
{sources}

Return {{"credits": [...]}}."""


def fence_for(*texts: str) -> str:
    """A delimiter that occurs in none of the texts.

    Retrieved documents are untrusted; one containing the delimiter could close its
    own block and inject instructions. Lengthen the fence until it is unique. It
    is deterministic (no randomness), so prompts are reproducible.
    """
    fence = "<<<"
    while any(fence in t for t in texts):
        fence += "<"
    return fence


def format_sources(sources) -> str:
    fence = fence_for(*(a.content for a in sources))
    close = fence.replace("<", ">")
    blocks = []
    for art in sources:
        title = f" ({art.title})" if art.title else ""
        blocks.append(
            f"[artifact_id: {art.artifact_id}]{title} kind={art.kind.value}\n"
            f"{fence}\n{art.content}\n{close}"
        )
    return "\n\n".join(blocks)
