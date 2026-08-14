"""Starter labeled retrieval set for the eval harness.

The RAG plan calls for ~200-500 hand-labeled queries against real ingested
documents. This is deliberately **not** that — building a set that size
means curating real documents and having a human label relevant chunks
per query, which is a data-curation task, not something to fabricate here.
What this is: ~20 hand-built queries against a small set of synthetic
"documents" (each already split into its final chunk form, with an id),
covering both categories the plan calls out (§Evaluation harness, point 9)
— text→text and text→image (via a chart/table's caption, since that's what
a lexical/rerank pass actually sees for a visual candidate — see
``backends/local.py::query()``'s docstring on why). Enough to exercise every
metric correctly end-to-end and catch a gross regression; not enough to
trust a specific score. See ``test_retrieval_eval.py``'s module docstring
for how to grow this toward the plan's real target size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from substrate.kernel.storage.vector import Document


@dataclass(slots=True)
class EvalQuery:
    query: str
    relevant_doc_ids: set[str]
    category: Literal["text", "image"] = "text"


@dataclass(slots=True)
class EvalDataset:
    documents: list[Document]
    queries: list[EvalQuery]


def build_starter_dataset() -> EvalDataset:
    docs: dict[str, Document] = {}

    def doc(doc_id: str, text: str, *, is_image_candidate: bool = False) -> Document:
        d = Document.from_text(
            text,
            metadata={"eval_doc_id": doc_id, "is_image_candidate": is_image_candidate},
        )
        docs[doc_id] = d
        return d

    documents = [
        doc(
            "revenue-q3",
            "Quarterly revenue grew 20 percent year over year to $500 million, "
            "driven by strong demand in the enterprise segment.",
        ),
        doc(
            "revenue-chart-caption",
            "[chart] Bar chart: quarterly revenue by segment, Q1-Q4, in millions of dollars.",
            is_image_candidate=True,
        ),
        doc(
            "headcount-q3",
            "Total headcount reached 4,200 employees at the end of the third quarter, "
            "up from 3,800 a year earlier.",
        ),
        doc(
            "headcount-chart-caption",
            "[chart] Line chart: employee headcount growth over the last eight quarters.",
            is_image_candidate=True,
        ),
        doc(
            "risk-factors",
            "Risk factors include foreign currency fluctuations, supply chain "
            "disruptions, and increased competition in the cloud infrastructure market.",
        ),
        doc(
            "weather-unrelated",
            "The weather in San Francisco was sunny with a high of 65 degrees Fahrenheit.",
        ),
        doc(
            "balance-sheet-table-caption",
            "[table] Condensed consolidated balance sheet: total assets, "
            "liabilities, and stockholders equity as of period end.",
            is_image_candidate=True,
        ),
        doc(
            "cash-flow",
            "Net cash provided by operating activities was $1.2 billion, compared "
            "to $950 million in the prior-year period.",
        ),
        doc(
            "competition",
            "The company faces significant competition from other cloud providers, "
            "particularly in enterprise infrastructure and data analytics.",
        ),
        doc(
            "office-locations",
            "The company operates offices in twelve countries, with its largest "
            "engineering presence in the United States and India.",
        ),
    ]

    def q(
        query: str, relevant: list[str], category: Literal["text", "image"] = "text"
    ) -> EvalQuery:
        return EvalQuery(
            query=query,
            relevant_doc_ids={docs[d].id for d in relevant},
            category=category,
        )

    queries = [
        q("What was quarterly revenue growth?", ["revenue-q3"]),
        q("How many employees does the company have?", ["headcount-q3"]),
        q("What are the main risk factors?", ["risk-factors"]),
        q("What was net cash from operating activities?", ["cash-flow"]),
        q("Who are the company's main competitors?", ["competition"]),
        q("Where does the company have offices?", ["office-locations"]),
        q("How much did revenue grow year over year?", ["revenue-q3"]),
        q("What was the headcount a year ago?", ["headcount-q3"]),
        # text→image: the query is textual, the only relevant hit is a
        # chart/table candidate — the lexical/rerank signal for it is its
        # caption, per backends/local.py::query()'s documented no-image-
        # rerank-input limitation.
        q("Show me the revenue by segment chart", ["revenue-chart-caption"], "image"),
        q(
            "Is there a chart of headcount growth over time?",
            ["headcount-chart-caption"],
            "image",
        ),
        q(
            "What does the balance sheet table show?",
            ["balance-sheet-table-caption"],
            "image",
        ),
        q(
            "quarterly revenue chart by segment",
            ["revenue-chart-caption", "revenue-q3"],
            "image",
        ),
    ]

    return EvalDataset(documents=documents, queries=queries)
