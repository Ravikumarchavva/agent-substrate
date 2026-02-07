from fastmcp import FastMCP
from mcp_server_fetch.server import fetch_url
mcp = FastMCP()

@mcp.tool(
    name="addition_tool",
    description="Adds two numbers together.",
)
async def addition_tool(a: float, b: float) -> dict:
    """Add two numbers.
    
    Args:
        a: First number
        b: Second number
        
    Returns:
        Dictionary with the sum of the two numbers
    """
    return {"result": a + b}

@mcp.tool(
    name='web_search_tool',
    description='Search the web for a query and return results.',
)
async def web_search_tool(
    url: str, user_agent: str, max_chars: int = 1000, start_index: int = 0
) -> str:
    """Search the web for a query.
    
    Args:
        url: URL to fetch content from (placeholder for real search query)
        user_agent: User-Agent string to use for the request
        max_chars: Maximum number of characters to return from the content
        start_index: Starting index from which to return characters (for pagination)
    Returns:
        Search results as a string (placeholder for real search results)
    """
    content, err_msg = await fetch_url(url=url, user_agent=user_agent)
    if err_msg:
        return err_msg
    return content[start_index:start_index + max_chars]

  
if __name__ == "__main__":
    mcp.run(transport='sse')