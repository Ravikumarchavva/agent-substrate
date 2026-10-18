"""Does a description help a model choose which document to open? A real-model eval, skipped without a real ``OPENAI_API_KEY``.

The task is the one the feature exists for. A user's uploads often have names that say nothing (``scan0042.pdf``, ``Document (3).docx``),
so the index a model sees is the only thing it has to choose by. The model is shown ``DocumentsTool``'s own ``list`` output, first for
documents with only the counted index, then after ``Library.enrich``, and must name the document that answers each question. Same model,
same questions, same text; only the index differs.

Like ``test_retrieval_eval.py`` this is a measurement, not a gate on a score: it prints both accuracies (run with ``-s``) and asserts only that
the descriptions did not make the choice worse and that they stayed grounded.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from substrate.documents import DocumentsTool, Library, LLMEnricher, Reader
from substrate.documents.types import ExtractedPage, ExtractionResult
from substrate.models.protocols import GenerationOptions, ReasoningEffort
from substrate.stores import Store
from substrate.types import ChatMessage, Role, TextBlock
from substrate.types.run import RunScope

C = "tenants/eval/users/u/conversations/c/documents"

# (generic filename, subject sections as (heading, facts))
CORPUS: dict[str, list[tuple[str, str]]] = {
    "scan0042.md": [
        (
            "Delivery performance",
            "On-time delivery reached 94.2% in Q3, up from 91.8% in Q2. The Rotterdam hub processed 1,284,500 parcels.",
        ),
        (
            "Fleet",
            "The fleet grew to 312 vehicles, of which 87 are electric. Fuel spend was 4,812,000 euros, 6% lower than Q2.",
        ),
        (
            "Outlook",
            "Peak volumes are forecast at 1.9 million parcels per month and a Hamburg cross-dock opens in November.",
        ),
    ],
    "Document (3).md": [
        (
            "Eligibility",
            "Employees with twelve months of service may take up to 26 weeks of paid parental leave at 80% of base salary.",
        ),
        (
            "Applying",
            "Submit the leave form to your manager at least 60 days before the expected date. Part-time staff accrue leave pro rata.",
        ),
        (
            "Returning",
            "A phased return over four weeks is available, and the role or an equivalent one is guaranteed.",
        ),
    ],
    "export_final_v2.md": [
        (
            "Primary endpoint",
            "The trial enrolled 412 patients. Progression-free survival was 11.4 months on the study drug versus 7.9 months on placebo.",
        ),
        (
            "Safety",
            "Grade 3 or higher adverse events occurred in 23% of treated patients; the most common was neutropenia.",
        ),
        (
            "Next steps",
            "A phase three trial across 40 sites is planned for 2027, pending regulatory feedback.",
        ),
    ],
    "IMG_2231_text.md": [
        (
            "Term and rent",
            "The lease runs five years from 1 March 2027 at 38,400 pounds per year, reviewed annually in line with inflation.",
        ),
        (
            "Responsibilities",
            "The tenant repairs the interior; the landlord is responsible for the roof, structure and shared stairwell.",
        ),
        (
            "Ending the lease",
            "Either party may end the lease after year three with six months written notice and a break fee of two months rent.",
        ),
    ],
    "notes_old.md": [
        (
            "What changes",
            "From 1 January the Team plan rises from 12 to 15 dollars per seat per month; the Enterprise plan is unchanged.",
        ),
        (
            "Existing customers",
            "Annual subscribers keep their current price until renewal. Monthly subscribers move on their next billing date.",
        ),
        (
            "Reasons",
            "Infrastructure costs per seat rose 22% and the new plan adds audit logs and single sign-on.",
        ),
    ],
    "attachment-17.md": [
        (
            "Timeline",
            "At 02:14 an engineer noticed unusual database reads. By 02:40 the credentials were rotated and the affected service isolated.",
        ),
        (
            "Impact",
            "About 3,100 customer email addresses were exposed; no payment data or passwords were involved.",
        ),
        (
            "Actions",
            "Access keys now expire after 24 hours, and alerts fire on any read of more than 500 rows from the customer table.",
        ),
    ],
    "page_scan_final.md": [
        (
            "Findings",
            "The inspector found the walk-in freezer at 6 degrees Celsius, above the 4 degree limit, and no handwashing sign in the prep area.",
        ),
        (
            "Score",
            "The kitchen received a hygiene rating of 3 out of 5. A re-inspection is due within 56 days.",
        ),
        (
            "Corrective action",
            "The compressor was replaced on the day of inspection and staff completed a refresher food safety course.",
        ),
    ],
    "SKM_C458.md": [
        (
            "Turbine status",
            "Of 24 turbines, 22 are operating. Turbine 7 has a gearbox oil leak and turbine 15 awaits a replacement blade.",
        ),
        (
            "Output",
            "The farm generated 71.3 gigawatt hours in the quarter, 4% below forecast because of low wind in August.",
        ),
        (
            "Maintenance plan",
            "A crane is booked for the first week of November to replace the blade; the gearbox repair follows.",
        ),
    ],
}

# (question, the file that answers it): worded the way a person asks, not the way the document says it
QUESTIONS = [
    ("How punctual were our shipments last quarter?", "scan0042.md"),
    ("How many electric vans do we run?", "scan0042.md"),
    ("How much time off do new parents get?", "Document (3).md"),
    ("When do I have to tell my boss about taking family leave?", "Document (3).md"),
    ("Did the new cancer drug extend survival?", "export_final_v2.md"),
    ("What were the main side effects in the study?", "export_final_v2.md"),
    ("What are we paying for the office space each year?", "IMG_2231_text.md"),
    ("Can we get out of the premises early?", "IMG_2231_text.md"),
    ("Why are we raising what teams pay per user?", "notes_old.md"),
    ("Will yearly subscribers see the higher cost straight away?", "notes_old.md"),
    ("How many people's emails leaked in the security incident?", "attachment-17.md"),
    ("What did we do to stop it happening again?", "attachment-17.md"),
    ("Why did the restaurant score poorly on its hygiene check?", "page_scan_final.md"),
    ("When is the next visit from the food inspector?", "page_scan_final.md"),
    ("Which generator is broken and what is wrong with it?", "SKM_C458.md"),
    ("Was the power plant's energy production on target?", "SKM_C458.md"),
]


def _document(sections: list[tuple[str, str]]) -> ExtractionResult:
    filler = "Further detail and supporting figures are set out in the appendices to this document. "
    body = "".join(f"## {h}\n\n{facts} {filler * 12}\n\n" for h, facts in sections)
    return ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")], markdown=body, engine="eval"
    )


async def _choose(model, index_text: str, question: str) -> str:
    system = (
        "You help find the right document. Below is the index of the documents available. Reply with the id of the single document most "
        "likely to answer the question, and nothing else."
    )
    user = f"{index_text}\n\nQuestion: {question}"
    reply = await model.generate(
        [ChatMessage(role=Role.USER, content=[TextBlock(text=user)])],
        options=GenerationOptions(
            system_instructions=system, max_tokens=60, reasoning=ReasoningEffort.OFF
        ),
    )
    return reply.text.strip()


async def test_descriptions_help_a_model_choose_the_right_document(tmp_path, capsys):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or key.startswith("sk-test"):
        pytest.skip("OPENAI_API_KEY not set (export a real key to run this test)")
    from substrate.integrations.llm.factory import create_model_client

    model = create_model_client("openai/gpt-5.4-mini", api_keys={"openai": key})
    store = Store.at(tmp_path / "s")
    await store.start()
    try:
        library = Library(
            store, reader=Reader(isolate=False), enricher=LLMEnricher(model)
        )
        ids: dict[str, str] = {}
        for name, sections in CORPUS.items():
            added = await library.add(_document(sections), name, collection=C)
            ids[name] = added.document
        tool = DocumentsTool(library, collection=lambda scope: C)
        ctx = SimpleNamespace(scope=RunScope(tenant_id="eval", thread_id="c"))

        async def accuracy() -> float:
            index = (await tool.execute(ctx=ctx, action="list")).text
            right = 0
            for question, answer_file in QUESTIONS:
                picked = await _choose(model, index, question)
                right += int(ids[answer_file] in picked)
            return right / len(QUESTIONS)

        before = await accuracy()
        warnings = 0
        cost = 0.0
        for name in CORPUS:
            done = await library.enrich(collection=C, document=ids[name])
            assert done.state == "done"
            warnings += len(done.warnings)
            cost += done.usage.cost_usd
        after = await accuracy()
        with capsys.disabled():
            print(
                f"\nchoosing the right document from the index ({len(QUESTIONS)} questions, {len(CORPUS)} documents, names like 'scan0042'):\n"
                f"  counted index only : {before:.0%}\n  with descriptions  : {after:.0%}\n"
                f"  figures dropped as ungrounded: {warnings}; cost to describe the corpus: ${cost:.4f}"
            )
        assert after >= before
        assert warnings <= len(
            CORPUS
        )  # a description that invents figures is a defect, not noise
    finally:
        await store.aclose()
