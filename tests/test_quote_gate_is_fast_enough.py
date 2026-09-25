"""The quote gate stays fast on the largest input the real runs contain.

Project rule 36: time is part of what a behaviour change must re-measure. This
test exists because a correctness fix to the gate made string comparison
quadratic - `SequenceMatcher(..., autojunk=False)` - and one entry over a
20,000-character artifact went from 0.09 s to 25 s. The input that triggered it
was not adversarial: it was a quote differing from its source by one full stop,
the benign drift the gate exists to forgive.

The size is measured, not chosen: 20,000 characters is the longest artifact, and
948 the longest quoted span, in the 424 posted traces of the two published runs.

The ceiling of one second was named before the fix was measured. It is generous
against the 0.013 s the fixed code takes, on purpose: this test must fail on a
complexity regression, not on a slow machine or a busy CI runner.
"""
from __future__ import annotations

import time

from tallystick import close_books, load_run

CEILING_SECONDS = 1.0
REAL_MAX_ARTIFACT = 20_000

UNIT = ("The auditor reviewed the consolidated statements for 2023 and confirmed "
        "that revenue reached 12.4 million euro. ")


def _trace(doc: str, quote: str) -> dict:
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "ans", "kind": "final_answer", "content": "Revenue reached 12.4 million euro."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "c1", "artifact_id": "ans", "start": 0, "end": 33}],
        "entries": [{"entry_id": "e1", "claim_id": "c1",
                     "account": f"EVIDENCE:doc#0-{len(doc)}", "quoted_span": quote}],
    }


def test_one_entry_over_the_largest_real_artifact_is_under_a_second():
    doc = (UNIT * (REAL_MAX_ARTIFACT // len(UNIT) + 1))[:REAL_MAX_ARTIFACT]
    run = load_run(_trace(doc, doc + "."))      # the benign case: one added full stop
    started = time.perf_counter()
    balance = close_books(run)
    elapsed = time.perf_counter() - started
    assert balance.books_balance, "the benign drift must still close, or this times the wrong path"
    assert elapsed < CEILING_SECONDS, f"{elapsed:.2f}s over a {REAL_MAX_ARTIFACT}-char artifact"


def test_the_same_holds_when_the_quote_is_refused():
    """A refusal walks the same comparison, and a gate nobody can afford to run
    is not a gate."""
    doc = (UNIT * (REAL_MAX_ARTIFACT // len(UNIT) + 1))[:REAL_MAX_ARTIFACT]
    run = load_run(_trace(doc, doc.replace("12.4", "92.4", 1)))
    started = time.perf_counter()
    balance = close_books(run)
    elapsed = time.perf_counter() - started
    assert not balance.books_balance
    assert elapsed < CEILING_SECONDS, f"{elapsed:.2f}s over a {REAL_MAX_ARTIFACT}-char artifact"


def test_the_cost_grows_with_the_length_and_not_with_its_square():
    """Clause 3 of the ceiling named before the v0.8.2 speed fix, which that fix
    did not meet: doubling the input must not more than double the time.

    `SequenceMatcher` met clauses 1 and 2 with a 75x margin and still grew x3.5
    per doubling, so the gate was quadratic with a small constant. Reading each
    side into a sequence once is linear, and this holds it there. The bound is
    deliberately loose - eight times is what linear predicts over three
    doublings, twelve leaves room for a busy machine, and a square term would
    show up as roughly sixty.
    """
    small, large = doc_of(20_000), doc_of(160_000)
    fast = min(_time_one(small) for _ in range(5))
    slow = min(_time_one(large) for _ in range(5))
    assert slow < 12 * fast, f"8x of input took {slow / fast:.1f}x the time"


def test_a_source_with_no_space_in_it_is_under_a_second_at_the_real_size():
    """The two tests above are ASCII, and ASCII takes the view's fast path. The
    boundary check reads a stretch of the source from one word to the next,
    and a text with no whitespace - Chinese, minified JSON, base64 - is one
    word, so the stretch was the whole of it for every citation (review of
    round 7, section 7). Since round 9 the stretch stops `_REACH_PER_WORD`
    characters into a word, and the two tests below hold that at a million."""
    doc = ("洪水造成三百人死亡据报道" * (REAL_MAX_ARTIFACT // 12 + 1))[:REAL_MAX_ARTIFACT]
    start = REAL_MAX_ARTIFACT // 2
    trace = _trace(doc, doc[start:start + 12])
    trace["entries"][0]["account"] = f"EVIDENCE:doc#{start}-{start + 12}"
    run = load_run(trace)
    started = time.perf_counter()
    close_books(run)
    elapsed = time.perf_counter() - started
    assert elapsed < CEILING_SECONDS, f"{elapsed:.2f}s over a {REAL_MAX_ARTIFACT}-char text with no space"


def _one_citation(doc: str, start: int, end: int) -> float:
    trace = _trace(doc, doc[start:end])
    trace["entries"][0]["account"] = f"EVIDENCE:doc#{start}-{end}"
    run = load_run(trace)
    started = time.perf_counter()
    close_books(run)
    return time.perf_counter() - started


def test_a_page_with_an_embedded_image_is_under_a_second():
    """The review of round 8 (section 8): a downloaded page with an image
    inlined as base64 (`<img src="data:...">`, about 730 KB) and the quote
    the sentence right after it took 1.09 s for one entry. The text has
    spaces; what it also has is one word a million characters long within two
    words of the quote, and the stretch the boundary reads took all of it.
    The ceiling is the one this file names; the size is the review's."""
    image = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk" * 20_000)[:1_000_000]
    doc = (f'<html><body><h1>Annual report</h1><img src="data:image/png;base64,{image}">'
           "<p>The company\u2019s revenue reached 5.2 million in 2023.</p></body></html>")
    start, end = doc.index("The company"), doc.index(" in 2023")
    elapsed = _one_citation(doc, start, end)
    assert elapsed < CEILING_SECONDS, f"{elapsed:.2f}s with a 1,000,000-character word beside the quote"


def test_a_million_characters_with_no_space_are_under_a_second():
    """The shape of round 8's own finding: a text with no whitespace at all
    is one word, 700,000 characters of it took 1.26 s."""
    doc = ("\u6d2a\u6c34\u9020\u6210\u4e09\u767e\u4eba\u6b7b\u4ea1" * 120_000)[:1_000_000]
    start = len(doc) // 2
    elapsed = _one_citation(doc, start, start + 9)
    assert elapsed < CEILING_SECONDS, f"{elapsed:.2f}s over a 1,000,000-character text with no space"


def test_a_long_word_beside_the_boundary_changes_no_answer():
    """The stretch stops inside a long word; what the boundary asks about is
    still inside it. A number and a word cut right after a word of 5000
    characters are refused as they are without it."""
    from tallystick.verify import verify_run
    for doc, cut in ((f"{'x' * 5000} about 66 300 people were counted.", "300 people"),
                     (f"{'x' * 5000} the drug is unsafe for children.", "safe for children"),
                     (f"{'y' * 5000}unsafe for children.", "safe for children")):
        start = doc.index(cut)
        trace = _trace(doc, cut)
        trace["entries"][0]["account"] = f"EVIDENCE:doc#{start}-{start + len(cut)}"
        entry = verify_run(load_run(trace))[0]
        assert not entry.verified, (cut, entry.reason)


def doc_of(n: int) -> str:
    return (UNIT * (n // len(UNIT) + 1))[:n]


def _time_one(doc: str) -> float:
    run = load_run(_trace(doc, doc + "."))
    started = time.perf_counter()
    close_books(run)
    return time.perf_counter() - started
