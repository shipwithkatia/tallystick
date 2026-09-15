[← README](../README.md)

# Design

How tallystick works, the decisions behind it and what they cost, what building it taught, where it sits among the 2026 work on the same problem, and what comes next. The measurements are in [benchmark.md](benchmark.md); the specification of what a trace has to contain is in [auditable-traces.md](auditable-traces.md).

- [How it works](#how-it-works)
- [Before the audit: can this trace be audited at all?](#before-the-audit-can-this-trace-be-audited-at-all)
- [Tradeoffs and Decisions](#tradeoffs-and-decisions)
- [Lessons](#lessons)
- [Related work](#related-work)
- [Next steps](#next-steps)

## How it works

**Artifacts** are either roots (documents, tool results — text that entered the run from outside) or derived (summaries, plans, memory writes, the answer — text a model wrote). A credit against a root terminates a chain. A credit against a derived artifact is a promissory note: the claims of *that* artifact covering the cited span must be redeemed in turn, recursively.

**The conservation rule** is the gate that makes this a trace auditor rather than another span matcher: a step may only cite artifacts it received as inputs. Reachability is checked without reading any text.

**The recorder recovers inputs from the prompt.** No framework logs what a model call could see, so the LangChain adapter takes the only honest source: when a model starts, its prompt is kept; when it ends, every artifact recorded so far whose content appears verbatim in that prompt is an input of the step. "Was retrieved earlier" is not "was seen". Matching is longest-first with each match blanked out, so a document nested inside another is credited only when it appears on its own, and duplicates are credited once. Root artifacts under 20 characters are never matched (a three-word tool result would appear in any prompt by coincidence); model outputs always are. If a framework reformats a document before prompting — truncates it, re-wraps it — the recorder will not find it, the step gets no input, and its claims fail reachability. That fails closed, on purpose, and is the adapter's main limitation.

**Two rules keep the walk honest.** A citation into a derived artifact inherits *every* claim it covers — quoting the whole summary does not launder the one bad sentence in it (and since v0.5.3 the proposer's split of one quote into several entries is bound into a group that closes on its worst member — see [Tradeoffs](#tradeoffs-and-decisions)). And the cited span must be fully accounted for by claims: text nobody vouched for cannot fund anything.

**The proposer is a pointer generator, not a judge.** It is asked for claims and quotes as verbatim substrings of text it was shown. Each one is then *located*: exact substring search first, then (since v0.6) a match on the sequence of words that forgives punctuation, spacing, case and quote or dash variants and never a changed letter, digit or symbol — `14%` does not find `15%` or `14`. Whatever cannot be located is logged in the posted file and dropped. A claim with no locatable support is posted as `PRIOR:model`, which never funds anything, and a quote into text no claim vouches for is refused — so an invented sentence fails closed downstream, whether or not the segmenter picked it up. The model never answers "is this faithful?"; it only answers "where?", and the answer is checked.

**Two searches back the model up (v0.6).** A sentence of an intermediate artifact the segmenter did not return is posted as a claim anyway — *coverage* — so text a later step quotes always has an account, funded or not; the final answer is not covered, because its segmenter decides what the audit checks and a hedge it skipped is not a claim to fund. And for every claim, each source of the step is searched for the claim's own text; a word-for-word hit is posted as a credit marked `proposed_by: "verbatim"`, ahead of whatever the model offers. Both are substring searches, not judgements, and both are counted in the posted file's `_proposal` block.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every verdict-path module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Before the audit: can this trace be audited at all?

An audit of provenance can only work on what the recording kept, and the thing it
most often did not keep is the page behind a tool result. `tallystick check-trace`
reads a raw trace and says so before a single model call is spent:

```
$ tallystick check-trace run.json

Auditability - 11 step(s), 12 artifact(s)
----------------------------------------------------------------------------
  What a chain passes through: 11 piece(s) of text
    9      81%  written by the model - the audit can ask what it rests on
    2           returned by a tool - a chain stops here, on trust
  Plus 1 document(s), 737 character(s) of external text kept verbatim -
  which is what a trace is for, so it is not counted in the share.

  Higher is better: the more of a run the model wrote down, the more of it
  an audit can follow.

AUDITABLE - no defect stands in the audit's way.
  2 step(s) recorded a tool result and nothing the model wrote, so the audit
  cannot ask what happened there. Record the model's own text for each of:
    s3.tools, s5.tools

Worth knowing (does not decide the verdict):
  [duplicate_content] these artifacts hold the same text, so a quote cannot
      be attributed to one rather than another, and tooling that matches
      artifacts by their text will confuse them
      answer, s10
```

Three things are reported and they are not the same kind of thing. The **share** is
a fact about the recording: of the pieces of text a chain passes through or stops at,
how many hold the model's own words rather than a tool's output. It is counted over
artifacts, not steps, because a step-based fraction measures the recorder — one
agent turn logged as a single step and the same turn logged as two score
differently, while the artifact count cannot change: artifacts are texts, and
how turns are grouped into steps does not change how many there are. It decides nothing unless you ask with `--min-reachable`, because a run
that leans on tools is not broken; it is a run whose clean audit means less. The
**defects** decide the verdict and each names something a recorder can fix: model
text from a step that declares no inputs (nothing it wrote can ever be funded), text
no step admits to producing, an empty or truncated artifact, more than one answer,
or none. The **notes** are worth knowing and are nobody's bug — above all two
artifacts holding the same string, which real agents produce constantly and which
breaks any tooling that matches artifacts by their text. Exit 0 when no defect
stands in the way, 1 when one does or the recording is thinner than a
`--min-reachable` you passed, 2 when the file cannot be read.

The three verdicts say what they license you to conclude. **AUDITABLE**: nothing in
the recording stops the audit; a clean result from it means something. **PARTIAL**:
the audit will run, and its silence will not mean much — read it as "nothing found
here", not "nothing there". **UNAUDITABLE**: there is no answer to work back from,
or nothing the model wrote, so the audit has no question to ask. Exit 1 covers the
last two, because the distinction that matters to a build is "can I trust a clean
result", and the answer for both is no.

Because `check-trace` needs no model, the 80% line could be measured on the whole of
AgentHallu rather than the subset a paid run could afford —
[`bench/auditability_agenthallu.py`](../bench/auditability_agenthallu.py), output in
[`bench/results/auditability-agenthallu.txt`](../bench/results/auditability-agenthallu.txt).
Of the 443 labelled trajectories, those at or above 80% have the hallucination beyond
the audit's reach in 24 of 84 runs (29%); those below it, in 212 of 359 (59%).

Three things are true about that table and they belong together.

The share itself is counting: it says how much of a run the audit cannot look at.

**The banding is mostly arithmetic, and the script proves that against itself.** Swap
the human label for a step drawn at random from the same trajectory — a label that
knows nothing about where the hallucination is — and the same ordering appears in
every one of the five draws the script prints, 13% above the line against 46% below
on average (7–15% against 44–47% across the draws), because a trace with more
tool-only steps makes *any* step more likely to be tool-only. So "below the line, more hallucinations are out of reach" is largely a
restatement of "below the line, more of everything is out of reach", and the
significance test the script prints rejects for the placebo too, which is why it is
printed beside it rather than quoted alone.

**But the real labels are not the placebo.** They sit at a tool boundary 236 times
where each trace's own composition predicts 172 — 1.37×. In a within-trace Monte Carlo
that draws each trace at its own rate of tool-only steps, none of 20,000 draws reached
236; that is the floor of what 20,000 draws can resolve rather than a measured p-value,
and it is not a permutation test — nothing is shuffled, each trace draws its own coin.
(The framework-stratified test printed beside the placebo *is* a permutation test, and
it rejects for the placebo too, which is why it is never quoted alone.)
The enrichment is largest in the traces with the *highest* share
(24 against 8.8, 2.7×). Real hallucinations do land at tool boundaries more often than
chance puts them. That is a fact about agents rather than about this number, and it is
why the boundary is worth measuring at all. The cut is not held out, and it decides
nothing unless you pass `--min-reachable`.

[`docs/auditable-traces.md`](auditable-traces.md) is the specification behind
it: what a trace has to contain, in the order it matters, framework-agnostic and
asking nothing of tallystick.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping. Found while reviewing the v0.5.2 benchmark results: the proposer never posted a wide span. It snapped a quote that covers two summary claims into one credit per claim, and a claim with several credits closes if any one does — so on proposer-posted files the all-of rule was bypassed by the pipeline that feeds it, and a sentence made of one true clause and one invented clause closed on the true one. The rule was right; the pipeline undercut it. v0.5.3 closes it with **entry groups**: the entries that came from one quote share a `group` id, and the ledger closes a group only if every member closes — the quote inherits the worst claim it covered, as a single wide entry would — while independent entries still close on the best (a real second source is a real second source). Hand-written traces without groups are unchanged. What stays open: a narrow second quote that is a sub-span of the group's own quote still closes the claim on its own, under the independent-entries rule — the same where-not-whether cost stated under [Benchmark](benchmark.md). The v0.5.2 table was measured with the leak in place; the v0.5.3 rerun that followed is in `bench/HISTORY.md`.
- **The model proposes into a file; the verdict reads the file.** Segmenting text into claims and proposing credits are model tasks by nature. Putting them in a separate package with a materialised output means the audit stays reproducible byte for byte and the model's work can be inspected, diffed and re-run without touching the verdict. The cost is one extra artefact on disk and one more command.
- **Verbatim or nothing on the model side.** The proposer could have been allowed to paraphrase. That would have raised recall and quietly reintroduced a judgement call into the credit. Every span the proposer posts is located word for word (punctuation, spacing and case at most — see the next point); paraphrase is dropped and logged. The verifier, which also audits hand-written traces, separately allows 2% edit distance for typographic drift with no floor — two rules, both stated.
- **v0.6: the false flags had causes on disk, so they were fixed on disk.** `bench/diagnose.py` re-audits a run's posted files offline and sorts every false flag by the reason the chain broke. At v0.5.3 the top causes were not paraphrase: a credit the model never offered, a quote that straddled the summary's claim boundaries, and a quote dropped for punctuation the model had added or left off. Each got a deterministic fix — the word-level locate, coverage claims for intermediate artifacts, the self-evident credit. Two were refused: a character-level tolerance, because a locate that forgave one character would let `14` find `15`; and coverage on the final answer, because it turned skipped hedges into flagged claims. What the fixes did to the table is under [Benchmark](benchmark.md). The full diagnosis, the holes review found in the first cut, and the one fix that overshot on real trajectories are in [`bench/HISTORY.md`](../bench/HISTORY.md).
- **Credits into a summary are resolved by containment, not by trimming.** The segmenter is told to skip meta text ("In summary,"); a quote that straddles such text would otherwise cover characters no claim owns, and the audit would report laundering that isn't there. The first fix trimmed the quote to whatever claims it touched — and review showed that a quote clipping two characters of a real claim was then rewritten into those two characters and *counted*. The rule now has no thresholds: a quote funds a claim only if one contains the other. A quote inside a claim is kept; a claim inside a quote is kept (one credit per whole claim, meta text discarded and logged); a partial straddle is refused as ambiguous.
- **No tolerance floor on quote matching.** A 2% edit tolerance for typographic drift with a minimum of one edit meant `14%` could be cited as `44%` and pass. The floor is gone; a three-character quote gets no free edit.
- **An empty trace does not balance.** `all([])` is `True` in Python, so a truncated trace originally exited 0. A gate that passes on missing input is not a gate.
- **Exit 2 is a hard boundary.** Seven ordinary malformed-JSON shapes originally escaped the loader as `KeyError`/`TypeError` and reached the shell as exit 1 — "your agent is unfaithful" — when the truth was "I could not read your file". The loader now validates every shape and raises one typed error, and the same validation runs on a `Run` built by hand.

## Lessons

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it. Each hop can be individually correct while the whole chain is wrong, and only a transitive walk sees that.
- "Deterministic" is a property you have to defend structurally. Memoising a result computed while a cycle was open made the verdict depend on array order in the JSON file. Same run, same code, different exit code. The fix was to refuse to cache anything tainted by an open cycle, and the shuffle test now exists so it cannot regress silently.
- A README that overstates what the code does is the single most expensive defect in a project whose thesis is "unsupported claims should be named". The adversarial review pass caught seven such lines before publish.
- **Writing and attacking are different jobs, and the same pass cannot do both.** Every serious defect in this repo was found not while writing but in a separate adversarial review run afterwards, by a reviewer whose only brief was to break it: the wide-citation bypass and the array-order dependence in v0.1; in v0.2, a false `laundered` verdict on a model that had followed the prompt correctly, a string-shaped answer that would have posted one claim per character, and three ways to sneak a model import past the boundary test. None of them were visible from inside the writing.
- **A framework tells you what happened, not what a model saw.** LangChain logs retrievals, tool calls and model outputs, but nothing in it says which of those a given model call had in its context. The recorder recovers that from the prompt bytes, and the first review showed how easy it is to get subtly wrong: a 20-character floor meant for tiny tool results was also dropping short model outputs from the chain, and chat "content blocks" were being JSON-escaped so no multi-line document ever matched. Both mislocated the injection step — the one number the project promises — while every test stayed green, because the demo happened to use one long paragraph.
- **A fix needs its own review.** The fix for the false `laundered` verdict passed its tests and was wrong in both directions — it still failed when the meta text sat *between* two claims, and it turned a mostly-unvouched quote into a two-character credit that counted. The tests I wrote for the fix tested the case I had in mind, not the cases I had not. A third pass, scoped to the fixes only, caught it. The rule this repo now follows: nothing ships without a second pass whose job is to make the first one look bad, and fixes get the same treatment as the code they fix.
- **Decide what the table means before it exists.** The benchmark's caveats, the three rows and the sentence "The table goes here when it exists, whatever it says" were all written and published before a single live call. When the number came in against tallystick on F1, there was nothing to negotiate: the README already said what would be reported and how. A number that could have been adjusted after the fact would have been worth less than the one that couldn't.
- **The failure you get is not the one you tested for.** The first live run crashed at trace 12 on a line I had added the same day to *handle* failures — a progress print that assumed the thing it was printing existed. Then 7 of 198 proposer runs failed and were dropped — the ones I inspected were malformed JSON (a missing comma inside the object), which no amount of trailing-text tolerance fixes. One was a harness bug, one a model-output bug; neither touched the audit, and both cost a rerun.
- **The worst failure is the one that prints a plausible table.** At the v0.5.3 rerun, `run.py` found the v0.5.2 rows file, checked that the model and run counts matched — they did — and resumed: 99 old rows plus one new trace, followed by a complete, well-formatted report. Nothing crashed. The resume check tested the settings and not the data, because when I wrote it the data could not change. Rows now carry a fingerprint of their trace, and a mismatch refuses to resume.
- **Sort the errors by cause before touching the model.** At v0.5.3, one clean sentence in six was flagged, and the obvious reading was "the proposer paraphrases". An offline pass over the posted files said otherwise: the top causes were a credit the model did not bother to offer for text sitting verbatim in its source, a sentence the segmenter skipped, and a full stop the model added — in that order. Three searches, no new judgement anywhere, and the false flags halved on the same sentences. The paraphrase problem is real — it is most of what is left — but it was a third of the story, not the story.

## Related work

The comparison table is in the [README](../README.md#where-this-sits).

The problem itself is not new and is now well documented: 2026 saw at least three papers on errors that arise at an intermediate agent step — AgentHallu, and two that name the effect *snowball* and *cascade*. Singh & Pawar inject errors and measure an 89% escape rate at the last of the three hand-offs in their four-agent pipeline; Jamshidi et al. ([Hallucination Cascade](https://arxiv.org/abs/2606.07937)) measure something different — the hallucination score of each agent's output in a refinement chain, no injection — and find it falls along the chain, which is worth keeping next to the first. AgentHallu is the closest in aim — find the step responsible — and benchmarks model judges at it on real trajectories; it is the data the v0.7 run is on (see [Benchmark](benchmark.md)). What none of the works in the [comparison table](../README.md#where-this-sits) do together is the three things this repository is built around: a verdict with no model in it, so the same trace gives the same answer; an audit that runs after the fact over a trace it did not produce, rather than provenance the agent writes about itself; and the conservation rule, under which a step may cite only what it received and a citation into model-written text inherits the worst claim it covers. The 2026 survey [*From Agent Traces to Trust*](https://arxiv.org/abs/2606.04990) lists claim-level provenance across execution chains and benchmarks of multi-step traces with labelled unsupported claims as open; this is a shipped, tested answer to a narrow slice of both, with its limits measured above.

Citations grounds one hop and does it well; the plan is still to ingest those citations as pre-verified credits and spend effort on the hops citations cannot see.

## Next steps

Done so far: v0.2 model-side proposers outside the verdict path; v0.3 LangChain recorder; v0.5–v0.5.3 the benchmark, its intervals and the entry-group fix; v0.6 precision from the false-flag diagnosis; v0.7 the AgentHallu adapter and harness. The version-by-version record, with what each one cost, is in [`bench/HISTORY.md`](../bench/HISTORY.md).

- [x] v0.7.3 run — 228 AgentHallu trajectories (115 labelled, of which 61 at a tool-result boundary; 113 clean), CodeAct runs excluded and counted. Two runs were withdrawn in review before publication, and three cheap changes were measured on the clean rerun before one was adopted; both stories, with numbers, are in `bench/HISTORY.md`
- [ ] a second retry when the proposer returns malformed or empty JSON: 7 of 228 runs were lost to it at v0.7.0, 4 at v0.7.2
- [x] a pre-flight auditability check (`check-trace`): what share of the artifacts a chain passes through hold the model's own words, and which defects a recorder can fix, so a trace is told whether it can be audited before anything is audited. Measured on all 443 labelled AgentHallu trajectories at no cost, since it calls no model: 29% unreachable above the line against 59% below, most of that gap arithmetic rather than prediction — though real labels do sit at tool boundaries more often than the placebo (see [Real trajectories](benchmark.md#real-trajectories-agenthallu-v073-september-2026))
- [ ] an adapter for the log shapes practitioners already have — OpenAI Chat Completions message lists first, then OpenTelemetry GenAI spans — so that `check-trace` can be run on a real recording without writing a converter by hand. This is the gap between the tool and its first user
- [ ] v0.8 — the recall gap is the verifier checking *where*, not *whether*. On real trajectories 5 of 23 reachable misses are a real source misread (a 1946 date taken for a 1937 one), which is exactly this. One deterministic candidate: require the funding span to contain the claim's content words, measured on this benchmark before it is adopted. Separately: let the proposer post a paraphrase together with the verbatim span behind it, and verify the span
- [ ] coverage for the remainder of a sentence the segmenter claimed only in part ("Revenue rose, and the CEO resigned" with a claim over the first clause): today the remainder is logged as uncredited characters, not posted as a claim
- [ ] adapters: LangSmith and OpenTelemetry exports, so a team can audit yesterday's logs without changing code — and a benchmark on such traces, three or more steps, where the last hop is not verbatim by construction; LlamaIndex; Claude Citations ingested as pre-verified credits
- [ ] HTML ledger view: the answer colour-coded by status, click a sentence to unfold its chain to the root
- [ ] PyPI release

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
