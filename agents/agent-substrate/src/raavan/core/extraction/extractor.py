"""High-level ``Extractor`` — combines LLM structured outputs with batch
processing for enterprise document extraction.

Examples::

    from raavan import create_model_client
    from raavan.core.extraction import Extractor, Invoice

    client = create_model_client("gpt-5-mini")

    # Single extraction
    extractor = Extractor(schema=Invoice, client=client)
    result = await extractor.extract("Invoice #1234 from Acme Corp ...")
    if result.ok:
        print(result.parsed.vendor_name, result.parsed.total_amount)

    # Batch extraction
    invoices = ["Invoice #1 ...", "Invoice #2 ...", "Invoice #3 ..."]
    batch = await extractor.extract_batch(invoices, max_concurrency=5)
    for item in batch.items:
        if item.success:
            print(item.output.parsed.total_amount)
"""

from __future__ import annotations

import logging
from typing import Any, Generic, List, Optional, Type, TypeVar

from pydantic import BaseModel

from raavan.core.batch.config import BatchConfig, BatchResult
from raavan.core.batch.processor import BatchProcessor
from raavan.core.messages.client_messages import SystemMessage, UserMessage
from raavan.core.structured.result import StructuredOutputResult

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Default system instructions per schema (can be overridden)
_DEFAULT_INSTRUCTIONS = (
    "You are a precise data extraction assistant. "
    "Extract the requested information from the provided content. "
    "Return only the structured data — no explanations or commentary. "
    "If a field cannot be determined, leave it null/empty."
)


class Extractor(Generic[T]):
    """Extract typed structured data from unstructured text or images.

    Wraps ``client.generate(response_format=schema)`` with sensible defaults,
    optional custom instructions, and built-in batch support.

    Parameters:
        schema: The Pydantic ``BaseModel`` subclass to extract into.
        client: A ``BaseModelClient`` instance (or pass at extract time).
        instructions: System-level instructions for extraction.
            Defaults to a general-purpose extraction prompt.
        model: Optional model override (e.g. ``"gpt-5-mini"``).
    """

    def __init__(
        self,
        schema: Type[T],
        *,
        client: Optional[Any] = None,
        instructions: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.schema = schema
        self._client = client
        self.instructions = instructions or _DEFAULT_INSTRUCTIONS
        self._model = model

    def _get_client(self, client: Optional[Any] = None) -> Any:
        """Resolve client — explicit param > init param > error."""
        resolved = client or self._client
        if resolved is None:
            raise ValueError(
                "No model client provided. Pass `client=` to Extractor() "
                "or to extract()/extract_batch()."
            )
        return resolved

    async def extract(
        self,
        content: str,
        *,
        client: Optional[Any] = None,
        **kwargs: Any,
    ) -> StructuredOutputResult[T]:
        """Extract structured data from a single piece of content.

        Args:
            content: The text/document content to extract from.
            client: Optional client override for this call.
            **kwargs: Additional kwargs forwarded to ``client.generate()``.

        Returns:
            ``StructuredOutputResult[T]`` — use ``.ok``, ``.parsed``, ``.unwrap()``.
        """
        resolved_client = self._get_client(client)

        messages: List[Any] = [
            SystemMessage(content=self.instructions),
            UserMessage(content=[content]),
        ]

        generate_kwargs: dict[str, Any] = {
            "response_format": self.schema,
            **kwargs,
        }
        if self._model:
            generate_kwargs["model"] = self._model

        result = await resolved_client.generate(messages, **generate_kwargs)

        # The result from generate() with response_format should have .parsed
        parsed_value = getattr(result, "parsed", None)
        raw_text = ""
        if hasattr(result, "content") and result.content:
            raw_text = (
                result.content[0]
                if isinstance(result.content, list)
                else str(result.content)
            )

        return StructuredOutputResult(
            parsed=parsed_value,
            raw_text=raw_text,
        )

    async def extract_batch(
        self,
        contents: List[str],
        *,
        client: Optional[Any] = None,
        max_concurrency: int = 5,
        config: Optional[BatchConfig] = None,
        **kwargs: Any,
    ) -> BatchResult:
        """Extract from multiple documents concurrently.

        Each content string is processed independently through ``extract()``.

        Args:
            contents: List of text/document contents.
            client: Optional client override.
            max_concurrency: Max parallel extractions.
            config: Full ``BatchConfig`` (overrides ``max_concurrency``).
            **kwargs: Additional kwargs forwarded to ``extract()``.

        Returns:
            ``BatchResult`` where each ``item.output`` is a
            ``StructuredOutputResult[T]``.
        """
        resolved_client = self._get_client(client)

        async def _extract_single(
            content: str,
        ) -> StructuredOutputResult[T]:
            return await self.extract(content, client=resolved_client, **kwargs)

        batch_config = config or BatchConfig(max_concurrency=max_concurrency)
        processor = BatchProcessor(fn=_extract_single, config=batch_config)
        return await processor.run(contents)
