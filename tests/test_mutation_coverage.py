"""Tests for places where a deliberate mistake in the code (a mutant) went
unnoticed by every other test: a lowered depth limit, claims walked in file
order, a slice that truncates a quote, a dropped self-citation gate, and more.

Each test PASSES on 23908ba and FAILS on the named mutant. They are not defect
tests: they pass here by design. Run against a mutated copy, from outside the
repository so the copy is what gets imported:
    PYTHONPATH=<copy> python -m pytest -p no:cacheprovider <copy>/tests/test_mutation_coverage.py
"""
import json

import pytest

import tallystick
from tallystick import TraceError, close_books, load_run
from tallystick.ledger import MAX_DEPTH, ClaimStatus


def _chain(n):
    """doc -> i1 -> i2 -> ... -> i_n -> final. Every hop verbatim, every claim covers
    its whole artifact. An honest chain n+1 hops long."""
    text = "The filing reports revenue of 14 million."
    arts = [{"artifact_id": "doc", "kind": "document", "content": text}]
    steps, claims, entries = [], [], []
    prev = "doc"
    for k in range(1, n + 2):
        aid = f"i{k}" if k <= n else "ans"
        kind = "intermediate" if k <= n else "final_answer"
        arts.append({"artifact_id": aid, "kind": kind, "content": text})
        steps.append({"step_id": f"s{k}", "kind": "summarize", "inputs": [prev], "outputs": [aid]})
        claims.append({"claim_id": f"c_{aid}", "artifact_id": aid, "start": 0, "end": len(text)})
        entries.append({"entry_id": f"e_{aid}", "claim_id": f"c_{aid}",
                        "account": f"EVIDENCE:{prev}#0-{len(text)}", "quoted_span": text})
        prev = aid
    return {"artifacts": arts, "steps": steps, "claims": claims, "entries": entries}


def test_an_honest_chain_just_past_max_depth_does_not_close():
    # guards L11 (MAX_DEPTH lowered) and L12 (depth limit removed)
    short = close_books(load_run(_chain(MAX_DEPTH - 1)))
    assert short.audits["c_ans"].status is ClaimStatus.GROUNDED
    deep = close_books(load_run(_chain(MAX_DEPTH + 1)))
    assert not deep.books_balance
    assert deep.audits["c_ans"].break_reason == f"chain deeper than {MAX_DEPTH}"


def test_an_honest_chain_tens_of_hops_deep_closes():
    # guards L11 (MAX_DEPTH lowered to 4). ledger.py: "Real runs are tens of hops deep".
    b = close_books(load_run(_chain(50)))
    assert b.books_balance


def test_a_deep_chain_verdict_does_not_depend_on_claim_array_order():
    # guards L18 (claims resolved in file order, not sorted id order). Past
    # MAX_DEPTH memoisation makes the resolution order visible in the verdict;
    # the existing shuffle test uses a 2-hop example where it never is.
    d = _chain(MAX_DEPTH + 1)
    rev = json.loads(json.dumps(d))
    rev["claims"].reverse()
    assert close_books(load_run(d)).books_balance == close_books(load_run(rev)).books_balance


def _one_hop(account_end=None, quote=None, content="Revenue was 14 million in 2023.", claim_extra=None):
    end = len(content) if account_end is None else account_end
    d = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": content},
                      {"artifact_id": "ans", "kind": "final_answer", "content": "Revenue was 14 million."}],
        "steps": [{"step_id": "s1", "kind": "answer", "inputs": ["doc"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "a1", "artifact_id": "ans", "start": 0, "end": 23}],
        "entries": [{"entry_id": "e1", "claim_id": "a1", "account": f"EVIDENCE:doc#0-{end}",
                     "quoted_span": content if quote is None else quote}],
    }
    if claim_extra:
        d["claims"][0].update(claim_extra)
    return d


def test_a_span_ending_one_past_the_artifact_is_out_of_range():
    # guards V04 (end <= len+1): the slice silently truncates, the quote still matches
    content = "Revenue was 14 million in 2023."
    b = close_books(load_run(_one_hop(account_end=len(content) + 1)))
    assert b.audits["a1"].status is ClaimStatus.UNSUPPORTED
    assert load_run(_one_hop(account_end=len(content) + 1)).entries[0].verified is None
    run = load_run(_one_hop(account_end=len(content) + 1))
    close_books(run)
    assert run.entries[0].reason == "span_out_of_range"


def test_an_evidence_account_with_trailing_text_is_malformed():
    # guards T09 (fullmatch -> match)
    d = _one_hop()
    d["entries"][0]["account"] = "EVIDENCE:doc#0-31garbage"
    with pytest.raises(TraceError):
        load_run(d)


def test_a_boolean_span_bound_is_refused():
    # guards I03 (bool accepted as int)
    with pytest.raises(TraceError):
        load_run(_one_hop(claim_extra={"start": False, "end": 23}))


def test_a_negative_claim_start_is_refused():
    # guards T03 (0 <= start dropped); text omitted, so the loader fills it from the slice
    d = _one_hop()
    d["claims"][0].update({"start": -5, "end": 23})
    with pytest.raises(TraceError):
        load_run(d)


def test_a_hand_built_run_with_a_misfiled_claim_is_refused():
    # guards T08 (claims dict key != claim_id)
    # (entries removed: an entry naming "a1" would trip the unknown-claim check
    # first, which is how the first version of this test survived the mutant)
    run = load_run(_one_hop())
    run.entries.clear()
    c = run.claims.pop("a1")
    run.claims["other"] = c
    with pytest.raises(TraceError):
        close_books(run)


def test_a_changed_digit_is_rejected_however_long_the_quote_is():
    # guards V12, rewritten with the edit budget it used to guard. That budget was
    # 2% of the span's length, so this 51-character quote got one free edit and
    # "14 million" cited as "15 million" passed - which this test asserted. The
    # budget is gone: content must survive whole, and length buys nothing.
    content = "The company reported revenue of 14 million in 2023."  # 51 chars
    long_content = content + " " + ("Every figure was reviewed by the auditor. " * 40)
    for text in (content, long_content):
        for wrong in (text.replace("14", "15", 1), text.replace("14", "45", 1)):
            run = load_run(_one_hop(quote=wrong, content=text))
            close_books(run)
            assert run.entries[0].verified is False, wrong[:60]
            assert run.entries[0].reason == "span_mismatch"


def test_whitespace_between_two_claims_does_not_break_a_citation():
    # guards L05 (whitespace must be covered): "A.  B." cited whole, claims own A. and B.
    summ = "Revenue was 14 million.   Profit was 2 million."
    doc = summ
    ans = "Revenue was 14 million.   Profit was 2 million."
    d = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "sum", "kind": "intermediate", "content": summ},
                      {"artifact_id": "ans", "kind": "final_answer", "content": ans}],
        "steps": [{"step_id": "s1", "kind": "summarize", "inputs": ["doc"], "outputs": ["sum"]},
                  {"step_id": "s2", "kind": "answer", "inputs": ["sum"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "s_a", "artifact_id": "sum", "start": 0, "end": 23},
                   {"claim_id": "s_b", "artifact_id": "sum", "start": 26, "end": len(summ)},
                   {"claim_id": "a", "artifact_id": "ans", "start": 0, "end": len(ans)}],
        "entries": [{"entry_id": "e1", "claim_id": "s_a", "account": "EVIDENCE:doc#0-23",
                     "quoted_span": doc[0:23]},
                    {"entry_id": "e2", "claim_id": "s_b", "account": f"EVIDENCE:doc#26-{len(doc)}",
                     "quoted_span": doc[26:]},
                    {"entry_id": "e3", "claim_id": "a", "account": f"EVIDENCE:sum#0-{len(summ)}",
                     "quoted_span": summ}],
    }
    b = close_books(load_run(d))
    assert b.audits["a"].status is ClaimStatus.GROUNDED


def test_a_citation_covering_a_claim_and_unclaimed_text_does_not_fund():
    # guards L04 (_span_is_accounted_for dropped). The existing ledger test removes
    # the only covering claim, so it goes through `not upstream` and never reaches
    # the accounting check.
    summ = "Revenue was 14 million. Profit doubled overnight."
    doc = "Revenue was 14 million."
    d = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": doc},
                      {"artifact_id": "sum", "kind": "intermediate", "content": summ},
                      {"artifact_id": "ans", "kind": "final_answer", "content": summ}],
        "steps": [{"step_id": "s1", "kind": "summarize", "inputs": ["doc"], "outputs": ["sum"]},
                  {"step_id": "s2", "kind": "answer", "inputs": ["sum"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "s_a", "artifact_id": "sum", "start": 0, "end": 23},
                   {"claim_id": "a", "artifact_id": "ans", "start": 0, "end": len(summ)}],
        "entries": [{"entry_id": "e1", "claim_id": "s_a", "account": "EVIDENCE:doc#0-23",
                     "quoted_span": doc},
                    {"entry_id": "e2", "claim_id": "a", "account": f"EVIDENCE:sum#0-{len(summ)}",
                     "quoted_span": summ}],
    }
    b = close_books(load_run(d))
    assert b.audits["a"].status is ClaimStatus.LAUNDERED
    assert not b.books_balance


def test_a_claim_located_in_a_document_is_grounded_in_the_json(tmp_path):
    # guards L01 (root-artifact claims no longer grounded). No verdict or exit code
    # changes - a citation of a root never resolves the root's claims - only the
    # per-claim status in `audit --json` does.
    from tallystick.cli import main
    d = _one_hop()
    d["claims"].append({"claim_id": "d1", "artifact_id": "doc", "start": 0, "end": 10})
    p, out = tmp_path / "t.json", tmp_path / "o.json"
    p.write_text(json.dumps(d))
    assert main([str(p), "--quiet", "--json", str(out)]) == 0
    status = {c["claim_id"]: c["status"] for c in json.loads(out.read_text())["claims"]}
    assert status["d1"] == "grounded"


def _cycle(final_id="z", x_id="a_x", y_id="b_y"):
    """X and Y cite each other; X also cites a document. Y is funded through X,
    so an answer quoting Y is grounded. Y resolved while X's walk is open looks
    unfunded - the value that must never be memoised."""
    t = "Revenue was 14 million."
    return {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": t},
                      {"artifact_id": "X", "kind": "intermediate", "content": t},
                      {"artifact_id": "Y", "kind": "intermediate", "content": t},
                      {"artifact_id": "ans", "kind": "final_answer", "content": t}],
        "steps": [{"step_id": "sX", "kind": "summarize", "inputs": ["Y", "doc"], "outputs": ["X"]},
                  {"step_id": "sY", "kind": "summarize", "inputs": ["X"], "outputs": ["Y"]},
                  {"step_id": "sA", "kind": "answer", "inputs": ["Y"], "outputs": ["ans"]}],
        "claims": [{"claim_id": x_id, "artifact_id": "X", "start": 0, "end": 23},
                   {"claim_id": y_id, "artifact_id": "Y", "start": 0, "end": 23},
                   {"claim_id": final_id, "artifact_id": "ans", "start": 0, "end": 23}],
        "entries": [{"entry_id": "e1", "claim_id": x_id, "account": "EVIDENCE:Y#0-23", "quoted_span": t},
                    {"entry_id": "e2", "claim_id": x_id, "account": "EVIDENCE:doc#0-23", "quoted_span": t},
                    {"entry_id": "e3", "claim_id": y_id, "account": "EVIDENCE:X#0-23", "quoted_span": t},
                    {"entry_id": "e4", "claim_id": final_id, "account": "EVIDENCE:Y#0-23", "quoted_span": t}],
    }


def test_a_result_computed_inside_an_open_cycle_is_not_memoised():
    # guards L09 (tainted results cached). README "What I Learned" says the shuffle
    # test guards this; the shuffle test stays green under the mutation.
    for ids in (("z", "a_x", "b_y"), ("a", "x", "y"), ("m", "a_x", "z_y")):
        b = close_books(load_run(_cycle(*ids)))
        assert b.audits[ids[0]].status is ClaimStatus.GROUNDED, ids


def test_a_cycle_is_named_circular_not_too_deep():
    # guards L10 (the visiting-set cycle guard removed: MAX_DEPTH still stops the
    # walk, so the status survives and only the reason changes)
    d = _cycle()
    d["entries"] = [e for e in d["entries"] if e["entry_id"] != "e2"]
    b = close_books(load_run(d))
    assert b.audits["z"].break_reason == "circular provenance"


def test_a_wide_citation_reports_the_worst_of_two_failing_claims():
    # guards L14 (worst chosen with < instead of >): the status stays laundered,
    # only which upstream claim is named as the break changes.
    summ = "Revenue was 14 million. Profit doubled."
    d = {
        "artifacts": [{"artifact_id": "doc", "kind": "document", "content": "nothing"},
                      {"artifact_id": "sum", "kind": "intermediate", "content": summ},
                      {"artifact_id": "ans", "kind": "final_answer", "content": summ}],
        "steps": [{"step_id": "s1", "kind": "summarize", "inputs": ["doc"], "outputs": ["sum"]},
                  {"step_id": "s2", "kind": "answer", "inputs": ["sum"], "outputs": ["ans"]}],
        "claims": [{"claim_id": "p_prior", "artifact_id": "sum", "start": 0, "end": 23},
                   {"claim_id": "q_none", "artifact_id": "sum", "start": 24, "end": len(summ)},
                   {"claim_id": "a", "artifact_id": "ans", "start": 0, "end": len(summ)}],
        "entries": [{"entry_id": "e1", "claim_id": "p_prior", "account": "PRIOR:model"},
                    {"entry_id": "e2", "claim_id": "a", "account": f"EVIDENCE:sum#0-{len(summ)}",
                     "quoted_span": summ}],
    }
    b = close_books(load_run(d))
    assert b.audits["a"].status is ClaimStatus.LAUNDERED
    assert b.audits["a"].break_claim_id == "q_none"   # unsupported ranks worse than prior_only


def test_an_answer_no_step_produced_cannot_cite_anything():
    # guards V03 (a claim with no producing step treated as reachable)
    d = _one_hop()
    d["steps"] = []
    run = load_run(d)
    b = close_books(run)
    assert run.entries[0].reason == "not_reachable_from_step"
    assert not b.books_balance


def test_an_answer_cannot_fund_one_of_its_sentences_with_another():
    # guards V01 (self-citation gate removed). A step that lists its own output
    # among its inputs passes the reachability gate, so only SELF_CITATION stops
    # sentence 2 of the answer from closing on sentence 1.
    ans = "Revenue was 14 million. Profit tripled."
    d = _one_hop()
    d["artifacts"][1]["content"] = ans
    d["steps"][0]["inputs"] = ["doc", "ans"]
    d["claims"].append({"claim_id": "a2", "artifact_id": "ans", "start": 24, "end": len(ans)})
    d["entries"].append({"entry_id": "e2", "claim_id": "a2", "account": "EVIDENCE:ans#0-23",
                         "quoted_span": ans[:23]})
    d["entries"][0]["account"] = "EVIDENCE:doc#0-22"
    d["entries"][0]["quoted_span"] = "Revenue was 14 million"
    run = load_run(d)
    b = close_books(run)
    assert b.audits["a1"].status is ClaimStatus.GROUNDED
    assert run.entries[1].reason == "self_citation"
    assert not b.books_balance


def test_a_three_character_quote_gets_no_free_edit_at_the_right_offsets():
    # guards V07 (tolerance floor of one edit restored). tests/test_ledger.py::
    # test_short_quotes_get_no_free_edit cites doc_filing#95-98, which is " ye",
    # not "14%" (that is #92-95), so it is rejected whatever the tolerance.
    from pathlib import Path
    ex = Path(tallystick.__file__).resolve().parents[1] / "examples" / "laundered_summary.json"
    if not ex.exists():
        ex = Path(__file__).resolve().parent / "laundered_summary.json"
    d = json.loads(ex.read_text(encoding="utf-8"))
    doc = next(a for a in d["artifacts"] if a["artifact_id"] == "doc_filing")["content"]
    assert doc[92:95] == "14%" and doc[95:98] == " ye"
    d["claims"].append({"claim_id": "sum_x", "artifact_id": "summary", "start": 13, "end": 16})
    d["entries"].append({"entry_id": "je_x", "claim_id": "sum_x",
                         "account": "EVIDENCE:doc_filing#92-95", "quoted_span": "44%"})
    run = load_run(d)
    close_books(run)
    je = next(e for e in run.entries if e.entry_id == "je_x")
    assert je.verified is False and je.reason == "span_mismatch"


def test_an_entry_with_no_quote_is_named_as_such():
    # guards V06 (NO_QUOTE gate removed): the verdict is the same, the stable
    # reason string - "public API" per verify.py - is not
    run = load_run(_one_hop(quote="   "))
    close_books(run)
    assert run.entries[0].reason == "no_quoted_span"


def test_an_empty_span_is_out_of_range():
    # guards V05 (start == end accepted): verdict the same, reason differs
    run = load_run(_one_hop(account_end=0, quote="Revenue"))
    close_books(run)
    assert run.entries[0].reason == "span_out_of_range"


def test_typographic_drift_in_a_short_quote_is_forgiven_by_normalisation():
    # guards V13 (normalize() dropped from the quote gate). The existing drift test
    # uses a 70-character quote with one differing character, which the 2%
    # edit tolerance forgives on its own.
    content = 'Margin was "11.2%" in the prior-year period.'   # 45 chars: no free edit
    quote = "Margin was “11.2%” in the prior‑year period."
    assert int(len(content) * 0.02) == 0
    run = load_run(_one_hop(quote=quote, content=content))
    close_books(run)
    assert run.entries[0].verified is True


def test_a_duplicate_entry_id_is_refused():
    # guards T05. No verdict depends on entry ids, so the only effect is exit 2 vs a verdict.
    d = _one_hop()
    d["entries"].append(dict(d["entries"][0]))
    with pytest.raises(TraceError):
        load_run(d)


def test_a_second_claim_with_the_same_id_cannot_hide_the_first(tmp_path):
    # guards I04 (duplicate claim id accepted). Without the check the later claim
    # overwrites the earlier one in run.claims: an unfunded answer sentence
    # vanishes and the books balance (exit 0 instead of 2).
    from tallystick.cli import main
    ans = "Revenue was 14 million. Profit tripled."
    d = _one_hop()
    d["artifacts"][1]["content"] = ans
    d["claims"] = [{"claim_id": "a1", "artifact_id": "ans", "start": 24, "end": len(ans)},
                   {"claim_id": "a1", "artifact_id": "ans", "start": 0, "end": 23}]
    d["entries"][0]["account"] = "EVIDENCE:doc#0-22"
    d["entries"][0]["quoted_span"] = "Revenue was 14 million"
    p = tmp_path / "t.json"
    p.write_text(json.dumps(d))
    with pytest.raises(TraceError):
        load_run(d)
    assert main([str(p), "--quiet"]) == 2


def test_a_trace_exactly_ten_times_the_log_is_reported():
    # guards S03 (`<` -> `<=`): README says the size is said "once the trace is ten
    # times the log"
    from tallystick.convert import TRACE_SIZE_NOTE_RATIO, json_bytes, trace_size
    log = [{"role": "user", "content": "hi"}]
    n = json_bytes(log)
    assert trace_size(log, {"steps": []}, trace_bytes=TRACE_SIZE_NOTE_RATIO * n) is not None
    assert trace_size(log, {"steps": []}, trace_bytes=TRACE_SIZE_NOTE_RATIO * n - 1) is None


def test_min_reachable_above_one_is_exit_2(tmp_path):
    # guards C13 (upper bound of --min-reachable dropped)
    from tallystick.cli import main
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_one_hop()))
    assert main(["check-trace", str(p), "--min-reachable", "1.5", "--quiet"]) == 2
