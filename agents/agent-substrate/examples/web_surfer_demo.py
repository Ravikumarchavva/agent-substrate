"""Example demonstrating WebSurferTool for agentic web browsing."""
import asyncio
from agent_framework.extensions.tools.web_surfer import WebSurferTool
import time

async def main():
    """Demo web surfing capabilities."""
    print("🌐 WebSurfer Tool Demo\n")
    
    # Initialize tool (set headless=False to see browser window)
    # Browser stays open across multiple actions until "close" is called
    surfer = WebSurferTool(headless=False, browser_type="chromium")
    
    try:
        # 1. Navigate to a website
        print("📍 Navigating to example.com...")
        result = await surfer.execute(action="navigate", url="https://example.com")
        print(f"✅ {result.content[0]['text']}\n")
        
        # 2. Get page metadata
        print("📊 Getting page metadata...")
        result = await surfer.execute(action="get_metadata")
        print(f"✅ {result.content[0]['text'][:200]}...\n")
        
        # 3. Extract text content
        print("📄 Extracting page text...")
        result = await surfer.execute(action="extract_text")
        text_data = result.content[0]['text']
        print(f"✅ Extracted {len(text_data)} characters\n")
        
        # 4. Extract as markdown
        print("📝 Extracting as markdown...")
        result = await surfer.execute(action="extract_markdown")
        print(f"✅ {result.content[0]['text'][:200]}...\n")
        
        # 5. Take screenshot
        print("📸 Taking screenshot...")
        result = await surfer.execute(action="screenshot", full_page=True)
        if len(result.content) > 1 and result.content[1]['type'] == 'image':
            print(f"✅ Screenshot captured: {len(result.content[1]['data'])} bytes (base64)\n")
        
        # 6. Navigate to GitHub
        print("📍 Navigating to GitHub...")
        result = await surfer.execute(action="navigate", url="https://github.com")
        print(f"✅ {result.content[0]['text']}\n")
        
        time.sleep(5)

        # 7. Execute JavaScript
        print("🔧 Executing JavaScript to count links...")
        result = await surfer.execute(
            action="execute_js",
            javascript="document.querySelectorAll('a').length"
        )
        print(f"✅ {result.content[0]['text']}\n")
        
        # 8. Scroll page
        print("📜 Scrolling down...")
        result = await surfer.execute(action="scroll", scroll_direction="down")
        print(f"✅ {result.content[0]['text']}\n")
        
        time.sleep(3)
        # 9. Go back
        print("⬅️ Going back...")
        result = await surfer.execute(action="go_back")
        print(f"✅ {result.content[0]['text']}\n")
        
        print("✅ All web surfing actions completed successfully!")
        
    finally:
        # Close browser
        print("\n🔒 Closing browser...")
        await surfer.execute(action="close")
        print("✅ Browser closed")


if __name__ == "__main__":
    asyncio.run(main())
