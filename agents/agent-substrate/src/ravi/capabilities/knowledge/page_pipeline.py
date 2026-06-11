"""PageIndex RAG Pipeline — vectorless, reasoning-based RAG using hierarchical tree navigation.

Inspired by VectifyAI/PageIndex, it parses documents into a structured Table of Contents tree
and uses an LLM to navigate the hierarchy to retrieve the most relevant sections, avoiding
traditional vector similarity matching.
"""

from __future__ import annotations

import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from ravi.kernel import ChatMessage, TextBlock
from ravi.kernel.llm import LLMClient, GenerationOptions
from ravi.kernel.vector import Document, SearchResult
from ravi.kernel.memory import LongTermMemory
from ravi.kernel.identity import AgentId
from ravi.capabilities.knowledge.chunking import PageChunker

logger = logging.getLogger(__name__)


@dataclass
class PageNode:
    """A node in the hierarchical PageIndex tree."""

    title: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    level: int = 1
    children: list[PageNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def node_to_dict(node: PageNode) -> dict[str, Any]:
    """Serialize a PageNode tree to a dictionary."""
    return {
        "title": node.title,
        "content": node.content,
        "id": node.id,
        "level": node.level,
        "metadata": node.metadata,
        "children": [node_to_dict(child) for child in node.children],
    }


def dict_to_node(d: dict[str, Any]) -> PageNode:
    """Deserialize a PageNode tree from a dictionary."""
    return PageNode(
        title=d["title"],
        content=d["content"],
        id=d["id"],
        level=d.get("level", 1),
        metadata=d.get("metadata", {}),
        children=[dict_to_node(child) for child in d.get("children", [])],
    )


def get_all_descendant_content(node: PageNode) -> str:
    """Recursively collect and concatenate content from the node and all its descendants."""
    parts = []
    if node.content.strip():
        parts.append(node.content.strip())
    for child in node.children:
        parts.append(get_all_descendant_content(child))
    return "\n\n".join(filter(None, parts))


class PageIndexRAGPipeline:
    """PageIndex reasoning-based RAG pipeline.

    Maintains a Table of Contents (TOC) tree of documents and navigates it step-by-step
    using an LLM to find relevant sections for query answering.
    """

    def __init__(
        self,
        model_client: LLMClient,
        memory_store: Optional[LongTermMemory] = None,
        agent_id: str | AgentId = "system",
    ) -> None:
        self._model = model_client
        self._memory = memory_store
        if isinstance(agent_id, str):
            self._agent_id = AgentId(type="system", key=agent_id)
        else:
            self._agent_id = agent_id
        # Fallback local in-memory store for trees if memory_store is not provided
        self._local_trees: dict[str, PageNode] = {}



    # ── Tree Storage Helper ───────────────────────────────────────────────────

    async def _get_collection_tree(self, collection: str) -> PageNode:
        """Load or initialize the root tree node for a collection."""
        if self._memory:
            memories = await self._memory.search(
                self._agent_id,
                query=collection,
                namespace="page_index_trees",
                limit=100,
            )
            for m in memories:
                if m.metadata.get("collection") == collection:
                    try:
                        data = json.loads(m.content)
                        return dict_to_node(data)
                    except Exception:
                        logger.warning("Failed to deserialize tree for collection %s", collection, exc_info=True)

        if collection in self._local_trees:
            return self._local_trees[collection]

        # Initialize new collection root node
        root = PageNode(
            title=f"Collection: {collection}",
            content="",
            level=0,
            metadata={"collection": collection},
        )
        await self._save_collection_tree(collection, root)
        return root

    async def _save_collection_tree(self, collection: str, root: PageNode) -> None:
        """Persist the collection root node tree."""
        if self._memory:
            # Delete existing tree first
            memories = await self._memory.search(
                self._agent_id,
                query=collection,
                namespace="page_index_trees",
                limit=100,
            )
            for m in memories:
                if m.metadata.get("collection") == collection:
                    await self._memory.delete(self._agent_id, m.id, namespace="page_index_trees")

            # Save new tree
            await self._memory.save(
                self._agent_id,
                content=json.dumps(node_to_dict(root)),
                namespace="page_index_trees",
                metadata={"collection": collection},
            )
        else:
            self._local_trees[collection] = root

    # ── Ingest ────────────────────────────────────────────────────────────────

    async def ingest(
        self,
        content: str | list[str],
        *,
        collection: str = "default",
        title: str | None = None,
        strategy: str = "flat",  # flat, markdown, or hierarchical
        **kwargs: Any,
    ) -> int:
        """Build a hierarchical index tree for the document and add it to the collection.

        Args:
            content: Text document or list of page texts.
            collection: Target collection.
            title: Title of the document.
            strategy: Strategy to build the hierarchy ("flat", "markdown", "hierarchical").
        """
        doc_title = title or f"Document {uuid.uuid4().hex[:8]}"

        if isinstance(content, str):
            if strategy == "markdown":
                doc_tree = self._build_markdown_tree(content, doc_title)
            elif strategy == "hierarchical":
                doc_tree = await self._build_hierarchical_tree_llm(content, doc_title)
            else:
                # Flat character pages
                pages = [content[i : i + 3000] for i in range(0, len(content), 3000)]
                doc_tree = self._build_flat_tree(pages, doc_title)
        else:
            # Content is list of page texts
            if strategy == "hierarchical":
                doc_tree = await self._build_hierarchical_tree_llm("\n\n".join(content), doc_title)
            else:
                doc_tree = self._build_flat_tree(content, doc_title)

        # Merge doc_tree into collection root
        root = await self._get_collection_tree(collection)
        # Remove existing document child if title matches to overwrite
        root.children = [child for child in root.children if child.title != doc_title]
        root.children.append(doc_tree)
        await self._save_collection_tree(collection, root)

        # Count nodes added
        def count_nodes(n: PageNode) -> int:
            return 1 + sum(count_nodes(child) for child in n.children)

        return count_nodes(doc_tree)

    def _build_flat_tree(self, pages: list[str], title: str) -> PageNode:
        """Build a simple 2-level tree where pages are flat children of the document root."""
        doc_root = PageNode(title=title, content="", level=1)
        for i, page_text in enumerate(pages):
            page_node = PageNode(
                title=f"Page {i+1}",
                content=page_text,
                level=2,
                metadata={"page_number": i + 1},
            )
            doc_root.children.append(page_node)
        return doc_root

    def _build_markdown_tree(self, text: str, title: str) -> PageNode:
        """Parse markdown headers to build a nested section hierarchy tree."""
        doc_root = PageNode(title=title, content="", level=1)
        lines = text.splitlines()

        # Keep track of active headers at each level
        # Level 1 is the document root. Level 2 maps to #, Level 3 to ##, etc.
        active_nodes: dict[int, PageNode] = {1: doc_root}

        current_content_lines: list[str] = []
        current_node = doc_root

        for line in lines:
            if line.startswith("#"):
                # Count level
                header_prefix = line.split(" ")[0]
                if all(c == "#" for c in header_prefix):
                    level = len(header_prefix) + 1  # Shift by 1 because doc_root is level 1
                    header_title = line[len(header_prefix) :].strip()

                    # Save current content to current node before branching
                    if current_content_lines:
                        current_node.content = "\n".join(current_content_lines).strip()
                        current_content_lines = []

                    # Find parent node
                    parent_level = level - 1
                    while parent_level > 1 and parent_level not in active_nodes:
                        parent_level -= 1
                    parent_node = active_nodes.get(parent_level, doc_root)

                    new_node = PageNode(title=header_title, content="", level=level)
                    parent_node.children.append(new_node)
                    active_nodes[level] = new_node
                    current_node = new_node
                    continue

            current_content_lines.append(line)

        if current_content_lines:
            current_node.content = "\n".join(current_content_lines).strip()

        return doc_root

    async def _build_hierarchical_tree_llm(self, text: str, title: str) -> PageNode:
        """Use the LLM to analyze the document structure and output a hierarchical TOC tree."""
        sample_text = text[:15000]  # Take a sample of the document to extract TOC
        prompt = (
            "Analyze the document text and generate a hierarchical Table of Contents (TOC) structure. "
            "Return a JSON object representing the document root with a title and a children list. "
            "Each child node must have a title, and optionally its own children list.\n\n"
            "Example:\n"
            "{\n"
            '  "title": "Annual Report 2025",\n'
            '  "children": [\n'
            '    {\n'
            '      "title": "1. Executive Summary",\n'
            '      "children": []\n'
            "    },\n"
            '    {\n'
            '      "title": "2. Financial Analysis",\n'
            '      "children": [\n'
            '        {"title": "2.1 Revenues", "children": []},\n'
            '        {"title": "2.2 Net Profits", "children": []}\n'
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            f"Here is a sample of the document:\n{sample_text}\n\n"
            "Return ONLY valid JSON."
        )

        try:
            response = await self._model.generate(
                [ChatMessage(role="user", content=[TextBlock(text=prompt)])],
                options=GenerationOptions(
                    system_instructions="You are a document indexing system. Always respond with raw valid JSON only."
                ),
            )
            text_response = "".join(b.text for b in response.content if isinstance(b, TextBlock))
            toc_data = json.loads(text_response.strip())


            # Recursively build PageNode structures from JSON
            def parse_json_node(data: dict[str, Any], level: int) -> PageNode:
                node_title = data.get("title", "Section")
                node = PageNode(title=node_title, content="", level=level)
                for child_data in data.get("children", []):
                    node.children.append(parse_json_node(child_data, level + 1))
                return node

            doc_root = parse_json_node(toc_data, level=1)
            doc_root.title = title

            # Distribute sections to node contents by matching keyword content
            # For simplicity in this lightweight implementation, split document into paragraphs
            # and assign them to the node whose title has the highest term intersection.
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            all_nodes: list[PageNode] = []

            def collect_nodes(n: PageNode):
                all_nodes.append(n)
                for child in n.children:
                    collect_nodes(child)

            collect_nodes(doc_root)

            # Assign each paragraph to the best matching node title
            for para in paragraphs:
                best_node = doc_root
                best_score = 0
                para_words = set(para.lower().split()[:20])  # Use start of paragraph for topic match
                for node in all_nodes:
                    if node == doc_root:
                        continue
                    node_words = set(node.title.lower().split())
                    score = len(para_words.intersection(node_words))
                    if score > best_score:
                        best_score = score
                        best_node = node
                best_node.content = (best_node.content + "\n\n" + para).strip()

            return doc_root

        except Exception:
            logger.warning("Failed to build hierarchical TOC tree using LLM, falling back to flat page index", exc_info=True)
            pages = [text[i : i + 3000] for i in range(0, len(text), 3000)]
            return self._build_flat_tree(pages, title)

    # ── Query / Navigation ────────────────────────────────────────────────────

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Perform a reasoning-based traversal search of the PageIndex tree.

        Args:
            question: Query text.
            collection: Collection name.
            limit: Unused here (since PageIndex targets a specific sub-section, we return it as a single SearchResult).
        """
        root = await self._get_collection_tree(collection)
        if not root.children:
            return []

        current = root
        navigation_path = []

        # Navigation loop
        while current.children:
            navigation_path.append(current.title)

            # Build prompt listing sub-sections
            options_str = ""
            for idx, child in enumerate(current.children):
                summary = child.content[:150].replace("\n", " ")
                options_str += f"[{idx}] {child.title} - {summary}...\n"

            prompt = (
                f"Query: {question}\n\n"
                f"We are looking for relevant sections in: {current.title}\n"
                f"Here are the available sub-sections:\n{options_str}\n"
                "Which section index (0, 1, 2, etc.) contains the information needed to answer the query? "
                "Respond with the index number only (e.g. 1).\n"
                "If none of the sub-sections are relevant, or if we should stop here to retrieve the current section, reply with 'retrieve'.\n"
                "Output ONLY the index number or 'retrieve'."
            )

            try:
                response = await self._model.generate(
                    [ChatMessage(role="user", content=[TextBlock(text=prompt)])],
                    options=GenerationOptions(
                        system_instructions="You are a document search navigator. Always respond with the single word index or 'retrieve' only."
                    ),
                )
                text_response = "".join(b.text for b in response.content if isinstance(b, TextBlock)).strip().lower()


                if "retrieve" in text_response:
                    break

                # Extract digits
                digits = "".join(c for c in text_response if c.isdigit())
                if digits:
                    idx = int(digits)
                    if 0 <= idx < len(current.children):
                        current = current.children[idx]
                    else:
                        break
                else:
                    break
            except Exception:
                logger.warning("Error navigating page index tree, stopping traversal", exc_info=True)
                break

        # Collect node content
        retrieved_text = get_all_descendant_content(current)

        return [
            SearchResult(
                id=current.id,
                content=[TextBlock(text=retrieved_text)],
                score=1.0,
                metadata={
                    "title": current.title,
                    "navigation_path": " -> ".join(navigation_path),
                },
            )
        ]

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Navigate page index tree, extract node text, and prompt LLM to generate final answer."""
        results = await self.query(question, collection=collection, limit=limit)
        if not results:
            return "No relevant context found in PageIndex."

        context_text = results[0].to_text()
        metadata = results[0].metadata

        system_prompt = system or (
            "You are a professional assistant. Answer the user's question using the provided context from the document tree. "
            "Always cite the section title and path where the answer was found."
        )

        doc_reference = f"Section: {metadata.get('title')}\nPath: {metadata.get('navigation_path')}"

        messages = [
            ChatMessage(
                role="user",
                content=[
                    TextBlock(
                        text=f"Question: {question}\n\n"
                        f"Document Context:\n{context_text}\n\n"
                        f"Reference:\n{doc_reference}"
                    )
                ],
            ),
        ]

        response = await self._model.generate(
            messages,
            options=GenerationOptions(system_instructions=system_prompt),
        )
        return "".join(b.text for b in response.content if isinstance(b, TextBlock))

