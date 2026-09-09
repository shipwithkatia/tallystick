# tallystick

Provenance accounting for LLM agent runs: every claim in the final answer is traced back, hop by hop, to something outside the model — or named, together with the step that invented it.

## The Problem

Teams shipping multi-step agents (retrieve → tool call → summarise → answer) check the answer against its sources with a citation checker, RAGAS faithfulness, or an LLM judge. All of those look at **one hop**: does the answer quote its immediate context?

But the context was usually written by a model too. When a summariser invents a sentence and the answer quotes that summary verbatim, every one-hop check reports 100% grounded. The fabrication acquired the appearance of support by passing through a step that copied it faithfully. Anyone debugging an agent that "cites everything and is still wrong" hits this, and today the answer is a score, not a location.

## The Solution

`tallystick` treats an agent run as a set of books. Each claim is a debit; each piece of evidence is a credit. An entry is verified by three deterministic gates — the cited artifact exists, the step that wrote the claim actually *had* it as an input, and the quoted text is really at that address. Then the chain is walked backwards until it lands on a **root** artifact (a document, a tool result) or breaks.

Where it breaks is the injection point. The output is a step number, not a faithfulness score. No model is asked whether anything is faithful.

## Demo

```
$ tallystick examples/laundered_summary.json

Trial balance — 3 claims in the final answer
──────────────────────────────────────────────────────────
  grounded       2  ████████████░░░░░░  67%
  laundered      1  ██████░░░░░░░░░░░░  33%
──────────────────────────────────────────────────────────
  coverage         66.7%
  laundering rate  33.3%   <- invisible to one-hop checks

1 claim(s) did not close:

  [LAUNDERED] ans_3: Management expects the fourth quarter to be the strongest on record.
      introduced at step s3 (summarize) — model prior, no external evidence
      └─ EVIDENCE:summary#113-181

BOOKS DO NOT BALANCE
```

In that example every sentence of the answer quotes the summary verbatim, so a one-hop checker passes all three. The third sentence was invented at step s3; the filing it came from explicitly says no guidance was given.

Two terms, since they appear above before they are explained. A **posted trace** is a run with its claims and evidence entries already written in — by hand, or by the model-side proposer below. The **trial balance** is the per-claim verdict over the final answer: `grounded` traces to something outside the model, `laundered` cites something real whose own support is missing further back, `unsupported` cites nothing.

```
$ tallystick examples/laundered_summary.json --chain ans_3

ans_3 [laundered] depth=1
  "Management expects the fourth quarter to be the strongest on record."

  1. ans_3 @ step s4
     credit EVIDENCE:summary#113-181  [intermediate]
     quote  "Management expects the fourth quarter to be the strongest on record."

  chain breaks at step s3 on claim sum_4: model prior, no external evidence
  that claim reads: "Management expects the fourth quarter to be the strongest on record."
  and nothing outside the model funds it.
```

## How to Use

```bash
git clone https://github.com/shipwithkatia/tallystick && cd tallystick
pip install -e ".[propose,langchain]"   # plain `pip install -e .` gives the audit path only

# 1. Get a raw trace (artifacts + steps, no claims yet). Either record a
#    LangChain run — or write the JSON by hand, as examples/raw_research_run.json.
python examples/langchain_demo.py          # a 4-step agent, recorded -> raw_langchain.json

# 2. Let a model post the books: writes a posted trace, then audits it.
#    Needs ANTHROPIC_API_KEY.
tallystick propose raw_langchain.json -o posted.json

# 3. Audit a posted trace. Deterministic, offline, no SDK needed.
tallystick posted.json                 # exit 0 = balance, 1 = don't, 2 = could not run
tallystick posted.json --chain <id>    # full provenance chain for one claim
tallystick posted.json --json out.json # machine-readable balance
tallystick posted.json --quiet         # exit code only
tallystick examples/balanced_run.json  # what a passing gate looks like: exit 0

# Same propose path with canned answers and no network — what CI runs.
tallystick propose examples/raw_research_run.json -o posted.json \
  --proposer fake --script examples/fake_answers.json
```

```python
from tallystick.adapters.langchain import TraceRecorder

rec = TraceRecorder()                                 # one recorder per agent run
chain.invoke(question, config={"callbacks": [rec]})   # any LangChain runnable
rec.save("raw.json")                                  # then: tallystick propose raw.json
```

Exit code 2 covers everything that stops the audit from running — a malformed trace, a missing SDK or key, a proposer that crashed. A bad API key must never read as "books do not balance".

The proposer's output is a file. Two proposer runs may differ; two audits of the same file never do. That boundary is physical: `tallystick/propose/` is the only package allowed to import an SDK, and nothing in the verdict path may import `propose/` by any spelling (`cli.py` may, lazily, inside the `propose` subcommand only). A test walks the AST and fails the build on either.

```python
from tallystick import audit

balance = audit("run.json")
assert balance.books_balance       # drop straight into a test suite

balance.coverage                   # share of final claims that trace to a root
balance.laundering_rate            # share that cite something real but unfunded
balance.injection_points()         # failing claims, each with the step that broke
```

The trace format is plain JSON — artifacts, steps with inputs/outputs, claims by character span, entries — documented in `tallystick/io.py`. `examples/laundered_summary.json` is a complete posted trace; `examples/raw_research_run.json` is the same run before posting; `examples/balanced_run.json` is the same run with an honest summariser, and it balances. A `Run` can also be built in Python from the exported `Artifact`, `Step`, `Claim` and `Entry` types; it gets the same validation as a file.

## How It Works

**Artifacts** are either roots (documents, tool results — text that entered the run from outside) or derived (summaries, plans, memory writes, the answer — text a model wrote). A credit against a root terminates a chain. A credit against a derived artifact is a promissory note: the claims of *that* artifact covering the cited span must be redeemed in turn, recursively.

**The conservation rule** is the gate that makes this a trace auditor rather than another span matcher: a step may only cite artifacts it received as inputs. Reachability is checked without reading any text.

**The recorder recovers inputs from the prompt.** No framework logs what a model call could see, so the LangChain adapter takes the only honest source: when a model starts, its prompt is kept; when it ends, every artifact recorded so far whose content appears verbatim in that prompt is an input of the step. "Was retrieved earlier" is not "was seen". Matching is longest-first with each match blanked out, so a document nested inside another is credited only when it appears on its own, and duplicates are credited once. Root artifacts under 20 characters are never matched (a three-word tool result would appear in any prompt by coincidence); model outputs always are. If a framework reformats a document before prompting — truncates it, re-wraps it — the recorder will not find it, the step gets no input, and its claims fail reachability. That fails closed, on purpose, and is the adapter's main limitation.

**Two rules keep the walk honest.** A citation into a derived artifact inherits *every* claim it covers — quoting the whole summary does not launder the one bad sentence in it. And the cited span must be fully accounted for by claims: text nobody vouched for cannot fund anything.

**The proposer is a pointer generator, not a judge.** It is asked for claims and quotes as verbatim substrings of text it was shown. Each one is then *located* by exact substring search; whatever cannot be located is logged in the posted file and dropped. A claim with no locatable support is posted as `PRIOR:model`, which never funds anything, and a quote into text no claim vouches for is refused — so an invented sentence fails closed downstream, whether or not the segmenter picked it up. The model never answers "is this faithful?"; it only answers "where?", and the answer is checked.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every verdict-path module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping.
- **The model proposes into a file; the verdict reads the file.** Segmenting text into claims and proposing credits are model tasks by nature. Putting them in a separate package with a materialised output means the audit stays reproducible byte for byte and the model's work can be inspected, diffed and re-run without touching the verdict. The cost is one extra artefact on disk and one more command.
- **Verbatim or nothing on the model side.** The proposer could have been allowed to paraphrase. That would have raised recall and quietly reintroduced a judgement call into the credit. Every span the proposer posts is located exactly (capitalisation at most); paraphrase is dropped and logged. The verifier, which also audits hand-written traces, separately allows 2% edit distance for typographic drift with no floor — two rules, both stated.
- **Credits into a summary are resolved by containment, not by trimming.** The segmenter is told to skip meta text ("In summary,"); a quote that straddles such text would otherwise cover characters no claim owns, and the audit would report laundering that isn't there. The first fix trimmed the quote to whatever claims it touched — and review showed that a quote clipping two characters of a real claim was then rewritten into those two characters and *counted*. The rule now has no thresholds: a quote funds a claim only if one contains the other. A quote inside a claim is kept; a claim inside a quote is kept (one credit per whole claim, meta text discarded and logged); a partial straddle is refused as ambiguous.
- **No tolerance floor on quote matching.** A 2% edit tolerance for typographic drift with a minimum of one edit meant `14%` could be cited as `44%` and pass. The floor is gone; a three-character quote gets no free edit.
- **An empty trace does not balance.** `all([])` is `True` in Python, so a truncated trace originally exited 0. A gate that passes on missing input is not a gate.
- **Exit 2 is a hard boundary.** Seven ordinary malformed-JSON shapes originally escaped the loader as `KeyError`/`TypeError` and reached the shell as exit 1 — "your agent is unfaithful" — when the truth was "I could not read your file". The loader now validates every shape and raises one typed error, and the same validation runs on a `Run` built by hand.


## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it. Each hop can be individually correct while the whole chain is wrong, and only a transitive walk sees that.
- "Deterministic" is a property you have to defend structurally. Memoising a result computed while a cycle was open made the verdict depend on array order in the JSON file. Same run, same code, different exit code. The fix was to refuse to cache anything tainted by an open cycle, and the shuffle test now exists so it cannot regress silently.
- A README that overstates what the code does is the single most expensive defect in a project whose thesis is "unsupported claims should be named". The adversarial review pass caught seven such lines before publish.
- **Writing and attacking are different jobs, and the same pass cannot do both.** Every serious defect in this repo was found not while writing but in a separate adversarial review run afterwards, by a reviewer whose only brief was to break it: the wide-citation bypass and the array-order dependence in v0.1; in v0.2, a false `laundered` verdict on a model that had followed the prompt correctly, a string-shaped answer that would have posted one claim per character, and three ways to sneak a model import past the boundary test. None of them were visible from inside the writing.
- **A framework tells you what happened, not what a model saw.** LangChain logs retrievals, tool calls and model outputs, but nothing in it says which of those a given model call had in its context. The recorder recovers that from the prompt bytes, and the first review showed how easy it is to get subtly wrong: a 20-character floor meant for tiny tool results was also dropping short model outputs from the chain, and chat "content blocks" were being JSON-escaped so no multi-line document ever matched. Both mislocated the injection step — the one number the project promises — while every test stayed green, because the demo happened to use one long paragraph.
- **A fix needs its own review.** The fix for the false `laundered` verdict passed its tests and was wrong in both directions — it still failed when the meta text sat *between* two claims, and it turned a mostly-unvouched quote into a two-character credit that counted. The tests I wrote for the fix tested the case I had in mind, not the cases I had not. A third pass, scoped to the fixes only, caught it. The rule this repo now follows: nothing ships without a second pass whose job is to make the first one look bad, and fixes get the same treatment as the code they fix.

## Next Steps

- [x] v0.2 — model-side proposers, kept outside the verdict path
- [x] v0.3 — LangChain callback recorder
- [ ] v0.3.x — LlamaIndex, Claude Citations ingestion as pre-verified credits, OpenTelemetry span reader
- [ ] v0.4 — HTML ledger view: the answer colour-coded by status, click a sentence to unfold its chain to the root
- [ ] v0.5 — benchmark: laundering detection on multi-step traces derived from RAGTruth, against one-hop baselines; one number, one command
- [ ] PyPI release

## Where This Sits

| | one-hop grounding | across steps | deterministic | names the step |
|---|---|---|---|---|
| Anthropic Citations API | ✅ documents only | ❌ | ✅ | ❌ |
| RAGAS faithfulness | ✅ | ❌ | ❌ | ❌ |
| LLM-as-judge | ✅ | ❌ | ❌ | ❌ |
| LEDGER ([arXiv 2608.18398](https://arxiv.org/abs/2608.18398)) | ✅ | ✅ | ❌ *(authors' own caveat)* | partly |
| **tallystick** | ✅ | ✅ | ✅ | ✅ |

Rows for other tools are my reading of their public documentation as of September 2026, not benchmark results; the benchmark is roadmap item v0.5.

Citations grounds one hop and does it well; the plan is to ingest those citations as pre-verified credits and spend effort on the hops citations cannot see. The 2026 survey [*From Agent Traces to Trust*](https://arxiv.org/abs/2606.04990) lists claim-level provenance and error propagation through execution chains as open problems; this is a shipped, tested answer to a narrow slice of both.

## Built With

- Python 3.10+; the verdict path is standard library only
- `anthropic` SDK, optional, for the proposer
- `langchain-core`, optional, for the recorder
- pytest

## Why a tally stick

An Exchequer tally was a stick notched with an amount and split lengthwise. Payer and payee each kept a half. To settle, the halves were fitted back together and the notches and the grain had to line up. No clerk's opinion was involved, and a forged half simply did not fit.

## License

MIT

---
Built by Katia Engalycheva | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
