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
pip install git+https://github.com/shipwithkatia/tallystick   # PyPI release follows v0.2
tallystick run.json                # exit 0 = books balance, 1 = they don't, 2 = malformed trace
tallystick run.json --chain <id>   # full provenance chain for one claim
tallystick run.json --json out.json
```

```python
from tallystick import audit

balance = audit("run.json")
assert balance.books_balance       # drop straight into a test suite

balance.coverage                   # share of final claims that trace to a root
balance.laundering_rate            # share that cite something real but unfunded
balance.injection_points()         # failing claims, each with the step that broke
```

The trace format is plain JSON — artifacts, steps with inputs/outputs, claims by character span, entries — documented in `tallystick/io.py`. `examples/laundered_summary.json` is a complete, commented one.

## How It Works

**Artifacts** are either roots (documents, tool results — text that entered the run from outside) or derived (summaries, plans, memory writes, the answer — text a model wrote). A credit against a root terminates a chain. A credit against a derived artifact is a promissory note: the claims of *that* artifact covering the cited span must be redeemed in turn, recursively.

**The conservation rule** is the gate that makes this a trace auditor rather than another span matcher: a step may only cite artifacts it received as inputs. Reachability is checked without reading any text.

**Two rules keep the walk honest.** A citation into a derived artifact inherits *every* claim it covers — quoting the whole summary does not launder the one bad sentence in it. And the cited span must be fully accounted for by claims: text nobody vouched for cannot fund anything.

**Determinism is enforced, not promised.** `tests/test_no_model_imports.py` walks the AST of every module and fails if anything imports an SDK, an HTTP client, `random`, `time`, `uuid`, or dynamic import machinery. Another test shuffles every array in the trace and asserts the balance is unchanged. The verdict path has zero dependencies.

## Tradeoffs and Decisions

- **All-of over any-of when a citation covers several upstream claims.** The first version marked a claim grounded if *any* claim under the cited span was grounded. Review showed that a wider, lazier citation (the whole summary instead of one sentence) then walked straight past the invented sentence — sloppiness evaded detection. Requiring every covered claim to close is stricter and occasionally flags a fine answer, but it makes the wide citation inherit the worst thing it covers, which is the correct bookkeeping.
- **No model in the verdict path, even where one would help.** Segmenting text into claims and proposing entries are model tasks by nature; they are deliberately outside the core (roadmap v0.2) so the verdict stays reproducible byte for byte. The cost is that v0.1 audits a trace you hand it rather than raw agent output.
- **No tolerance floor on quote matching.** A 2% edit tolerance for typographic drift with a minimum of one edit meant `14%` could be cited as `44%` and pass. The floor is gone; a three-character quote gets no free edit.
- **An empty trace does not balance.** `all([])` is `True` in Python, so a truncated trace originally exited 0. A gate that passes on missing input is not a gate.
- **What I'd do differently:** check the package name on PyPI before writing a line. The project started as `pacioli`; someone else shipped an unrelated AI-trust library under that name four days before this was scaffolded.

## What I Learned

- The interesting failure is not the fabricated sentence — it's the perfectly honest step that copies it. Each hop can be individually correct while the whole chain is wrong, and only a transitive walk sees that.
- "Deterministic" is a property you have to defend structurally. Memoising a result computed while a cycle was open made the verdict depend on array order in the JSON file. Same run, same code, different exit code. The fix was to refuse to cache anything tainted by an open cycle, and the shuffle test now exists so it cannot regress silently.
- A README that overstates what the code does is the single most expensive defect in a project whose thesis is "unsupported claims should be named". The adversarial review pass caught seven such lines before publish.

## Next Steps

- [ ] v0.2 — model-side proposers: sentence segmentation and entry proposal via structured output, kept outside the verdict path
- [ ] v0.3 — adapters: LangChain / LlamaIndex callback traces, Claude Citations ingestion as pre-verified credits, OpenTelemetry span reader
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

Citations grounds one hop and does it well; the plan is to ingest those citations as pre-verified credits and spend effort on the hops citations cannot see. The 2026 survey [*From Agent Traces to Trust*](https://arxiv.org/abs/2606.04990) lists claim-level provenance and error propagation through execution chains as open problems; this is a shipped, tested answer to a narrow slice of both.

## Built With

- Python 3.10+, standard library only
- pytest

## Why a tally stick

An Exchequer tally was a stick notched with an amount and split lengthwise. Payer and payee each kept a half. To settle, the halves were fitted back together and the notches and the grain had to line up. No clerk's opinion was involved, and a forged half simply did not fit.

## License

MIT

---
Built by Katia Engalycheva | [GitHub](https://github.com/shipwithkatia) | [LinkedIn](https://www.linkedin.com/in/katiaengalycheva/)
