from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from agent_substrate.capabilities.tools.web.read_url import ReadUrlTool, _extract_relevant
from agent_substrate.capabilities.tools.web.wikipedia import WikipediaTool
from agent_substrate.capabilities.tools.web.search import WebSearchTool
from agent_substrate.capabilities.tools.web.surfer import WebSurferTool


# ---------------------------------------------------------------------------
# ReadUrlTool — crawl4ai path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("crawl4ai.AsyncWebCrawler")
async def test_read_url_tool_capping(mock_crawler_class):
    mock_crawler = AsyncMock()
    mock_crawl_result = MagicMock()
    mock_crawl_result.success = True
    mock_crawl_result.markdown = "A" * 20000
    mock_crawl_result.error_message = ""
    mock_crawler.arun.return_value = mock_crawl_result
    mock_crawler_class.return_value.__aenter__.return_value = mock_crawler

    tool = ReadUrlTool()  # no Exa key → crawl4ai path

    # Default max_chars is now 6000 (down from 12000).
    result = await tool.execute(url="http://example.com")
    assert not result.is_error
    text = result.content[0].text
    assert len(text) > 6000
    assert "[truncated" in text
    assert text.startswith("A" * 6000)

    # Custom max_chars.
    result_custom = await tool.execute(url="http://example.com", max_chars=2000)
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    assert text_custom.startswith("A" * 2000)
    assert not text_custom.startswith("A" * 2001)


# ---------------------------------------------------------------------------
# ReadUrlTool — _extract_relevant helper
# ---------------------------------------------------------------------------


def test_extract_relevant_returns_highest_scoring_paragraphs():
    text = (
        "Introduction paragraph with some general text about nothing.\n\n"
        "This paragraph is about Python asyncio event loop details.\n\n"
        "Unrelated paragraph about cooking recipes and ingredients.\n\n"
        "More asyncio details: tasks, futures, and coroutines in Python.\n\n"
        "Another unrelated paragraph about sports and weather."
    )
    result = _extract_relevant(text, query="asyncio event loop Python", max_chars=130)
    assert "asyncio" in result
    assert "cooking" not in result


def test_extract_relevant_no_query_falls_back_to_slice():
    text = "X" * 1000
    result = _extract_relevant(text, query=None, max_chars=500)
    assert result == "X" * 500


def test_extract_relevant_preserves_reading_order():
    text = (
        "Para A about nothing.\n\n" * 5
        + "Para B about asyncio.\n\n"
        + "Para C about asyncio.\n\n"
    )
    result = _extract_relevant(text, query="asyncio", max_chars=200)
    # Both asyncio paras should be present and B before C.
    assert result.index("Para B") < result.index("Para C")


# ---------------------------------------------------------------------------
# ReadUrlTool — Tavily Extract path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_url_tool_tavily_path():
    mock_client = AsyncMock()
    mock_client.extract.return_value = {
        "results": [
            {
                "url": "http://example.com",
                "raw_content": "Asyncio event loop manages coroutines. Tasks and futures are core primitives.",
            }
        ],
        "failed_results": [],
    }

    with patch("tavily.AsyncTavilyClient", return_value=mock_client):
        tool = ReadUrlTool(tavily_api_key="test-key")
        result = await tool.execute(url="http://example.com", query="asyncio")

    assert not result.is_error
    text = result.content[0].text
    assert "Asyncio event loop" in text


@pytest.mark.asyncio
async def test_read_url_tool_tavily_takes_priority_over_exa():
    mock_tavily = AsyncMock()
    mock_tavily.extract.return_value = {
        "results": [{"url": "http://example.com", "raw_content": "From Tavily"}],
        "failed_results": [],
    }

    with patch("tavily.AsyncTavilyClient", return_value=mock_tavily):
        tool = ReadUrlTool(tavily_api_key="tavily-key", exa_api_key="exa-key")
        result = await tool.execute(url="http://example.com")

    assert not result.is_error
    assert "From Tavily" in result.content[0].text


# ---------------------------------------------------------------------------
# ReadUrlTool — Exa path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_url_tool_exa_path():
    mock_result = MagicMock()
    mock_result.highlights = [
        "Asyncio event loop manages coroutines.",
        "Tasks and futures.",
    ]
    mock_result.text = ""

    mock_response = MagicMock()
    mock_response.results = [mock_result]

    mock_exa = MagicMock()
    mock_exa.get_contents.return_value = mock_response

    with patch("exa_py.Exa", return_value=mock_exa):
        tool = ReadUrlTool(exa_api_key="test-key")
        result = await tool.execute(url="http://example.com", query="asyncio")

    assert not result.is_error
    text = result.content[0].text
    assert "Asyncio event loop" in text
    assert "Tasks and futures" in text


# ---------------------------------------------------------------------------
# WikipediaTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_wikipedia_tool_capping(mock_client_class):
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client

    mock_search_resp = MagicMock()
    mock_search_resp.raise_for_status = MagicMock()
    mock_search_resp.json.return_value = {
        "query": {"search": [{"title": "Python (programming language)"}]}
    }

    mock_article_resp = MagicMock()
    mock_article_resp.raise_for_status = MagicMock()
    mock_article_resp.json.return_value = {
        "query": {"pages": {"12345": {"extract": "P" * 10000}}}
    }

    mock_client.get.side_effect = [mock_search_resp, mock_article_resp]
    tool = WikipediaTool()

    result_default = await tool.execute(query="python", full_article=True)
    assert not result_default.is_error
    text = result_default.content[0].text
    assert "[truncated" in text
    assert "P" * 6000 in text
    assert "P" * 6001 not in text

    mock_client.get.side_effect = [mock_search_resp, mock_article_resp]
    result_custom = await tool.execute(
        query="python", full_article=True, max_chars=1500
    )
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    assert "P" * 1500 in text_custom
    assert "P" * 1501 not in text_custom


# ---------------------------------------------------------------------------
# WebSearchTool — DuckDuckGo path (fallback, no key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddgs.DDGS")
async def test_web_search_tool_ddgs_capping(mock_ddgs_class):
    mock_ddgs = MagicMock()
    mock_ddgs.text.return_value = [
        {"title": f"Title {i}", "body": "B" * 2000, "href": f"http://example.com/{i}"}
        for i in range(1, 4)
    ]
    mock_ddgs_class.return_value = mock_ddgs

    tool = WebSearchTool()  # no keys → DuckDuckGo

    # Default limit is now 5000.
    result = await tool.execute(query="test query")
    assert not result.is_error
    text = result.content[0].text
    assert "[truncated" in text
    assert "Search results" in text

    # Custom cap.
    result_custom = await tool.execute(query="test query", max_chars=1000)
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    assert len(text_custom) < 1200


# ---------------------------------------------------------------------------
# WebSearchTool — Exa path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_tool_exa_path():
    mock_result = MagicMock()
    mock_result.title = "Asyncio Guide"
    mock_result.url = "https://docs.python.org/asyncio"
    mock_result.highlights = [
        "Event loop runs coroutines.",
        "Use asyncio.run() as entry point.",
    ]

    mock_response = MagicMock()
    mock_response.results = [mock_result]

    mock_exa = MagicMock()
    mock_exa.search.return_value = mock_response

    with patch("exa_py.Exa", return_value=mock_exa):
        tool = WebSearchTool(exa_api_key="test-key")
        result = await tool.execute(query="asyncio event loop")

    assert not result.is_error
    text = result.content[0].text
    assert "Asyncio Guide" in text
    assert "Event loop runs coroutines" in text
    assert "via Exa" in text


# ---------------------------------------------------------------------------
# WebSearchTool — Tavily path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_tool_tavily_path():
    mock_tavily = MagicMock()
    mock_tavily.search.return_value = {
        "results": [
            {
                "title": "Python asyncio",
                "url": "https://docs.python.org/asyncio",
                "content": "The asyncio module provides tools for async I/O.",
            }
        ]
    }

    with patch("tavily.TavilyClient", return_value=mock_tavily):
        tool = WebSearchTool(tavily_api_key="test-key")
        result = await tool.execute(query="asyncio")

    assert not result.is_error
    text = result.content[0].text
    assert "Python asyncio" in text
    assert "async I/O" in text
    assert "via Tavily" in text


# ---------------------------------------------------------------------------
# WebSearchTool — Exa takes priority over Tavily when both keys present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_tool_exa_priority_over_tavily():
    mock_result = MagicMock()
    mock_result.title = "Exa result"
    mock_result.url = "https://exa.ai"
    mock_result.highlights = ["Exa highlight."]

    mock_response = MagicMock()
    mock_response.results = [mock_result]

    mock_exa = MagicMock()
    mock_exa.search.return_value = mock_response

    with patch("exa_py.Exa", return_value=mock_exa):
        tool = WebSearchTool(exa_api_key="exa-key", tavily_api_key="tavily-key")
        result = await tool.execute(query="test")

    assert not result.is_error
    assert "via Exa" in result.content[0].text


# ---------------------------------------------------------------------------
# WebSurferTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_surfer_tool_capping():
    with (
        patch("agent_substrate.capabilities.tools.web.surfer.PLAYWRIGHT_AVAILABLE", True),
        patch("agent_substrate.capabilities.tools.web.surfer.async_playwright"),
    ):
        tool = WebSurferTool()

        mock_page = AsyncMock()
        mock_page.url = "http://example.com/page"
        tool._page = mock_page
        tool._browser = MagicMock()
        tool._playwright = MagicMock()

        mock_page.evaluate.return_value = "T" * 20000
        result_text = await tool.execute(action="extract_text", max_chars=5000)
        assert not result_text.is_error
        import json

        data_text = json.loads(result_text.content[0].text)
        assert data_text["truncated"] is True
        assert len(data_text["text"]) > 5000
        assert data_text["original_length"] == 20000

        mock_page.evaluate.return_value = "M" * 25000
        result_md = await tool.execute(action="extract_markdown", max_chars=4000)
        assert not result_md.is_error
        data_md = json.loads(result_md.content[0].text)
        assert data_md["truncated"] is True
        assert len(data_md["markdown"]) > 4000
        assert data_md["original_length"] == 25000

        mock_page.content.return_value = "H" * 30000
        result_html = await tool.execute(action="get_html", max_chars=8000)
        assert not result_html.is_error
        data_html = json.loads(result_html.content[0].text)
        assert data_html["truncated"] is True
        assert len(data_html["html"]) > 8000
        assert data_html["original_length"] == 30000
