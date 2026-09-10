"""The benchmark's construction and metrics, offline.

No model here. An oracle proposer, scripted straight from the human labels,
must make tallystick reproduce those labels exactly: that proves the trace
construction, the audit and the scoring agree end to end, so a live result
measures the proposer and nothing else.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import build  # noqa: E402
from run import prf, sentence_flags_from_audit, summary_claim_eval  # noqa: E402

from tallystick import audit, load_run  # noqa: E402
from tallystick.propose import FakeProposer, post_run  # noqa: E402


@pytest.fixture
def traces():
    resp, src = build.load_ragtruth(ROOT / "bench" / "sample")
    out = []
    for item in build.select(resp, src, limit=6, seed=1):
        t = build.build_trace(item, src[item["source_id"]], seed=1)
        if t:
            out.append(t)
    assert out
    return out


def test_every_constructed_trace_is_a_valid_run(traces):
    for t in traces:
        load_run(t)
        ans = next(a for a in t["artifacts"] if a["kind"] == "final_answer")
        for s in t["_truth"]["answer_sentences"]:
            assert ans["content"][s["start"]:s["end"]] == s["text"]


def test_a_trace_does_not_depend_on_how_many_others_were_built():
    """Per-item seeding: the same item yields the same trace at --limit 3 and 6."""
    resp, src = build.load_ragtruth(ROOT / "bench" / "sample")
    by_id = {}
    for limit in (3, 6):
        for item in build.select(resp, src, limit=limit, seed=1):
            t = build.build_trace(item, src[item["source_id"]], seed=1)
            if t:
                by_id.setdefault(item["id"], []).append(json.dumps(t, sort_keys=True))
    shared = [v for v in by_id.values() if len(v) == 2]
    assert shared, "some items must be selected at both limits"
    assert all(a == b for a, b in shared)


def test_sentences_are_chosen_without_preferring_hallucinated_ones():
    """Selection inside an item is uniform: over many seeds, a hallucinated
    sentence is quoted about as often as a clean one."""
    resp, src = build.load_ragtruth(ROOT / "bench" / "sample")
    item = next(r for r in resp if r["labels"])
    sents = build.sentences(item["response"])
    labels = [(l["start"], l["end"]) for l in item["labels"]]
    bad_idx = {i for i, sp in enumerate(sents) if build.overlaps(sp, labels)}
    if not bad_idx or len(bad_idx) == len(sents):
        pytest.skip("sample item has no mix")
    counts = [0] * len(sents)
    for seed in range(300):
        t = build.build_trace(item, src[item["source_id"]], seed=seed)
        for s in t["_truth"]["answer_sentences"]:
            for i, (a, b) in enumerate(sents):
                if item["response"][a:b].strip() == s["text"]:
                    counts[i] += 1
    bad = statistics.mean(counts[i] for i in bad_idx)
    good = statistics.mean(counts[i] for i in range(len(sents)) if i not in bad_idx)
    assert 0.7 < bad / good < 1.3


def _oracle_script(t):
    summary = next(a for a in t["artifacts"] if a["artifact_id"] == "summary")["content"]
    docs = [a for a in t["artifacts"] if a["kind"] == "document"]
    spans = [(x["start"], x["end"]) for x in t["_truth"]["summary_hallucinated_spans"]]
    ssents = build.sentences(summary)
    asents = [s["text"] for s in t["_truth"]["answer_sentences"]]
    script = [{"claims": [summary[s:e].strip() for s, e in ssents]}, {"claims": asents}]
    for s, e in ssents:
        bad = any(s < le and ls < e for ls, le in spans)
        script.append({"credits": [] if bad else
                       [{"artifact_id": docs[0]["artifact_id"],
                         "quote": docs[0]["content"][:60]}]})
    for a in asents:
        script.append({"credits": [{"artifact_id": "summary", "quote": a}]})
    return script


def test_an_oracle_proposer_reproduces_the_human_labels_exactly(traces):
    truth_all, pred_all, s_pred_all, s_truth_all = [], [], [], []
    for t in traces:
        posted = post_run(load_run(t), FakeProposer(_oracle_script(t)))
        posted["_truth"] = t["_truth"]
        balance = audit(posted)
        truth = [s["laundered"] for s in t["_truth"]["answer_sentences"]]
        pred = sentence_flags_from_audit(posted, balance)
        truth_all += truth
        pred_all += pred
        sp, st = summary_claim_eval(posted, balance)
        s_pred_all += sp
        s_truth_all += st
    assert any(truth_all) and not all(truth_all), "sample must mix both classes"
    assert prf(pred_all, truth_all)["f1"] == 1.0
    assert prf(s_pred_all, s_truth_all)["f1"] == 1.0


def test_run_trace_counts_failures_instead_of_scoring_them(traces, tmp_path):
    """A proposer that returns garbage is a counted failure, not a free miss."""
    import argparse
    from run import run_trace
    t = traces[0]
    args = argparse.Namespace(proposer_runs=1, judge_runs=1, skip_judge=False, model="x")
    bad = FakeProposer(["not json at all"] * 10)
    row = run_trace(t, bad, args, tmp_path, "x.json")
    assert row["failures"]["proposer"] == 1 and row["tallystick_runs"] == [None]
    assert row["failures"]["one_hop"] == 1 and row["failures"]["full"] == 1
    assert row["one_hop_runs"] == [None] and row["full_runs"] == [None]


def test_prf_arithmetic():
    m = prf([True, True, False, False], [True, False, True, False])
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["fpr"] == 0.5


# --------------------------------------------------------------------------- #
# Found in review of the harness — a live run must survive partial failure
# --------------------------------------------------------------------------- #


def _oracle_rows(traces, tmp_path, judge_runs=2, proposer_runs=2, break_trace=None):
    import argparse
    from run import run_trace
    args = argparse.Namespace(proposer_runs=proposer_runs, judge_runs=judge_runs,
                              skip_judge=False, model="oracle")
    rows = []
    for i, t in enumerate(traces):
        truth_idx = [k for k, s in enumerate(t["_truth"]["answer_sentences"]) if s["laundered"]]
        script = _oracle_script(t) * proposer_runs
        script += [json.dumps({"unsupported": []}),
                   json.dumps({"unsupported": [k + 1 for k in truth_idx]})] * judge_runs
        if i == break_trace:
            script = ["garbage"] * 40          # every call fails on this trace
        rows.append(run_trace(t, FakeProposer(script), args, tmp_path, f"t{i}.json"))
    return rows, args


def test_a_trace_that_failed_on_any_side_is_dropped_from_all_sides(traces, tmp_path):
    from run import summarise
    rows, args = _oracle_rows(traces, tmp_path, break_trace=0)
    rep = summarise(rows, args, 1.0, {"natural_sentence_prevalence": 0.1})
    assert rep["dropped_traces"] == 1
    n = rep["tallystick"]["runs"][0]["n"]
    assert rep["one_hop"]["runs"][0]["n"] == n == rep["full"]["runs"][0]["n"]
    assert rep["failures"]["proposer"] == 2 and rep["failures"]["full"] == 2


def test_render_survives_a_run_where_everything_failed(traces, tmp_path):
    from run import render, summarise
    import argparse
    from run import run_trace
    args = argparse.Namespace(proposer_runs=1, judge_runs=1, skip_judge=False, model="x")
    rows = [run_trace(t, FakeProposer(["garbage"] * 40), args, tmp_path, f"t{i}.json")
            for i, t in enumerate(traces)]
    rep = summarise(rows, args, 1.0, {})            # no natural prevalence either
    text = render(rep)
    assert "not run" in text and rep["dropped_traces"] == len(traces)


def test_judge_run_indices_stay_aligned_when_one_run_fails(traces, tmp_path):
    """A failed judge run is a None placeholder, not a shift of later runs."""
    import argparse
    from run import run_trace, _per_run
    t = traces[0]
    args = argparse.Namespace(proposer_runs=1, judge_runs=2, skip_judge=False, model="x")
    truth_idx = [k for k, s in enumerate(t["_truth"]["answer_sentences"]) if s["laundered"]]
    good = json.dumps({"unsupported": [k + 1 for k in truth_idx]})
    script = _oracle_script(t) + ["garbage", "garbage", good,   # one-hop run 0 fails twice, full ok
                                  good, good]                    # run 1: both ok
    row = run_trace(t, FakeProposer(script), args, tmp_path, "t.json")
    assert row["one_hop_runs"][0] is None and row["one_hop_runs"][1] is not None
    assert len(row["full_runs"]) == 2 and all(r is not None for r in row["full_runs"])
    assert len(_per_run([row], "one_hop_runs")) == 1     # only the successful run scores


def test_rows_file_enables_resume(traces, tmp_path, monkeypatch):
    """Rows are appended per trace; a second invocation skips finished ones."""
    import run as runmod
    work = tmp_path / "work"
    (work / "traces").mkdir(parents=True)
    names = []
    for i, t in enumerate(traces):
        (work / "traces" / f"t{i}.json").write_text(json.dumps(t), encoding="utf-8")
        names.append(f"t{i}.json")
    (work / "manifest.json").write_text(json.dumps({"order": names,
                                                    "natural_sentence_prevalence": 0.1}))
    calls = {"n": 0}

    class Oracle:
        name = "oracle"

        def __init__(self, *a, **k):
            pass

    def fake_run_trace(trace, proposer, args, w, name):
        calls["n"] += 1
        row = {"file": name, "meta": {}, "truth": [False], "tallystick_runs": [[False]],
               "model": args.model, "proposer_runs": args.proposer_runs,
               "judge_runs": args.judge_runs,
               "summary_runs": [([], [])], "audit_identical": True,
               "claims_per_sentence": [1.0],
               "proposal": [{"claims_posted": 1, "credits_posted": 1, "prior_posted": 0}],
               "one_hop_runs": [[False]], "full_runs": [[False]],
               "failures": {"proposer": 0, "one_hop": 0, "full": 0}}
        return row

    import tallystick.propose as tp
    monkeypatch.setattr(tp, "AnthropicProposer", Oracle)
    monkeypatch.setattr(runmod, "run_trace", fake_run_trace)
    assert runmod.main(["--work", str(work), "--limit", "3", "--proposer-runs", "1",
                        "--judge-runs", "1"]) == 0
    assert calls["n"] == 3
    assert runmod.main(["--work", str(work), "--limit", "5", "--proposer-runs", "1",
                        "--judge-runs", "1"]) == 0
    assert calls["n"] == 5                       # only the two new traces ran


def test_resume_refuses_rows_from_different_settings_and_survives_a_truncated_line(
        traces, tmp_path, monkeypatch):
    import run as runmod
    work = tmp_path / "work"
    (work / "traces").mkdir(parents=True)
    names = []
    for i, t in enumerate(traces[:3]):
        (work / "traces" / f"t{i}.json").write_text(json.dumps(t), encoding="utf-8")
        names.append(f"t{i}.json")
    (work / "manifest.json").write_text(json.dumps({"order": names}))
    good = {"file": "t0.json", "meta": {}, "truth": [False], "tallystick_runs": [[False]],
            "model": "m", "proposer_runs": 1, "judge_runs": 1,
            "summary_runs": [[[], []]], "audit_identical": True, "claims_per_sentence": [1.0],
            "proposal": [{"claims_posted": 1, "credits_posted": 1, "prior_posted": 0}],
            "one_hop_runs": [[False]], "full_runs": [[False]],
            "failures": {"proposer": 0, "one_hop": 0, "full": 0}}
    (work / "rows.jsonl").write_text(json.dumps(good) + "\n" + '{"file": "t1.json", "tru',
                                     encoding="utf-8")

    class Oracle:
        name = "oracle"

        def __init__(self, *a, **k):
            pass

    ran = []

    def fake_run_trace(trace, proposer, args, w, name):
        ran.append(name)
        return dict(good, file=name, model=args.model, proposer_runs=args.proposer_runs,
                    judge_runs=args.judge_runs)

    import tallystick.propose as tp
    monkeypatch.setattr(tp, "AnthropicProposer", Oracle)
    monkeypatch.setattr(runmod, "run_trace", fake_run_trace)
    # different model -> refuse, exit 2, nothing run
    assert runmod.main(["--work", str(work), "--model", "other",
                        "--proposer-runs", "1", "--judge-runs", "1"]) == 2
    assert ran == []
    # same settings -> t0 kept, truncated t1 rerun, t2 run
    assert runmod.main(["--work", str(work), "--model", "m",
                        "--proposer-runs", "1", "--judge-runs", "1"]) == 0
    assert ran == ["t1.json", "t2.json"]


def test_a_judge_reply_without_a_list_is_a_failure_not_a_clean_sheet(traces):
    from judge import JudgeFailed, judge_one_hop
    t = traces[0]
    for bad in ('{"answer": "none"}', '{"unsupported": "1, 2"}', '{"Unsupported": [1]}'):
        with pytest.raises(JudgeFailed):
            judge_one_hop(t, FakeProposer([bad, bad]))
    assert judge_one_hop(t, FakeProposer(['{"unsupported": [1]}'])) == [0]


def test_a_trace_with_any_failed_run_is_dropped_so_denominators_match(traces, tmp_path):
    from run import summarise
    rows, args = _oracle_rows(traces, tmp_path)
    # break one judge run on one trace only
    rows[0]["one_hop_runs"][1] = None
    rows[0]["failures"]["one_hop"] = 1
    rep = summarise(rows, args, 1.0, {"natural_sentence_prevalence": 0.1})
    assert rep["dropped_traces"] == 1
    ns = {run["n"] for side in ("tallystick", "one_hop", "full") for run in rep[side]["runs"]}
    assert len(ns) == 1 and ns == {rep["answer_sentences"]}


def test_progress_line_survives_a_failed_first_proposer_run(traces, tmp_path, capsys):
    """Seen live at trace 12: the first proposer run failed, and the progress
    print crashed the whole session on sum(None)."""
    import argparse
    from run import run_trace
    t = traces[0]
    args = argparse.Namespace(proposer_runs=2, judge_runs=1, skip_judge=True, model="x")
    script = ["garbage"] + _oracle_script(t)          # run 0 fails, run 1 succeeds
    row = run_trace(t, FakeProposer(script), args, tmp_path, "t.json")
    assert row["tallystick_runs"][0] is None and row["tallystick_runs"][1] is not None
    # the exact expression main() prints must not raise
    fmt = lambda runs: [sum(x) if x is not None else None for x in runs]  # noqa: E731
    assert fmt(row["tallystick_runs"])[0] is None


# --------------------------------------------------------------------------- #
# The labels are the foundation of the table; the oracle test cannot see them.
# --------------------------------------------------------------------------- #


def test_ragtruth_annotation_offsets_address_the_text_they_claim():
    """Every RAGTruth label carries both a span and the text at that span. If
    any preprocessing shifted the response, this is where it would show. The
    same check over the full dataset (14,289 labels) passed at the n=100 run."""
    resp, _ = build.load_ragtruth(ROOT / "bench" / "sample")
    n = 0
    for r in resp:
        for lab in r["labels"]:
            n += 1
            assert r["response"][lab["start"]:lab["end"]] == lab["text"]
    assert n > 0


def test_answer_sentence_labels_can_be_rederived_from_the_raw_annotations(traces):
    """Independent derivation: locate each answer sentence in the raw response
    and test overlap against the raw label spans, without using build.sentences
    or the spans build_trace chose. Must agree with what build_trace wrote."""
    resp, _ = build.load_ragtruth(ROOT / "bench" / "sample")
    by_id = {r["id"]: r for r in resp}
    checked = 0
    for t in traces:
        item = by_id[t["_meta"]["ragtruth_id"]]
        labels = [(lab["start"], lab["end"]) for lab in item["labels"]]
        cursor = 0                      # answer sentences are in response order
        for s in t["_truth"]["answer_sentences"]:
            # A sentence's text can recur in a response (51 of 9,690 pool
            # sentences), and in one pool item (RAGTruth 12471) the recurrence
            # sits inside a labelled span while the sentence itself does not.
            # So collect every occurrence past the cursor and require the
            # written label to be the label of one of them; where the text is
            # unique, that is an exact check.
            occ, o = [], item["response"].find(s["text"], cursor)
            while o >= 0:
                occ.append(o)
                o = item["response"].find(s["text"], o + 1)
            assert occ, "answer sentence must come verbatim from the response"
            expects = {any(o < e and a < o + len(s["text"]) for a, e in labels)
                       for o in occ}
            assert s["laundered"] in expects
            cursor = occ[0] + len(s["text"])
            checked += 1
    assert checked > 0


def test_ci_reproduces_the_table_and_bootstraps_over_traces(traces, tmp_path, capsys):
    """bench/ci.py's point estimate must be exact on oracle rows, its interval
    must contain it, and the paired difference must collapse to zero when both
    sides are the oracle."""
    import ci
    rows, _ = _oracle_rows(traces, tmp_path)
    point = ci.mean_f1(rows, "tallystick_runs")
    assert point["f1"] == 1.0                      # oracle rows
    boot = ci.bootstrap(rows, n=50, seed=3)
    lo, hi = boot["f1_ci"]["tallystick_runs"]
    assert lo <= 1.0 <= hi
    assert boot["diff_full_minus_tallystick"]["ci"] == (0.0, 0.0)  # oracle on both sides
