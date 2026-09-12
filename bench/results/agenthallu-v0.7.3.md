# tallystick on AgentHallu (real agent trajectories, human step labels)
Selection `factual`: 115 hallucinated trajectories, 110 clean from the same frameworks. Proposer `claude-sonnet-4-6`, one run, credits on demand from the final answer. tallystick 0.7.3.

| set | n | any final claim flagged | earliest break = labelled step | labelled step among the breaks | within one step |
|---|---|---|---|---|---|
| hallucinated, label in model text (reachable) | 54 | 31/54 (57%) | 8/54 (15%) | 8/54 (15%) | 11/54 (20%) |
| hallucinated, label at a tool-only step (beyond the audit's boundary) | 61 | 7/61 (11%) | — | — | — |
| clean | 110 | 38/110 (35%) (false alarms) | — | — | — |

Reachable rows by AgentHallu sub-category:

| category / sub-category | n | flagged | exact step | label among breaks | within one |
|---|---|---|---|---|---|
| Planning Hallucination / Fact Derive | 34 | 18 | 4 | 4 | 4 |
| Reasoning Hallucination / Factual Reasoning | 11 | 5 | 3 | 3 | 4 |
| Retrieval Hallucination / Context Misalign | 3 | 2 | 1 | 1 | 1 |
| Retrieval Hallucination / Query Misalign | 1 | 1 | 0 | 0 | 1 |
| Retrieval Hallucination / Summarize Misalign | 5 | 5 | 0 | 0 | 1 |

The label names the step that made the answer wrong; the audit names the step where the chain from the answer to a root breaks. Where the labelled step is a tool call and its result only, the hallucination is inside a tool result - a root for this audit, with no page in the file to check it against - and those rows are reported apart, not as misses; a flag on them is a flag on something else in the run. AgentHallu's own best model judge localises the step in 41.1% of cases over all categories (Liu et al., 2026); that figure is over a different set and is not comparable to any cell above without that caveat.

15 short final answer(s) the segmenter returned no locatable claim for were posted whole as one claim, so that an answer of a bare number is audited rather than passed for having nothing to audit.

3 trajectory(ies) had no final-answer claim posted (the segmenter returned none that could be located, and the answer was not a bare value) and count as not flagged: 2 reachable, 1 beyond the boundary, 0 clean.

3 trajectory(ies) failed in the proposer and are excluded.
