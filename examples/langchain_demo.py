"""A four-step LangChain agent, recorded by tallystick with no network.

Same story as examples/laundered_summary.json, but nobody writes the trace by
hand: the recorder watches LangChain and writes it. The model is a FakeListLLM
so this runs offline; swap in a real model and nothing else changes.

    python examples/langchain_demo.py            # writes raw_langchain.json
    tallystick propose raw_langchain.json -o posted.json
"""

from __future__ import annotations

from typing import List

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import tool

from tallystick.adapters.langchain import TraceRecorder

FILING = ("Northwind Systems reported revenue of $412.6 million for the second "
          "quarter, an increase of 14% year over year. Operating margin was 11.2%, "
          "down from 12.9% in the prior-year period. The company did not provide "
          "guidance for the fourth quarter.")


class FilingRetriever(BaseRetriever):
    """Stands in for a vector store: always returns the Q2 filing."""

    def _get_relevant_documents(self, query: str, *,
                                run_manager: CallbackManagerForRetrieverRun,
                                ) -> List[Document]:
        return [Document(page_content=FILING, metadata={"source": "northwind_q2.pdf"})]


@tool
def headcount(company: str) -> str:
    """Look up current headcount for a company."""
    return '{"company": "northwind", "quarter": "Q2", "headcount": 2840, "change_qoq": -0.06}'


SUMMARY = ("Revenue rose 14% year over year in the second quarter. Operating margin "
           "fell to 11.2%. Headcount stands at 2840. Management expects the fourth "
           "quarter to be the strongest on record.")
ANSWER = ("Revenue rose 14% year over year in the second quarter. Operating margin "
          "fell to 11.2%. Management expects the fourth quarter to be the strongest "
          "on record.")


def build_and_run(recorder: TraceRecorder) -> str:
    config = {"callbacks": [recorder]}
    llm = FakeListLLM(responses=[SUMMARY, ANSWER])

    docs = FilingRetriever().invoke("Northwind Q2 results", config=config)
    hc = headcount.invoke({"company": "northwind"}, config=config)

    summary_prompt = ("Summarise the following for an analyst.\n\nFILING:\n"
                      + docs[0].page_content + "\n\nHEADCOUNT:\n" + hc)
    summary = llm.invoke(summary_prompt, config=config)

    answer_prompt = ("Answer the user's question using only this summary.\n\n"
                     + summary + "\n\nQuestion: How did Northwind do in Q2?")
    return llm.invoke(answer_prompt, config=config)


if __name__ == "__main__":
    rec = TraceRecorder()
    answer = build_and_run(rec)
    path = rec.save("raw_langchain.json")
    print(f"agent answered: {answer!r}")
    print(f"recorded {len(rec.artifacts)} artifacts, {len(rec.steps)} steps -> {path}")
    print("next: tallystick propose raw_langchain.json -o posted.json")
