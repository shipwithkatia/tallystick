# tallystick — version history and earlier benchmark runs

[`docs/benchmark.md`](../docs/benchmark.md) keeps the current benchmark table. Earlier runs are kept here in full — tables, intervals, and what each change cost — because the honest record of a measurement includes the versions where it went the wrong way.

## Versions

Three words below are not the same thing, and this file had used them as if they
were. A version is **tagged** when `git tag` shows it: `v0.6.0`, `v0.7.0`,
`v0.7.4` and `v0.9.0` are, and nothing else is. A version **reached `main`**
when its commits are on the public default branch, tag or no tag: every number
below did except the three named next, and v0.7.5, v0.8.0 and v0.8.1 are the
ones that did so without a tag. A version **never reached `main`** when its work
stayed in a branch: v0.8.2 to v0.8.4, each said so where it stands. Two numbers
are a third case worth naming: `pyproject.toml` never carried `0.5` or `0.5.1`,
so those two entries record work that reached `main` under the number after
them.

- **v0.1** — deterministic core: artifacts, steps, claims, entries; three verifier gates; the chain walk; exit codes 0/1/2; an AST test that no verdict-path module imports a model SDK.
- **v0.2** — model-side proposers (`propose/`), kept outside the verdict path; the model posts claims and quotes into a file, the audit reads the file; verbatim-or-nothing locate; containment rule for credits into a summary.
- **v0.3** — LangChain callback recorder: inputs of a step recovered from what was verbatim in its prompt; then, before v0.5, exit 2 as a hard boundary for unreadable input.
- **v0.5** — benchmark harness: two-hop traces from RAGTruth, tallystick vs. a one-hop and a full-history LLM judge, one command.
- **v0.5.1** — harness survives a failed proposer run; JSON parse ignores trailing text.
- **v0.5.2** — first run at n=100; bootstrap intervals, paired difference, label-integrity tests. Result below.
- **v0.5.3** — entry groups close the any-of leak the v0.5.2 review found; a proposer reply that is not JSON is asked for once more; the sentence splitter treats a line break as a boundary; QA traces carry the question as a root; rows carry a trace fingerprint and a mismatch refuses to resume. Result below.
- **v0.6** — precision, from `bench/diagnose.py` on the v0.5.3 posted files: word-level locate (punctuation and case forgiven, never a character), coverage claims for intermediate artifacts, self-evident credits, containment judged on words; rows refuse to resume across versions. Result in [`docs/benchmark.md`](../docs/benchmark.md).
- **v0.6: the false flags had causes on disk, so they were fixed on disk.** `bench/diagnose.py` re-audits a run's posted files offline and sorts every false flag by the reason the chain broke. On the v0.5.3 run (86 false flags in 198 posted files) the top causes were not paraphrase: the model returned no credit at all (45, of which 18 at the answer's own hop, where the answer sentence is a summary sentence by construction); the answer's quote cut across the summary's claim boundaries or landed on a sentence the segmenter had skipped (26); a quote dropped for a full stop, quotation marks or a `(Passage 1)` tail the model had added or left off (15). Each got a deterministic fix — the word-level locate, coverage claims for intermediate artifacts, the self-evident credit — and the containment rule now judges on words, so a trailing full stop the segmenter and the credit prompt disagree about is no longer a straddle. Two things were considered and refused: a character-level tolerance, because a locate that forgave one character would let `14` find `15`; and coverage on the final answer, because on the posted files it turned skipped hedges ("it is difficult to give an exact answer") into flagged claims. Review of the first cut found two more holes, both closed: a dash is a separator, so `-5%` was found by `5%` (a dash or bracket hugging a number is now part of it), and the sentence regex let a sentence begin after any full stop, so `3.5%` produced the claim "5% compared to last year" and the search grounded it — the splitter, in `bench/build.py` too, now starts each sentence where the last one ended and does not break after an abbreviation. Two limits are stated up front. On this benchmark the answer is made of summary sentences, so the self-evident credit settles the answer hop by bookkeeping: the 44 flags that broke there should go away without any detection improving (at the rerun, 3 of them remained — two of those on one sentence the v0.5.3 splitter had cut inside "3.2%", a cut the splitter fix above cannot reach because the traces were deliberately not rebuilt), and the summary→document hop is where detection is measured — the rerun carries the three counters (`coverage_claims`, `tolerant_locates`, `self_evident_credits`) in every row so the two can be told apart. And the search is as blind as the verifier to *whether*: a summary sentence found inside a document sentence that negates it is grounded, as a model's quote of it was before; the difference is that now nothing declines to quote. What the fixes did to the table is measured in [`docs/benchmark.md`](../docs/benchmark.md): false flags 86 → 37 on the same sentences, with the recall and summary-step costs stated there. One of the review's own fixes then overshot: to stop `5%` matching inside `-5%`, an exact hit had to sit on a word boundary on both sides — and the first AgentHallu run (v0.7.0) refused 177 quotes (and 36 segmenter claims) that were in the source word for word, because scraped pages glue words together ("87,700 resultsEtta Cone commissioned…"). v0.7.1 refuses an exact hit only where it cuts into a number — a digit, sign, dash, currency or percent mark, or a mark between two digits on the outside, or a bracket pair hugging it, the same reading of a number the word-level match uses; letters glued to a hit are fine. That run's numbers are not published, and nor are the v0.7.1 rerun's — its `--reuse` replayed coverage claims onto final answers (see v0.7.1 below); the v0.7.2 rerun's are.
- **v0.7** — real agent trajectories: `tallystick/adapters/agenthallu.py` and `bench/agenthallu.py`; credits proposed on demand from the final answer. The first run (v0.7.0: 228 trajectories, 221 scored, 7 proposer failures) was withdrawn in review: the locate refused 177 verbatim quotes and 36 segmenter claims at glued word boundaries.
- **v0.7.1** — the locate refuses an exact hit only where it cuts into a number; `--reuse` answers a rerun's questions from an earlier run's posted files; `bench/diagnose_agenthallu.py` sorts false alarms by cause. The rerun (228 trajectories, 223 scored, 5 proposer failures) was withdrawn in review too: `--reuse` matched artifacts by text alone. In AgentHallu the final answer usually repeats an earlier artifact word for word — 210 of the 223 did — and the reuse answered the answer's segment question from the twin. Where the twin was the last model step, the step's coverage claims came back as the segmenter's own, so the answer got claims it never has by design ("Otherwise, I will terminate the interaction."): 82 answers were given claims the segmenter had never returned for them. Where the twin was a tool result (OpenManus copies a `browser_use` digest into its answer), the answer got no claims at all and could not be flagged: 19 trajectories. On the 194 trajectories an offline replay reproduces exactly, the fixed reuse gives 26 false alarms on 92 clean trajectories where that run gave 34 and the v0.7.0 run 29.
- **v0.7.2** — posted claims carry `proposed_by` (the segmenter's name, `coverage`, or `replay` for a claim replayed from an untagged earlier file; `whole-answer` joins them at v0.7.3); the reuse replays only what the model said, prefers the final answer among model-written artifacts with the same text, takes several directories in order, and reads a directory written by a reuse run for credits only unless its files are tagged; a claim replayed from an untagged file stays `replay` however many runs pass it on, and such a rerun's `coverage_claims` counter is not comparable with a fresh run's. The summary names the trajectories whose final answer got no claim. The rerun (224 scored, 4 proposer failures) was clean and is not published on its own: three cheap changes were measured on its posted files first, offline, and the run that carries the one adopted is the one published.
- **v0.7.3** — a bare-value final answer the segmenter returned no locatable claim for is posted whole as one claim (`proposed_by: "whole-answer"`). The hole this closes: on the v0.7.2 run 18 of 224 answers carried no claim at all, so they could not be flagged whatever they said, and in the table they were indistinguishable from an answer that was checked and came back clean — 7 of the 18 are labelled hallucinations, among them the answers "1898", "360,573,1200", "234.8" and "Yes". Fifteen of the 18 are a bare value: one line, at most 4 words and at most 24 characters. The corpus does not choose those bounds — its longest bare value is "The Cradle Will Rock." at 21 characters and its shortest answer above them is 148, so anything from 22 to 147 selects the same 15. They are set at the observed maximum plus a small margin, deliberately, against a case this corpus does not contain: a short sentence the segmenter skipped on purpose ("Task completed successfully.", 28 characters — a constructed example, not one from the data) would become a false alarm under a wider cap.
- **What v0.7.3 cost and bought, on the published run.** An offline replay of the v0.7.2 files predicted one extra reachable flag and two extra false alarms; the live run gave neither figure, and the prediction's own caveat is why. The replay answered new credit questions with a stub that offered nothing, so every new claim came back unfunded; the real proposer offered quotes for most of them. On the 224 trajectories both runs scored, everything in the published table is unchanged — reachable flagged 31, exact 8, labelled step among the breaks 8, within one 11, beyond the boundary 7 — and exactly one cell moves: false alarms 36 → 37 (`Camel/002`, the answer "536", which no root in the trace supports). What the rule bought is the line below the table: answers carrying no claim at all fell from 18 to 3. Fourteen of the fifteen bare answers came back grounded, one flagged. So it found no new hallucination and cost one false alarm; what it removed is a silent pass — an answer that was never audited and sat in the table looking exactly like one that was checked and came back clean. That is worth one false alarm, and the replay-versus-run gap is worth recording: an offline measurement that stubs the model measures the stub.
- **Two further changes were measured on the same files and refused.** Skipping model narration in coverage — lines like `**Step-by-Step Process:**` (Octotools/038) or `I will now consult you to clarify:` (OpenManus/099) — matched 1,323 of the 8,178 sentences of model steps (16%), factual bold lines among them ("**Total: 5 distinct hydrogen signals**"), and removed 3 of 36 false alarms without changing a single hit; three flags are not worth a hand-written list of phrases that can swallow a real claim. Requiring the claim's numbers to appear in the funding span: flagged reachable 31 → 40, but false alarms 36 → 58, and reading them, both came from one place — the answer rounds what the code printed (`3.12 × 10⁻⁷ meters` in OpenDeepSearch/018 against `3.117947442816187e-07` in the interpreter output) — a difference in how a number is written down, not a signal about provenance. None of the three fires on RAGTruth: all 199 posted files of the v0.6 run have a claim on the final answer, so that table is unchanged.

- **v0.7.4** — the AgentHallu run is published (rows, report and diagnosis in `bench/results/`), and `tallystick check-trace` ships with it: what an audit of a raw trace will be able to see, before any claim is posted. It reports a share — of the artifacts a chain passes through or stops at, how many hold the model's own words rather than a tool's output — plus defects a recorder can fix and notes that are nobody's bug. The share is counted over artifacts rather than steps because a step-based fraction measures the recorder: one agent turn logged as a single step and the same turn logged as two score differently, while the artifact count cannot: artifacts are texts, and how turns are grouped into steps does not change how many there are. `document` roots are left out of it, and an artifact recorded with no content counts on neither side. Two artifacts holding the same text is a note, not a defect — it fires on 216 of 225 traces of the published run, and the fix is a change to the agent, not the recording, so failing a build on it would be noise. Because the check calls no model, the line behind it could be measured on the whole dataset instead of the subset a paid run could afford: `bench/auditability_agenthallu.py` over all 693 trajectories, output in `bench/results/auditability-agenthallu.txt`. Of the 443 labelled, those at or above 80% have the label beyond the audit's reach in 24 of 84 runs (29%) and those below in 212 of 359 (59%) — and the script carries the placebo that keeps that from being oversold. Replace the human label with a step drawn at random from the same trajectory, which knows nothing about the hallucination, and the same ordering appears — 13% against 46% — because a trace with more tool-only steps makes any step more likely to be tool-only. So most of the banding is arithmetic. The real labels are not the placebo, though: they sit at a tool boundary 236 times where each trace's own composition predicts 172 (1.37×; in a within-trace Monte Carlo that draws each trace at its own tool-only rate, none of 20,000 draws reached 236 — the floor of what that many draws resolve, not a measured p-value), and most so in the traces with the highest share (24 against 8.8). That test was called a *permutation* test in the v0.7.4 entry, in the README and in the committed report, and it is not one: nothing is shuffled, each trace draws its own coin. Corrected at v0.7.5, with a test that fails the build if the word returns. Hallucinations really do land at tool boundaries more than chance — a fact about agents, not about this number. A first draft of this entry quoted a framework-stratified permutation test at p = 0.0027 as evidence that the association "survives the test that matters"; it rules out a between-framework confounder, it rejects for the placebo too, and quoting it that way was wrong. It is printed beside the placebo and nowhere else. The cut is chosen on this data and not held out, so it decides nothing unless `--min-reachable` asks. `docs/auditable-traces.md` is the specification behind it.

- **v0.7.5** — the log a person already has, read without a converter:
  `tallystick/adapters/openai_chat.py` reads an OpenAI Chat Completions message
  list (LiteLLM, vLLM and most gateways write the same keys) and
  `tallystick/adapters/otel_genai.py` an OpenTelemetry GenAI span export;
  `check-trace`, `convert` and `propose` all take `--from {auto,tallystick,openai,otel}`.
  With it: the three tool declarations (`--tool-returns-model-text`,
  `--tool-returns-verbatim`, `--tool-returns-external`), `--require-declared-tools`
  for a team whose tool set is fixed, and the demotion the reader decides on its
  own — a tool reply that IS a value of the call it answered is the model's text,
  not a root, whatever anyone declares. Six example logs and their exact expected
  output ship with it under `examples/logs/` and `examples/expected/`. Also here:
  the within-trace test is called a Monte Carlo rather than a permutation test, in
  this file, the README and the committed report, with a test that fails the build
  if the word comes back (`tests/test_bench.py`).
- **v0.8.0** — what the reader noticed stops being a gate and becomes a sentence.
  The echo *warning* that held the exit at 1 until each tool was confirmed by name
  is gone and does not come back: a signal wrong two times in three may not fail a
  build, but what it noticed is printed — as `NOTE` under the report, in
  `may_be_model_text` under `--json`, on stderr under `--quiet` — and it moves no
  exit code. An external review read 20 such notes on AgentHallu by hand: 6 of 20
  were the model's own text. Three exit-code rules join it: more than half of the
  answer's letters and digits under no claim is exit 1 (`answer_mostly_unclaimed`,
  the owner's decision of 16 September 2026 — on 1,489 posted traces it moved 130
  runs from 0 to 1, `python bench/half_line.py`); a chain deeper than 256 hops is
  `unchecked` and exit 2 with `chain_too_deep`, not a failure; tool-call arguments
  nested past 256 levels are refused at that limit of ours rather than wherever
  the interpreter gives up, the same on the Pythons CI runs. `convert` and
  `check-trace` say out loud, with the sizes,
  when a trace is ten times the log it was read from, and change no exit code for
  it.
- **v0.8.1** — two findings of an external adversarial review of v0.8.0, both
  demonstrated against the shipped code before anything was changed here.
  **The quote gate.** Its tolerance for typographic drift was an edit budget of
  2% of the span's length, with no ceiling, so a long quote bought edits: a
  307-character source saying `12.4 million euro` cited as `92.4 million euro`
  balanced and exited 0, and a 2,707-character span had its closing sentence
  replaced and balanced too. The floor of that budget had been removed in an
  earlier round and written up as closing the class; it did not. A ceiling would
  not close it either, since one edit moves a digit. The budget is gone, replaced by a
  content signature of letters, digits and separators standing between two digits
  — itself too narrow, and corrected at v0.8.2 below. The mutation test that
  asserted `14 million` may be cited as `15 million` now asserts the opposite,
  and `tests/test_quote_content_survives.py` holds both demonstrated cases open.
  Published numbers predate this and were re-checked rather than re-measured:
  see v0.8.2, where the count is given.
  **The break reason.** `ledger.py` promised results independent of the order of
  arrays in the input file. The verdict was, and is: 2,000 shuffles of every array in
  each of the two shipped examples move no status and no breaking step, recounted
  here rather than taken from the review, and the reason is now in that set
  (`tests/test_break_reason_is_order_independent.py` repeats the 2,000
  shuffles). The sentence explaining it was not — it read
  `entries[0].reason`, so one claim with three rejected entries reported
  `artifact_unknown`, `span_mismatch` or `prior_never_funds` according to how the
  file happened to list them, in the terminal, in `--chain` and in `--json`. The
  earliest gate that failed is now named, in a fixed order
  (`verify.REASON_ORDER`), with the two bookkeeping positions last;
  `tests/test_break_reason_is_order_independent.py` walks every permutation.
  Still open from the same review, and not fixed here: inside a *cycle* of claims
  that cite each other, which step is named still depends on the claim ids,
  because the members are ordered by id; and a recorder that writes UTF-16 code
  units where the format means Unicode code points gets a verdict about its agent
  rather than a word about its offsets.

- **v0.8.2** — the quote gate says what it means, and the price is measured.
  **Never reached `main`:** this rule lived in a working copy only, an external
  review broke it before it reached `main`, and the published project went from
  v0.8.1 straight to v0.9.0.
  An external review of v0.8.1 (project rule 10) demonstrated that the content
  signature was looser than the three documents describing it: it kept letters,
  digits and separators standing between two digits, and dropped every other mark,
  so `+5%` cited as `-5%`, `EUR 12` as `USD 12`, `>50` as `50`, `mg/kg` as
  `mg kg`, `notable` as `not able` and `safe.` as `safe?` all balanced at exit 0 —
  20 of 33 tampered quotes in the review's own set. The model-side locate already
  refused every one of them, so the gate was the weaker of the two rules the
  project states, which is the opposite of what `docs/design.md` implied.
  **The rule is now a property rather than a list**, because a list closes the
  cases someone thought of and nothing else: *only the setting may differ.* Three
  clauses follow — nothing may be substituted; a space may be added or dropped only
  where it touches a mark, never between two letters or digits; a mark may be added
  or dropped only at the ends of the quote. `tallystick/verify.py:says_the_same`
  carries the statement and what each clause protects; `normalize` remains the one
  honest list (case, NFC, invisible characters, quotation and dash styles, runs of
  whitespace).
  **The price, measured before the change and against a threshold named before the
  measurement** (rule 15). Threshold: 0 changed verdicts on the 424 posted traces of
  the two published runs — every quote in them is a slice of its own source, so any
  change there is a new false alarm on honest data rather than a fabrication caught.
  Result on 424 traces and 11,547 claims: **0 verdicts changed, 0 claim statuses
  changed**, against v0.8.1 and against v0.8.0 alike. The reason is visible in the
  same files: of **5,615 EVIDENCE entries, 0 have a quote that differs from its
  source slice at all** — `propose/pipeline.py` writes `quoted_span` as the slice.
  So the published tables in [`docs/benchmark.md`](../docs/benchmark.md) cannot move
  under either rule change, and this is a count rather than an argument. The posted
  files are not in the repository (`docs/benchmark.md` says so); the count was run on
  the owner's copies, offline, at no cost.
  **What this does not measure.** The price falls on a recorder that writes a quote
  by other means than slicing — a thousands separator retyped, a narrow no-break
  space lost to a PDF extractor. Across every real text the repository holds, 114 of
  556 numeric tokens (20.5%) carry a separator between two digits and so are exposed
  to the rule; the number actually paying it is 0, because no path in the repository
  emits a quote that is not a slice. Exposure, not cost. The experiment that would
  measure the cost needs a recorder that quotes from logged text, and does not exist
  yet.
  **Still open**, unchanged from v0.8.1: the step named inside a cycle of claims that
  cite each other depends on the claim ids, and a recorder writing UTF-16 code units
  where the format means code points gets a verdict about its agent rather than a
  word about its offsets.

- **v0.8.3** — the gate reads a sequence, and one reading serves both halves.
  The v0.8.2 rule was written as a property in three clauses, and an instrument
  built before this change showed what no test had: those clauses closed the middle
  of a quote and left its two ends open, because the test that guarded them only
  ever probed the middle. `bench/quote_gate_corpus.py` plants each kind of change
  **in the middle of a quote and at each of its two edges**, drops every pair the
  equality fast path in `verify_entry` would have answered (counting those would be
  counting free passes — project rule 35), and runs two controls that must fail.
  **The rule is now one sentence about a sequence rather than a list of clauses**:
  a quote says what the source says when it reads as the same sequence — the words
  in order, each carrying whatever is stuck to a number in it (a sign, a currency
  mark, a percent sign, a mark standing between two digits), and the marks that end
  a sentence. One clause follows, and it is the only one: marks that end a sentence
  may be added or dropped where the quote begins or ends, but never exchanged for
  others, so `safe.` cited as `safe` balances and `safe.` cited as `safe?` does not.
  The reading itself moved to `tallystick/tokens.py`, which `verify.py` and
  `propose/pipeline.py` both import.
  **Broken by the external review that followed** (rule 10), and the entry is
  left standing with what it got wrong: the sentence above went on to claim that
  the gate could not be more forgiving than the locate "by construction", which
  was never true in either direction — `a 5 % rise` verifies against `a 5% rise`
  and the locate places no span for it — and the test named after that claim
  checked it on 27 chosen pairs. Worse, the rule kept the old habit of naming
  what counts as content: whole Unicode categories were setting, so a `-`
  standing one space from its number was neither a word nor part of one, and
  `margin - 5.2 % lower` cited as `margin 5.2 % lower` balanced at exit 0. See
  v0.8.4.
  **Measured against thresholds named before the measurement** (rule 15): refuse
  100% of tampering, accept 100% of the drift the rule is asked about. Material is
  the `quoted_span` of all 5,615 EVIDENCE entries in the posted files of the two
  published runs — 160,029 tampering pairs and 42,214 drift pairs. The rule refuses
  **100% of tampering and accepts 100% of drift** (99.0% if the 445 spaces held out
  of the drift set are counted as failures); the v0.8.1 content signature on the
  same instrument refuses 37.2% and accepts 99.3%, `always accept` fails the
  tampering side, `always refuse` fails the drift side, and the three land on three
  different points — so the candidate's 100% is a measurement rather than an
  instrument saying yes. Re-run on 20 September 2026 against the committed
  `bench/results/quote_gate_corpus.json`, which it reproduces exactly; that is
  reproducibility, not an independent check (rule 14). The posted files are not in
  the repository, and without them the instrument falls back to text the repository
  carries, so a stranger can run it on smaller material.
  **The published tables cannot move**, for the reason recorded at v0.8.2 and not
  re-measured here: every quote in those files is an exact slice of its source, and
  `verify_entry` settles an exact slice before any rule is consulted. The exposure
  recorded at v0.8.2 — a recorder that retypes a number instead of slicing it — is
  unchanged.
  **Still open**: the new rule has had no external review (rule 10); the step named
  inside a cycle of claims that cite each other still depends on the claim ids; and
  a recorder writing UTF-16 code units where the format means code points still gets
  a verdict about its agent rather than a word about its offsets.

- **v0.8.4** — the gate lists what may differ, not what may not.
  An external review (rule 10) broke v0.8.3 three days after it was written, and
  not on a case v0.8.3 had missed: on the **shape** of the rule. Four rules for
  this gate in a row named what counts as content and let the rest be noise — an
  edit budget, a signature of letters and digits, a sequence of tokens over whole
  Unicode categories — and each was broken by a reader who thought of a character
  the list had not. The review's demonstration: `Operating margin - 5.2 % against
  the prior year` cited as `Operating margin 5.2 % against the prior year`,
  `BOOKS BALANCE`, exit 0. A fifth list would have been the same move a fifth
  time, so the **owner's decision was to invert the default**: name the characters
  a quote is *allowed* to differ by, and treat every other character — including
  the ones nobody here has thought of — as content. That sentence is true of the
  list and incomplete about the gate: the list applies after
  `tallystick/normalize.py` has read both sides, and that reading already
  forgives letter case, the kind of dash, the style of a quotation mark, Unicode
  composition and invisible characters, so `Polish` cited as `polish` and `–$5`
  as `-$5` pass. Both lists in full are under
  [Known limitations](../docs/known-limitations.md#known-limitations).
  **The list is four lines** (`tallystick/tokens.py:SETTING`): whitespace, the
  comma, the two quotation marks, and a run of sentence marks where the quote
  opens or closes. Each carries the reason it is there. A run of setting that
  separates two letters or digits leaves one space behind it, which is what keeps
  `66,300` from reading as `66300` and `not able` from reading as `notable` — and,
  said rather than left to be found, what makes `66,300` read the same as `66 300`,
  which is looser than v0.8.3: the separator has to be there, which one it is is
  setting. The
  one clause that is about position rather than about a character: a sentence
  mark may be added or dropped where the quote begins or ends, never exchanged
  for another, and a full stop — and only a full stop — may follow the mark that
  closes the quoted sentence, because that one is the recorder's own.
  **What it buys, measured rather than argued.** The instrument now sweeps
  characters instead of only cases: for each of 3,565 punctuation and symbol
  codepoints it builds a quote with that character beside a space and at its end
  and cites it without. v0.8.1 let 3,550 of them be dropped without noticing,
  v0.8.3 let 140, v0.8.4 lets 0 — and 0 was named as the threshold before the
  sweep was run, beside the two that were already named. On the pair corpus,
  built from the `quoted_span` of all 5,615 EVIDENCE entries of the two published
  runs: **211,038 tampering pairs and 42,625 drift pairs, 100% refused and 100%
  accepted**, against 83.9% and 99.0% for the v0.8.3 rule and 28.2% and 98.3% for
  the v0.8.1 signature on the same instrument, with `always accept` failing the
  tampering side and `always refuse` the drift side. Every number here is from a
  run made on 20 September 2026 (`python bench/quote_gate_corpus.py --limit
  100000 --json bench/results/quote_gate_corpus.json`, the line now in the
  script's own Usage), and the JSON it writes is committed beside it.
  **Three families joined the tampering set** and v0.8.3 accepts all three at
  every site: a sign standing apart from its number (16,845 pairs), a bracket
  that carries the sign — `net income (1.2) bn` cited as `net income 1.2 bn`
  (16,845) — and a mark nobody put on any list, `5‰` cited as `5` (16,845, which
  v0.8.3 refuses and v0.8.1 accepts).
  **One class moved the other way, and it is a correction of the instrument
  rather than of the rule.** v0.8.3 held 445 spaces out of the drift set on the
  argument that dropping them makes one number out of two. Split by what they
  actually do: 474 pairs where the space keeps a sign off a digit (`1946 - 5
  July` → `1946 -5 July`) are tampering and are asserted as such — v0.8.3 accepts
  300 of them; 411 pairs where a mark with a digit behind it stays exactly where
  it was (`March 11, 2011` → `March 11,2011`, `10^3 = 1000` → `10^3= 1000`) are
  drift, because the words are still held apart by the mark that was always
  there. v0.8.3 refuses 408 of those 411, which is a false-alarm class the
  hold-out kept anyone from seeing. Nothing is held out of the count now, so the
  100% has no asterisk beside it.
  **Time re-measured** (§36), since the rule changed: one entry over the largest
  real artifact, 20,000 characters, is 5.1 ms against a ceiling of one second
  named before the fix, and doubling the input multiplies the time by 1.96 to
  2.03 over five doublings — linear, and 2.7× faster than v0.8.3's 13.9 ms.
  **The published tables cannot move**, and this was recomputed rather than
  carried: all 5,615 EVIDENCE quotes in the 424 posted files of both runs are
  exact slices of their sources, character for character, so `verify_entry`
  settles them before any rule is consulted. The count was made by a script that
  never calls `verify_entry` — 424 files, 11,547 claims, 5,615 entries, 0 that
  differ.
  **What it costs**, and it is refusals rather than passes: a colon or semicolon
  dropped, a hyphen a recorder spelled out (`state-of-the-art` cited as `state of
  the art`), a bracket dropped mid-quote — which v0.8.3 called setting — and a
  full stop read as content wherever it stands between two words, so `the U.S.
  delegation` cited as `the US delegation` is refused. None of these appears in
  42,625 drift pairs of real material; they are exposure, not cost, and the
  experiment that would measure the cost needs a recorder that quotes from logged
  text by other means than slicing.
  **Still open**: the rule has had one external review and the fix that followed
  it has had none (rule 10); git tags were still the sixth place a version lived
  and still stopped at `v0.7.4`, so a reader who wanted the code behind a
  published number had neither a tag nor a hash; the step named inside a cycle
  of claims that cite each other still depends on the claim ids; and a recorder
  writing UTF-16 code units where the format means code points still gets a
  verdict about its agent rather than a word about its offsets.

- **v0.9.0** — the gate asks where a quote was cut. Tagged after `v0.7.4`; the
  numbers v0.7.5, v0.8.0 and v0.8.1 reached `main` without a tag of their own,
  and v0.8.2 to v0.8.4 above never reached `main` at all. The release notes,
  readable on their own, are in [`CHANGELOG.md`](../CHANGELOG.md); this entry
  keeps what they leave out.
  **Why.** Every rule above compared two strings, and one forgery has nothing in
  the pair of strings to see: a trace may declare its span one character to the
  right, and `66 300 people affected` cut there is `300 people affected`, word
  for word. The equality fast path answered it before any rule was consulted.
  Rounds 3 to 9 of external review closed that shape one mark at a time — `.5%`,
  `,300`, ` 300`, `-$5.2`, `~$3`, `≤$5`, `$100-$300` — until round 9 read a
  number at the boundary as one thing (sign, currency, digits and what joins
  them, share, brackets) and asked one question of it: does the boundary fall
  inside it. The same question is asked of a word (`unsafe` cited as `safe`).
  **Measured** on `bench/quote_gate_corpus.py` over all 5,615 real quotes
  (`--limit 100000`, run on 24 September 2026): 305,499 tampering pairs and
  284,010 typographic pairs, 194,321 spans cut inside a number and 415,886
  inside a word, 1,381 values of real tool output (members of a list of
  numbers written with no space after the comma counted apart: 192 of 192
  refused); every threshold met, both
  controls failing as they must. The v0.8.1 content signature on the same pairs:
  25.23% of the tampering refused.
  **What it cost, measured against v0.8.1** (the last version on `main` before
  this one): 8 of the
  5,615 real quotes refused (one a real cut, `1/2` cited as `2`; seven named
  prices), 75 of 1,044 values of real tool output refused (7.18%, members of
  11 lists in one trajectory), 1 of 11,547 claim statuses changed on the 424
  posted traces.
  An intermediate version of round 9 refused 577 of the 1,044 (55.27%), mostly
  `key=value` pairs in URLs, because it read `=` as part of a number; the
  release does not.
  **The published tables did not move, and the old reason for that no longer
  holds.** Up to v0.8.4 this file said the tables could not move because every
  quote in the posted files is an exact slice of its source, which
  `verify_entry` settles before any rule. The boundary checks now run before
  that equality, and refuse 8 exact slices. So the tables were re-derived
  rather than argued: re-auditing the 424 posted files on v0.9.0 reproduces all
  580 sentence flags of the v0.6 rows and all 225 trajectory scores of the
  v0.7.3 rows. The model sides were not rerun.
  **The model-side proposer** asks the gate's question about a number of a span
  before placing it, and since a fix made before release asks it of the span as
  placed rather than of its core, so an exact `-$5.2 million` is placed again;
  with punctuation added around it, it is placed nowhere. It does not ask the
  question about a word: of the 5,611 spans it places on the 5,615 quotes, the
  gate refuses 4 for cutting a word. Price of the fix on the 5,615 quotes:
  5,611 placed before and after it, 0 placed differently. What the reviews
  found it still places wrongly or not at all is under Known limitations in
  `docs/known-limitations.md`. The worst of it is a class, not a case: when the model adds a full
  stop or quotation marks, the word search drops a mark at the edge of the
  quote, and where the gate does not protect that cut the forgery closes the
  books at exit 0 (`!ready` placed as `ready`, a removed diff line
  `-enable_ssl = true` as `enable_ssl = true`; 15 of 31 forms tried, 21
  before the sign check below).
  **The proposer wrote a sign the model never quoted, from v0.6.0.** The last
  review asked something no class of the instrument had asked: what the
  proposer does with a quote its source does not hold. Source `a loss of
  $5 million`, model quote `-$5 million`: the word search, to which a dash is a
  separator, placed `$5 million` and wrote it into the trace as the model's
  quote, and the audit closed the books. The same answer on b92acc6 (v0.6.0),
  5972b77, 6816b58, 1f9dacc and aa2ddfa, so this was never a regression. The
  module's own docstring promised a match on words "that forgives punctuation
  and case and never a changed character". The instrument asked 30 such quotes
  first and failed on 8, all a sign added (`-$5`, `- $5`, `–$5`, `—$5`,
  `-€5`, a minus inside a longer quote, `- 5.2 %`, `-(5%)`). A changed digit,
  a dropped sign and a reversed sign were already placed nowhere. The rule: a
  span the word search finds is placed only if the text it
  writes, read alone, states the same signs and digits as the quote read alone
  (`propose/pipeline.py:_signs_and_numerals`, the gate's reading of a number
  with a dash glued to a letter read as a hyphen). The gate is unchanged. A
  stop rule was set before measuring: if more than 1% of the 5,615 real quotes
  stopped being placed, the rule would be reverted and the finding recorded as
  open. Measured: 0 of 5,615 (5,611 placed before and after). That ruler is
  nearly blind here: every one of those quotes is an exact slice, and all but
  4 are placed before the word search is reached; the 4 are exact hits that
  cut a number, and neither version places them. The one with power is the same quotes with a full
  stop added, or quotation marks around them: 5 of 5,615 are no longer placed
  in each case. All 5 open on a list bullet before a year (`- 2017: Vittoria
  Colizza`, two sources). Before, they were placed without the bullet; read
  alone, `- ` there cannot be told from a sign. 0 of 1,044 tool values, 0 of
  11,547 statuses and 0 published rows moved; the audit side does not call the
  proposer. What it does not reach: a mark before a word (`- Paris is big.`),
  a trailing minus, accounting brackets, a dash glued to a square or a curly
  bracket (`-[5]`), a dash a space away from a bracket (`- (5%)`). These are
  the forms tried, not the class. Nor does any of these prices reach a third
  route: a quote into the model's own intermediate text is written clipped to
  the claims it falls in, not as it was checked, and a dash just before the
  claim is lost (`– (5%) this year` written as `(5%) this year`). No earlier
  round measured it; every price above was taken on the exact search and the
  word search, two of the three routes. Measured now as exposure, not as
  cost: of the 2,767 quotes the published runs write into intermediate text,
  404 would be written without the dash standing just before them (a dash not
  glued to a letter on its left) had the model quoted it, all 404 a list bullet
  at the start of a line (`docs/known-limitations.md`,
  Known limitations).
  **Time.** Checking the 424 posted traces (`verify_run` alone, best of five
  runs, one machine) takes 0.48 s on v0.9.0 against 0.052 s on v0.8.1: about
  nine times slower, about 1.1 ms a trace. v0.8.1 answered almost every quote
  by string equality; v0.9.0 reads both boundaries of every quote.
  **Still open**: everything under Known limitations in `docs/known-limitations.md`, with its
  frequency where it was counted. That list is what the external reviews had
  found by 25 September 2026, and it is not complete: each review found shapes
  the one before it had not, and the next will most likely find more; v0.9.0
  refuses what the README describes as refused, and anything else it may
  accept. The rule was frozen for this release, so none of the classes the
  external reviews found has been closed or priced. Git tags stopped at `v0.7.4`
  until `v0.9.0` was tagged at the merge.

## Earlier benchmark results

The construction, the caveats and the scoring are described in [`docs/benchmark.md`](../docs/benchmark.md); the differences these runs had from it that we know of are named in each section.

### Results — v0.5.2 (September 2026)

Built with `--limit 100` (99 traces: one selected item had fewer than two sentences and yields no trace); 99 ran, 93 are scored: 6 were dropped because at least one of their proposer runs failed (7 failed runs out of 198; both judges 0 failures). 273 final-answer sentences, 43 laundered by human annotation (constructed prevalence 15.8%; natural sentence-level prevalence in the eligible pool 6.2%). Every row is scored on the same 273 sentences. Model on every side: `claude-sonnet-4-6`, API-default sampling.

| method | precision | recall | F1 | FPR | variance |
|---|---|---|---|---|---|
| one-hop judge (summary + answer only) | 0.33 | 0.05 | 0.09 | 0.02 | 3 runs, F1 spread 0.04, 1% of sentences flip |
| full-history judge (documents + summary + answer) | 0.50 | 0.81 | 0.62 | 0.15 | 3 runs, F1 spread 0.02, 7% of sentences flip |
| tallystick (propose + audit) | 0.44 | 0.53 | 0.48 | 0.13 | 2 runs, F1 spread 0.06, 8% of sentences flip; audit of one posted file: identical |

Sample noise, separately from the model noise in the last column: `bench/ci.py` bootstraps over traces (2000 resamples of the 93, seed 0) on the committed rows. 95% intervals on F1: one-hop judge [0.01, 0.19], full-history judge [0.51, 0.71], tallystick [0.37, 0.58]. The paired difference full-history judge − tallystick is 0.14, 95% CI [0.01, 0.27]; about 1% of resamples (0.7–1.7% across seeds 0–4) put tallystick at or ahead. So "the judge beats tallystick on F1" is not sample noise, and "the one-hop judge sees nothing" holds with a wide margin.

Summary-step detection (claim level, first proposer run, the classic task): precision 0.49, recall 0.64, F1 0.55 over 542 claims. Granularity 1.11 claims per answer sentence (mean over traces and runs). Across both proposer runs on the 93 scored traces, 1697 claims were posted, 234 of them prior-only.

What the table says, in order of importance:

- **The one-hop judge cannot see laundering** — recall 0.05 on a task where the answer is verbatim, as the construction predicts. This is the number the benchmark exists to show.
- **A judge given the full history beats tallystick on F1, 0.62 to 0.48.** The gap is mostly recall. The main mechanism is that the verifier checks *where* a quote is, not *whether* it supports the claim: when the proposer attaches a real document span to a summary claim overlapping an annotated span, the audit accepts it, and every answer sentence quoting it is missed. Two further paths exist and the harness does not separate them out: a claim with several credits closes if *any one* of them closes, so an answer sentence whose summary sentence was segmented into a true clause and an invented clause passed on the true clause even when the invented one was correctly posted prior-only (found while reviewing these results; closed by entry groups in v0.5.3 — see Tradeoffs); and an answer sentence the segmenter failed to locate has no claim to fail and is never flagged. Nothing in the verdict path judges support, by design, and this is the price of that design measured for the first time.
- **The false positives come from the opposite case.** A summary sentence the proposer cannot locate verbatim (paraphrase, two passages fused) is posted prior-only, which is a failing status, so every answer sentence quoting it is flagged — whether the annotators marked it or not. That, plus the same failure one hop later (an answer claim whose summary quote the proposer did not return verbatim is itself posted prior-only), is where tallystick's FPR of 0.13 comes from — the "verbatim or nothing" tradeoff above, in numbers.
- **What tallystick buys for that price is a verdict that is a function of the file.** The audit path has no model, no randomness and no clock (a test walks the AST to enforce it), and the harness confirms per-claim statuses are identical when the posted file is re-read and audited again. So the 8% of sentences that flip between tallystick's two runs are differences between two posted files, on disk, that can be diffed; the judge's 7% cannot be attributed to anything on disk.
- Neither of the two rows that can see laundering is good enough to ship as a silent filter: both put a false flag on roughly one in seven or eight clean sentences.

### Results — v0.5.3 (September 2026)

Same command, traces rebuilt by v0.5.3's `build.py` (a line break is a sentence boundary; QA traces carry the question as a root). 100 traces built, 99 scored: 1 dropped because both proposer runs failed on it — the same trace, 885, that failed both runs at v0.5.2 (2 failed runs out of 200; 7 of 198 at v0.5.2, before the one-retry; judges 0). 290 final-answer sentences, 40 laundered (constructed prevalence 13.8%; natural 5.8%). Model on every side: `claude-sonnet-4-6`, API-default sampling. Rows: `bench/results/2026-09-n100-v0.5.3.json`.

| method | precision | recall | F1 | FPR | variance |
|---|---|---|---|---|---|
| one-hop judge (summary + answer only) | 0.12 | 0.04 | 0.06 | 0.05 | 3 runs, F1 spread 0.04, 3% of sentences flip |
| full-history judge (documents + summary + answer) | 0.45 | 0.81 | 0.57 | 0.16 | 3 runs, F1 spread 0.04, 7% of sentences flip |
| tallystick (propose + audit) | 0.38 | 0.65 | 0.48 | 0.17 | 2 runs, F1 spread 0.03, 8% of sentences flip; audit of one posted file: identical |

Bootstrap over traces (`bench/ci.py`, 2000 resamples, seed 0): 95% intervals on F1 — one-hop judge [0.00, 0.15], full-history judge [0.47, 0.67], tallystick [0.37, 0.58]. Paired difference full-history judge − tallystick: 0.10, 95% CI [−0.00, 0.21] (lower bound −0.01 to 0.00 across seeds 0–4; seed 3 gives [0.00, 0.19]); about 3% of resamples (2.3–4.2%) put tallystick at or ahead. Summary-step detection (claim level, first proposer run): precision 0.49, recall 0.62, F1 0.55 over 590 claims. Across both proposer runs on the 99 scored traces, 1815 claims were posted, 252 of them prior-only.

**What changed from v0.5.2, and what it cost.** The two runs are not the same sentences, so this is two versions of a benchmark, not one benchmark twice; with that caveat:

- **tallystick's F1 did not move (0.48 → 0.48), but its shape did.** Recall rose from 0.53 to 0.65 — 27 and 25 of 40 laundered sentences found in the two runs, against 25 and 21 of 43 — and precision fell from 0.44 to 0.38 (false flags 43 and 43 per run, against 30 and 29). The direction is what the group fix predicts — a quote now inherits the invented clause it covered (more true flags) and the paraphrased honest clause it covered (more false flags) — but the rows do not separate the fix from the rebuilt traces, and on the 84 traces whose labels are identical in both files the recall gain is smaller (0.57 → 0.63) and the false-flag rise larger (25 → 37 per run) than the aggregate suggests. The verdict got stricter in both directions, and the two effects cancelled on F1.
- **The gap to the full-history judge narrowed from 0.14 to 0.10, and is now on the edge of significance.** The judge's own F1 fell 0.62 → 0.57 with recall unchanged at 0.81 and its false-flag rate where it was (FPR 0.15 → 0.16): the precision drop (0.50 → 0.45) is mostly fewer laundered sentences in a larger scored set, not a change in how it reads them — on the 84 traces with identical labels its true and false flags are unchanged. Saying "the judge beats tallystick on F1" is still the best reading of the data; saying it is settled is not.
- **The judge is still not a gate.** Its 7% flip rate between runs at identical settings is unchanged; tallystick's audit of one posted file is byte-identical as before, and its two proposer runs now differ by 0.03 F1 (was 0.06).
- **Both rows put a false flag on roughly one in six clean sentences.** Neither is a silent filter. By design, tallystick's false flags each trace to one named place — a summary clause the proposer posted prior-only or whose quote the verifier rejected — and the posted file records the claim and the step it broke at, and since v0.6 `python bench/diagnose.py bench/work/posted bench/work/traces` tallies those reasons for a whole run; the judge's are not attributable to anything on disk.

---
Built by Katia Engalycheva, co-authored with Claude (Anthropic) | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
