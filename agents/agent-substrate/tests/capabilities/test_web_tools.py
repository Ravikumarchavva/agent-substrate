from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ravi.capabilities.tools.web.read_url import ReadUrlTool
from ravi.capabilities.tools.web.wikipedia import WikipediaTool
from ravi.capabilities.tools.web.search import WebSearchTool
from ravi.capabilities.tools.web.surfer import WebSurferTool


# --- ReadUrlTool Tests ---

@pytest.mark.asyncio
@patch("crawl4ai.AsyncWebCrawler")
async def test_read_url_tool_capping(mock_crawler_class):
    # Mock crawler setup
    mock_crawler = AsyncMock()
    mock_crawl_result = MagicMock()
    mock_crawl_result.success = True
    # Create a 20,000 character string
    mock_crawl_result.markdown = "A" * 20000
    mock_crawl_result.error_message = ""
    mock_crawler.arun.return_value = mock_crawl_result
    
    mock_crawler_class.return_value.__aenter__.return_value = mock_crawler

    tool = ReadUrlTool()

    # Case 1: Test with default max_chars (12000)
    result = await tool.execute(url="http://example.com")
    assert not result.is_error
    text = result.content[0].text
    assert len(text) > 12000
    assert "[truncated" in text
    # The clean content length should be exactly 12000
    assert text.startswith("A" * 12000)

    # Case 2: Test with custom max_chars (2000)
    result_custom = await tool.execute(url="http://example.com", max_chars=2000)
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    assert text_custom.startswith("A" * 2000)
    assert not text_custom.startswith("A" * 2001)


# --- WikipediaTool Tests ---

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_wikipedia_tool_capping(mock_client_class):
    # Setup mock HTTP response for search and full article
    mock_client = AsyncMock()
    mock_client_class.return_value.__aenter__.return_value = mock_client

    # Mock responses
    mock_search_resp = MagicMock()
    mock_search_resp.raise_for_status = MagicMock()
    mock_search_resp.json.return_value = {
        "query": {"search": [{"title": "Python (programming language)"}]}
    }

    mock_article_resp = MagicMock()
    mock_article_resp.raise_for_status = MagicMock()
    # 10,000 characters plain text
    mock_article_resp.json.return_value = {
        "query": {
            "pages": {
                "12345": {
                    "extract": "P" * 10000
                }
            }
        }
    }

    mock_client.get.side_effect = [mock_search_resp, mock_article_resp]

    tool = WikipediaTool()

    # Case 1: Default cap (6000)
    result_default = await tool.execute(query="python", full_article=True)
    assert not result_default.is_error
    text = result_default.content[0].text
    assert "[truncated" in text
    assert "P" * 6000 in text
    assert "P" * 6001 not in text

    # Reset mock and setup for custom cap
    mock_client.get.side_effect = [mock_search_resp, mock_article_resp]

    # Case 2: Custom cap (1500)
    result_custom = await tool.execute(query="python", full_article=True, max_chars=1500)
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    assert "P" * 1500 in text_custom
    assert "P" * 1501 not in text_custom


# --- WebSearchTool Tests ---

@pytest.mark.asyncio
@patch("ddgs.DDGS")
async def test_web_search_tool_capping(mock_ddgs_class):
    mock_ddgs = MagicMock()
    mock_ddgs.text.return_value = [
        {"title": f"Title {i}", "body": "B" * 3000, "href": f"http://example.com/{i}"}
        for i in range(1, 6)
    ]
    mock_ddgs_class.return_value = mock_ddgs

    tool = WebSearchTool()

    # Case 1: Default max_chars (10000)
    # Total characters will be 5 * (length of result representation) which is > 15000
    result_default = await tool.execute(query="test query")
    assert not result_default.is_error
    text = result_default.content[0].text
    # Output should not exceed default limit (10000) + length of truncated warning
    assert len(text) > 10000
    assert "[truncated" in text
    assert text.startswith("Search results")

    # Case 2: Custom max_chars (3000)
    result_custom = await tool.execute(query="test query", max_chars=3000)
    assert not result_custom.is_error
    text_custom = result_custom.content[0].text
    assert "[truncated" in text_custom
    # Ensure it was sliced around 3000 chars
    assert len(text_custom) < 3200


# --- WebSurferTool Tests ---

@pytest.mark.asyncio
async def test_web_surfer_tool_capping():
    # We patch playwright availability so we don't start the browser engine
    with patch("ravi.capabilities.tools.web.surfer.PLAYWRIGHT_AVAILABLE", True), \
         patch("ravi.capabilities.tools.web.surfer.async_playwright") as mock_pw:
        
        tool = WebSurferTool()
        
        # Mock browser page
        mock_page = AsyncMock()
        mock_page.url = "http://example.com/page"
        tool._page = mock_page
        tool._browser = MagicMock()
        tool._playwright = MagicMock()

        # Case 1: extract_text action
        mock_page.evaluate.return_value = "T" * 20000
        result_text = await tool.execute(action="extract_text", max_chars=5000)
        assert not result_text.is_error
        import json
        data_text = json.loads(result_text.content[0].text)
        assert data_text["truncated"] is True
        assert len(data_text["text"]) > 5000  # includes truncation message
        assert data_text["original_length"] == 20000

        # Case 2: extract_markdown action
        mock_page.evaluate.return_value = "M" * 25000
        result_md = await tool.execute(action="extract_markdown", max_chars=4000)
        assert not result_md.is_error
        data_md = json.loads(result_md.content[0].text)
        assert data_md["truncated"] is True
        assert len(data_md["markdown"]) > 4000
        assert data_md["original_length"] == 25000

        # Case 3: get_html action
        mock_page.content.return_value = "H" * 30000
        result_html = await tool.execute(action="get_html", max_chars=8000)
        assert not result_html.is_error
        data_html = json.loads(result_html.content[0].text)
        assert data_html["truncated"] is True
        assert len(data_html["html"]) > 8000
        assert data_html["original_length"] == 30000
