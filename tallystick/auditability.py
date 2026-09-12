"""Can this trace be audited at all? A verdict before any claim is posted.

    tallystick check-trace run.json

An audit of provenance can only work on what the trace kept. Where a step
recorded nothing but the output of a tool, the audit has nothing to check:
whatever the tool said is taken as given, and if the fabrication happened
inside it - a search tool returning a model-written digest of a page the file
does not hold - no reading of the file can reach it. That is not a weakness of
any particular verifier. It is a property of the recording, and it is visible
in the recording, before a single model call is spent.

So this module answers one question with plain code and no model: **how much
of this run is a provenance audit able to look at, and what would have to be
recorded for the rest?** Nothing here judges truth, and nothing here flags a
claim; there are no claims yet.

A number, some defects, some notes
----------------------------------
The **number** is `reachable_share`: of the artifacts a chain passes through or
stops at, how many hold the model's own words rather than a tool's output -
`derived / (derived + tool_results)`. It is counted over artifacts on purpose.
A step-based fraction would measure the recorder rather than the run: one agent
turn written down as a single step (prose plus its tool results) and the same
turn written as two score differently, and on the AgentHallu traces that choice
alone moves 11% of them across any threshold. Artifacts do not move.

`document` roots are left out of the fraction entirely. Text retrieved and
stored verbatim is exactly what a trace should hold; counting it against the
recording would punish the thing being asked for.

On all 693 AgentHallu trajectories, measured with this function and no model at
all (`bench/auditability_agenthallu.py`, output in `bench/results/`): of the 443
that carry a human label, those at or above 80% have the label beyond the
audit's reach in 24 of 84 runs (29%), and those below it in 212 of 359 (59%).

Three things are true about that table and they have to be said together.

The share itself is counting: it says how much of this run the audit cannot
look at. Nothing about it is in doubt.

The banding is mostly arithmetic. Replace the human label with a step drawn at
random from the same trajectory and the same ordering appears - 11% against
45% - because a trace with more tool-only steps makes any step more likely to
be tool-only. So "below the line, more hallucinations are out of reach" is
largely a restatement of "below the line, more of everything is out of reach".

But the real labels are not the placebo. They sit at a tool boundary 236 times
where composition alone predicts 172 (1.37x, within-trace permutation
p < 0.0001), and the enrichment is largest exactly in the traces with the
highest share (24 against 8.8, 2.7x). Real hallucinations do land at tool
boundaries more often than chance puts them - which is a fact about agents,
not about this number, and it is the reason the boundary matters at all.

The 80% line is chosen on this data and not held out, and one corpus is one
corpus. So it decides nothing unless `--min-reachable` asks.

A low share is not a defect. It is the reason a later clean audit of the same
trace may mean less than it looks.

The **defects** each name something a recorder can fix, and they decide the
verdict: a step that wrote model text while declaring no inputs (the
conservation rule then has nothing to conserve, and every credit into an
earlier artifact is impossible by construction), a derived artifact no step
admits to writing, an empty artifact, one recorded only in part, more than one
answer, or none.

The **notes** are worth knowing and are nobody's bug. Two artifacts holding the
same text is the one that matters: real agents repeat their last step verbatim
as the answer - 210 of 223 trajectories in one run here - a quote can then not
be attributed to one rather than the other, and tooling that matches artifacts
*by their text* will confuse them, which is how one of this project's own runs
was withdrawn. Worth printing; not worth failing a build on, because the fix is
a change to the agent, not to the recorder.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .normalize import normalize
from .types import ArtifactKind, Run

#: The share `--min-reachable` defaults to when it is asked for. Read off the
#: AgentHallu v0.7.3 run (see above); a default, not a law, and it decides
#: nothing unless the caller passes it.
DEFAULT_MIN_REACHABLE = 0.8

#: A root shorter than this, measured the way a recorder measures it, is not
#: reported as unconsumed. The number is `TraceRecorder.min_chars`'s default in
#: `adapters/langchain.py`: that recorder deliberately refuses to match such a
#: root into a prompt - a "4" in a prompt is no evidence that this tool result
#: is what put it there - so an unconsumed short root is that rule working, not
#: a mistake. A recorder with a higher `min_chars` will see false reports here;
#: there is no way to know its setting from the trace.
_MATCHABLE_ROOT_CHARS = 20


@dataclass(frozen=True)
class Finding:
    """Something that will stop the audit doing its job, or is worth knowing.

    `code` is stable and machine-readable; `subject` names the step or
    artifact; `detail` says what to record instead - in the recorder, not here.
    """

    code: str
    subject: str
    detail: str
    fatal: bool = False


@dataclass(frozen=True)
class Auditability:
    artifacts: int = 0
    documents: int = 0
    tool_results: int = 0
    derived: int = 0
    empty_derived: int = 0
    empty_tool_results: int = 0
    root_chars: int = 0
    tool_result_chars: int = 0
    steps: int = 0
    reachable_steps: int = 0
    opaque_steps: Tuple[str, ...] = ()      # produced only tool results
    ingest_steps: Tuple[str, ...] = ()      # produced only documents
    silent_steps: Tuple[str, ...] = ()      # produced nothing
    findings: Tuple[Finding, ...] = ()
    notes: Tuple[Finding, ...] = ()
    min_reachable: Optional[float] = None

    @property
    def judged_artifacts(self) -> int:
        """What the share is taken over: model text and tool results that hold
        something. Documents are excluded - storing them verbatim is the point,
        not a shortcoming - and an artifact recorded with no content is not
        evidence of anything, so it neither lifts nor lowers the share."""
        return ((self.derived - self.empty_derived)
                + (self.tool_results - self.empty_tool_results))

    @property
    def reachable_share(self) -> Optional[float]:
        """`None` when there is nothing to take a share of, never a bare 0."""
        if not self.judged_artifacts:
            return None
        return (self.derived - self.empty_derived) / self.judged_artifacts

    @property
    def fatal(self) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.fatal)

    @property
    def verdict(self) -> str:
        """`auditable`, `partial` or `unauditable`.

        `unauditable` is reserved for a trace the audit cannot start on: no
        answer to work back from, or nothing the model wrote. `partial` means
        it will run and its silence will not mean much. Notes never decide it.
        """
        if self.fatal or not (self.derived - self.empty_derived):
            return "unauditable"
        share = self.reachable_share
        if (self.min_reachable is not None and share is not None
                and share < self.min_reachable):
            return "partial"
        return "partial" if self.findings else "auditable"

    def as_dict(self) -> Dict[str, Any]:
        share = self.reachable_share
        return {
            "verdict": self.verdict,
            "reachable_share": None if share is None else round(share, 4),
            "min_reachable": self.min_reachable,
            "artifacts": self.artifacts,
            "judged_artifacts": self.judged_artifacts,
            "empty_derived": self.empty_derived,
            "empty_tool_results": self.empty_tool_results,
            "documents": self.documents,
            "tool_results": self.tool_results,
            "derived": self.derived,
            "root_chars": self.root_chars,
            "tool_result_chars": self.tool_result_chars,
            "steps": self.steps,
            "reachable_steps": self.reachable_steps,
            "opaque_steps": list(self.opaque_steps),
            "ingest_steps": list(self.ingest_steps),
            "silent_steps": list(self.silent_steps),
            "findings": [_as_dict(f) for f in self.findings],
            "notes": [_as_dict(f) for f in self.notes],
        }


def _as_dict(f: Finding) -> Dict[str, Any]:
    return {"code": f.code, "subject": f.subject, "detail": f.detail, "fatal": f.fatal}


def check_trace(run: Run, *, min_reachable: Optional[float] = None,
                meta: Optional[Dict[str, Any]] = None) -> Auditability:
    """Report what a provenance audit of `run` will and will not be able to see.

    `min_reachable` is opt-in: pass it to make a thin recording a `partial`
    verdict (and, from the CLI, a non-zero exit); leave it out and the share is
    reported without deciding anything, which rests on defects alone.

    `meta` is the optional `_meta` block a recorder or adapter wrote; only
    `truncated`, a list of artifact ids cut to fit a prompt, is read from it.
    """
    # The same gate `close_books` uses. A trace that contradicts itself - two
    # steps claiming one output, a reference to an artifact that is not there -
    # is unreadable, not merely thin, and must not be reported on as if it were.
    run.validate()

    findings: List[Finding] = []
    notes: List[Finding] = []
    producers: Dict[str, List[str]] = defaultdict(list)
    for step in run.steps:
        for out in step.outputs:
            producers[out].append(step.step_id)

    reachable: List[str] = []
    opaque: List[str] = []
    ingest: List[str] = []
    silent: List[str] = []
    for step in run.steps:
        kinds = [run.artifacts[o].kind for o in step.outputs if o in run.artifacts]
        if not kinds:
            silent.append(step.step_id)
        elif any(not k.is_root for k in kinds):
            reachable.append(step.step_id)
            if not step.inputs:
                findings.append(Finding(
                    "undeclared_inputs", step.step_id,
                    "the step wrote model text but declares no inputs, so nothing it "
                    "wrote can ever be credited to an earlier artifact; record what "
                    "the step was given"))
            if ArtifactKind.TOOL_RESULT in kinds:
                notes.append(Finding(
                    "mixed_outputs", step.step_id,
                    "the step recorded the model's text and a tool result together; "
                    "the tool result is still a root the audit cannot see behind"))
        elif ArtifactKind.TOOL_RESULT in kinds:
            opaque.append(step.step_id)
        else:
            ingest.append(step.step_id)
    if silent:
        notes.append(Finding(
            "no_outputs", ", ".join(silent),
            "recorded as steps but produced nothing, so they are in no chain"))

    documents = tool_results = derived = 0
    empty_derived = empty_tool = 0
    root_chars = tool_chars = 0
    consumed: set = {aid for step in run.steps for aid in step.inputs}
    by_text: Dict[str, List[str]] = defaultdict(list)
    finals: List[str] = []
    for aid, art in run.artifacts.items():
        if art.kind is ArtifactKind.DOCUMENT:
            documents += 1
            root_chars += len(art.content)
        elif art.kind is ArtifactKind.TOOL_RESULT:
            tool_results += 1
            root_chars += len(art.content)
            tool_chars += len(art.content)
        else:
            derived += 1
            if art.kind is ArtifactKind.FINAL_ANSWER:
                finals.append(aid)
            if not producers.get(aid):
                findings.append(Finding(
                    "orphan_derived", aid,
                    "model-written text no step admits to producing; the audit cannot "
                    "ask what that step had in hand"))
        if not art.content.strip():
            if art.kind is ArtifactKind.TOOL_RESULT:
                empty_tool += 1
            elif art.kind is not ArtifactKind.DOCUMENT:
                empty_derived += 1
            findings.append(Finding("empty_artifact", aid, "no content recorded"))
        else:
            by_text[art.content].append(aid)
        # A root nobody took as an input is evidence the run cannot reach. The
        # LangChain recorder documents exactly this failure: when a prompt
        # reformats or truncates a document, the recorder cannot match it and
        # drops it from the step's inputs - and then every claim that rests on
        # it is reported unfunded, with nothing in the audit saying why.
        # ... but only for a root long enough that a recorder could have matched
        # it. tallystick's own LangChain recorder refuses to match anything under
        # 20 characters, on purpose: a "4" appearing in a prompt is not evidence
        # that this tool result is what put it there. Flagging those would blame
        # a recorder for a rule it is right to have.
        # Measured the way the recorder measures it - `normalize` collapses
        # whitespace runs, so "abc\n\n\n\n\n\n\n\n\n\ndefghij" is 11 characters to
        # `TraceRecorder` and 20 to `len().strip()`. Using the other one would
        # report exactly the roots the recorder is right to have skipped.
        if (art.kind.is_root and aid not in consumed and run.steps
                and len(normalize(art.content)) >= _MATCHABLE_ROOT_CHARS):
            findings.append(Finding(
                "orphan_root", aid,
                "recorded but no step declares it as an input, so nothing can ever be "
                "credited to it; if the prompt reformats or truncates documents, "
                "record the inputs the step really saw"))

    for ids in by_text.values():
        if len(ids) > 1:
            notes.append(Finding(
                "duplicate_content", ", ".join(sorted(ids)),
                "these artifacts hold the same text, so a quote cannot be attributed "
                "to one rather than another, and tooling that matches artifacts by "
                "their text will confuse them"))

    truncated = (meta or {}).get("truncated")
    if isinstance(truncated, (list, tuple, set, frozenset)):
        # a set has no order of its own; sorting keeps the report byte-stable
        for aid in (sorted(map(str, truncated))
                    if isinstance(truncated, (set, frozenset)) else truncated):
            findings.append(Finding(
                "truncated", str(aid),
                "recorded only in part, so a quote into the missing tail cannot be "
                "found and reads as unsupported rather than as unrecorded"))

    if not finals:
        findings.append(Finding(
            "no_final_answer", "-",
            "nothing is marked as the run's answer, so there is nothing to audit back "
            "from", fatal=True))
    elif len(finals) > 1:
        findings.append(Finding(
            "multiple_final_answers", ", ".join(sorted(finals)),
            "more than one artifact is marked as the run's answer; the audit cannot "
            "tell which one the user saw"))
    if run.steps and not reachable:
        findings.append(Finding(
            "no_model_text", "-",
            "no step recorded anything the model wrote; the whole run is roots and an "
            "audit of it is vacuous", fatal=True))
    if not run.steps:
        findings.append(Finding(
            "no_steps", "-", "the trace records no steps", fatal=True))

    return Auditability(
        artifacts=len(run.artifacts), documents=documents, tool_results=tool_results,
        derived=derived, empty_derived=empty_derived, empty_tool_results=empty_tool,
        root_chars=root_chars, tool_result_chars=tool_chars,
        steps=len(run.steps), reachable_steps=len(reachable),
        opaque_steps=tuple(opaque), ingest_steps=tuple(ingest),
        silent_steps=tuple(silent), findings=tuple(findings), notes=tuple(notes),
        min_reachable=min_reachable,
    )


#: `check` reads better inside this module; `check_trace` is the exported name.
check = check_trace

_HEADLINE = {
    "auditable": "AUDITABLE - no defect stands in the audit's way.",
    "partial": "PARTIAL - the audit will run, but its silence will not mean much.",
    "unauditable": "UNAUDITABLE - the audit cannot start on this trace.",
}


def _group(items: Tuple[Finding, ...], title: str, examples: int) -> List[str]:
    if not items:
        return []
    by_code: Dict[str, List[Finding]] = defaultdict(list)
    for f in items:
        by_code[f.code].append(f)
    lines = ["", title]
    for code, fs in sorted(by_code.items()):
        subjects = ", ".join(f.subject for f in fs[:examples])
        more = f" (+{len(fs) - examples} more)" if len(fs) > examples else ""
        lines.append(f"  [{code}] {subjects}{more}")
        lines.append(f"      {fs[0].detail}")
    return lines


def report(a: Auditability, *, examples: int = 5) -> str:
    """The human-readable view. Written for someone who has not read the docs:
    every number says what it is counted over, which direction is better, and
    what to do about it. `as_dict` has the rest."""
    share = a.reachable_share
    pct = "n/a" if share is None else f"{int(share * 100)}%"
    below_default = share is not None and share < DEFAULT_MIN_REACHABLE
    line = a.min_reachable
    lines = [
        f"Auditability - {a.steps} step(s), {a.artifacts} artifact(s)",
        "-" * 66,
        f"  What a chain can pass through   {a.judged_artifacts} piece(s) of text",
        f"    written by the model          {a.derived - a.empty_derived:<4} {pct:>5}  "
        "checkable: the audit can ask what it rests on",
        f"    returned by a tool            {a.tool_results - a.empty_tool_results:<4}        "
        "not checkable: a chain stops here, on trust",
        f"  Stored from outside             {a.documents} document(s), "
        f"{a.root_chars} character(s) of root text in all",
        "",
        "  Higher is better: the more of a run the model wrote down, the more of it",
        "  an audit can follow.",
        "",
    ]
    if a.verdict == "auditable" and below_default:
        lines.append(f"AUDITABLE - no defect stands in the audit's way, but only {pct} of "
                     "what a chain\npasses through is the model's own.")
    else:
        lines.append(_HEADLINE[a.verdict])
    if a.opaque_steps:
        lines += [
            f"  {len(a.opaque_steps)} step(s) recorded a tool result and nothing the model "
            "wrote, so the audit",
            "  cannot ask what happened there. Record the model's own text for each of:",
            "    " + ", ".join(a.opaque_steps),
        ]
    if below_default:
        lines += [
            "  Across AgentHallu's 443 labelled trajectories, runs below 80% had the",
            "  hallucination beyond the audit's reach 59% of the time, against 29% above",
            "  it - mostly because there is more out of reach to begin with. Read a clean",
            "  audit of a thin recording as 'nothing found here', not 'nothing there'.",
        ]
    if line is not None and share is not None and share < line:
        lines.append(f"  Below the {line:.0%} you asked for.")
    lines += _group(a.findings, "Defects a recorder can fix:", examples)
    lines += _group(a.notes, "Worth knowing (does not decide the verdict):", examples)
    return "\n".join(lines)
