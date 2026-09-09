"""LangChain callback handler that records a raw tallystick trace.

How LangChain becomes a ledger
------------------------------
LangChain fires callbacks as an agent works. Three of them carry everything an
audit needs:

  on_retriever_end   documents came back        -> root artifacts (document)
  on_tool_end        a tool returned something   -> root artifact  (tool_result)
  on_llm_end         a model wrote text          -> derived artifact (intermediate)

The one thing no framework logs is *what a model call could see*. We recover it
from the only honest source: the prompt. When a model starts, we keep its prompt
text; when it ends, every artifact recorded so far whose content appears verbatim
in that prompt is an input of the step. Not "was probably in context" - literally
present in the bytes the model was given. That is the conservation rule of the
verifier, applied at recording time.

The last model output is marked final_answer when the recorder is closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "the LangChain adapter needs langchain-core: pip install 'tallystick[langchain]'"
    ) from exc

from ..normalize import normalize


class TraceRecorder(BaseCallbackHandler):
    """Attach to any LangChain run via `config={"callbacks": [recorder]}`."""

    def __init__(self, min_chars: int = 20):
        super().__init__()
        self.artifacts: List[Dict[str, Any]] = []
        self.steps: List[Dict[str, Any]] = []
        self._prompts: Dict[UUID, str] = {}
        self._tool_names: Dict[UUID, str] = {}
        self._counts = {"doc": 0, "tool": 0, "llm": 0,
                        "retrieve": 0, "tool_step": 0, "generate": 0}
        self._last_llm: Optional[str] = None
        # Root artifacts shorter than this are not matched into prompts: a
        # three-word tool result would "appear" in almost any prompt by
        # coincidence. Derived artifacts (model outputs) are always matched: a
        # later prompt containing a model's earlier sentence verbatim is exactly
        # the provenance link the audit needs, however short the sentence.
        self.min_chars = min_chars

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _next(self, prefix: str) -> str:
        self._counts[prefix] += 1
        return f"{prefix}_{self._counts[prefix]}"

    def _add_artifact(self, aid: str, kind: str, content: str, title: str = "") -> None:
        self.artifacts.append({
            "artifact_id": aid, "kind": kind, "title": title, "content": content,
        })

    def _seen_in(self, prompt: str) -> List[str]:
        """Ids of recorded artifacts whose content is verbatim in `prompt`.

        Longest artifact first (by normalised length, since matching is on
        normalised text), and each match reserves its region of the prompt before
        the next is tried. So a document nested inside another is credited only when
        it appears on its own, and identical documents are credited once - the
        most recently recorded one, so a recorder reused across runs credits the
        current run's copy. An input is text the model was given as such.
        """
        norm_prompt = normalize(prompt)
        candidates = []
        for idx, a in enumerate(self.artifacts):
            needle = normalize(a["content"])
            if not needle:
                continue
            is_root = a["kind"] in ("document", "tool_result")
            if is_root and len(needle) < self.min_chars:
                continue
            candidates.append((-len(needle), -idx, idx, a["artifact_id"], needle))
        taken: List[tuple] = []          # regions of the prompt already credited
        seen = []
        for _, _, idx, aid, needle in sorted(candidates):
            start = 0
            while True:
                i = norm_prompt.find(needle, start)
                if i < 0:
                    break
                j = i + len(needle)
                if not any(a < j and i < b for a, b in taken):
                    taken.append((i, j))
                    seen.append((idx, aid))
                    break
                start = i + 1
        return [aid for _, aid in sorted(seen)]   # recording order

    def reset(self) -> None:
        """Forget everything. One recorder per agent run is the intended use; call
        this (or make a new recorder) between runs, or their ledgers merge."""
        self.artifacts.clear()
        self.steps.clear()
        self._prompts.clear()
        self._tool_names.clear()
        self._last_llm = None
        for k in self._counts:
            self._counts[k] = 0

    @staticmethod
    def _text_of(output: Any, text_only: bool = False) -> str:
        """Best-effort text of whatever LangChain hands us.

        Chat content in langchain-core 1.x is often a list of blocks. Text blocks
        are joined as text, never JSON-dumped, or every newline would become a
        literal backslash-n and nothing multi-line would ever match a prompt.
        Other blocks (search_result, json, tool_use, ...) are JSON-dumped exactly as
        LangChain itself stringifies them, so an artifact and its later appearance
        in a prompt get the same string. `text_only` drops those instead - used for
        a model's *own* output, where a tool_use block is a call, not a claim.
        """
        if output is None:
            return ""
        if isinstance(output, str):
            return output
        if isinstance(output, (bytes, bytearray)):
            return bytes(output).decode("utf-8", errors="replace")
        page = getattr(output, "page_content", None)
        if isinstance(page, str):
            return page
        content = getattr(output, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            output = content
        if isinstance(output, list):
            parts = []
            for block in output:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif text_only:
                    continue
                else:
                    parts.append(TraceRecorder._text_of(block))
            return "\n".join(p for p in parts if p)
        try:
            return json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(output)

    # ------------------------------------------------------------------ #
    # retrieval -> root documents
    # ------------------------------------------------------------------ #

    def on_retriever_end(self, documents: Sequence[Any], *, run_id: UUID,
                         **kwargs: Any) -> None:
        ids = []
        for doc in documents:
            text = getattr(doc, "page_content", None) or self._text_of(doc)
            if not text.strip():
                continue
            aid = self._next("doc")
            meta = getattr(doc, "metadata", {}) or {}
            title = str(meta.get("source") or meta.get("title") or "")
            self._add_artifact(aid, "document", text, title)
            ids.append(aid)
        if ids:
            self.steps.append({
                "step_id": self._next("retrieve"), "kind": "retrieve",
                "inputs": [], "outputs": ids,
            })

    # ------------------------------------------------------------------ #
    # tools -> root tool results
    # ------------------------------------------------------------------ #

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, *,
                      run_id: UUID, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._tool_names[run_id] = f"{name}({input_str})"

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        title = self._tool_names.pop(run_id, "tool")
        text = self._text_of(output)
        if not text.strip():
            return
        aid = self._next("tool")
        self._add_artifact(aid, "tool_result", text, title)
        self.steps.append({
            "step_id": f"tool_step_{self._counts['tool_step'] + 1}", "kind": "tool",
            "inputs": [], "outputs": [aid],
        })
        self._counts["tool_step"] += 1

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._tool_names.pop(run_id, None)

    # ------------------------------------------------------------------ #
    # model calls -> derived artifacts, inputs recovered from the prompt
    # ------------------------------------------------------------------ #

    def _generation_text(self, response: Any) -> str:
        """Text of the first generation, from its text blocks only.

        Chat messages may carry a list of blocks; only "text" blocks are the
        model's prose. A tool_use block is a call, not a claim, and is skipped.
        Blocks are joined with newlines, the same way _text_of joins them when
        the message later appears in a prompt, so the artifact matches itself.
        """
        try:
            gen = response.generations[0][0]
        except (AttributeError, IndexError, TypeError):
            return ""
        message = getattr(gen, "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, list):
            return self._text_of(content, text_only=True)
        if isinstance(content, str):
            return content
        return getattr(gen, "text", "") or ""

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], *,
                     run_id: UUID, **kwargs: Any) -> None:
        self._prompts[run_id] = "\n".join(prompts)

    def on_chat_model_start(self, serialized: Dict[str, Any], messages: List[List[Any]],
                            *, run_id: UUID, **kwargs: Any) -> None:
        parts = []
        for thread in messages:
            for m in thread:
                parts.append(self._text_of(getattr(m, "content", m)))
        self._prompts[run_id] = "\n".join(parts)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        prompt = self._prompts.pop(run_id, "")
        text = self._generation_text(response)
        # Empty, blocked, or tool-call-only completions produce no text and
        # therefore no artifact. Recording a repr of the result object here would
        # hand the proposer a "final answer" nobody wrote.
        if not text.strip():
            return
        aid = self._next("llm")
        inputs = self._seen_in(prompt)
        self._add_artifact(aid, "intermediate", text, f"model output (step {aid})")
        self.steps.append({
            "step_id": self._next("generate"), "kind": "generate",
            "inputs": inputs, "outputs": [aid],
        })
        self._last_llm = aid

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._prompts.pop(run_id, None)

    # ------------------------------------------------------------------ #
    # output
    # ------------------------------------------------------------------ #

    def run(self, final_artifact_id: Optional[str] = None) -> Dict[str, Any]:
        """The raw trace. The final answer is the last model output unless told
        otherwise. Nothing in here is a claim or an entry; `tallystick propose`
        writes those."""
        final = final_artifact_id or self._last_llm
        known = {a["artifact_id"] for a in self.artifacts}
        if final is None:
            raise ValueError("no model output was recorded, so there is no final "
                             "answer to audit; pass final_artifact_id explicitly")
        if final not in known:
            raise ValueError(f"final_artifact_id {final!r} is not a recorded "
                             f"artifact; known: {sorted(known)}")
        artifacts = [dict(a) for a in self.artifacts]
        for a in artifacts:
            if a["artifact_id"] == final:
                a["kind"] = "final_answer"
        return {
            "_comment": "raw trace recorded by tallystick.adapters.langchain.TraceRecorder",
            "artifacts": artifacts,
            "steps": [dict(s) for s in self.steps],
        }

    def save(self, path: str | Path, final_artifact_id: Optional[str] = None) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.run(final_artifact_id), indent=2,
                                   ensure_ascii=False), encoding="utf-8")
        return path
