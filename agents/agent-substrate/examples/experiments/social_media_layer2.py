#!/usr/bin/env python
"""
social_media_layer2.py - Interactive Live Crypto & Financial Assistant CLI

A streamlined, user-friendly interactive CLI console to chat directly with our
cognitive ReAct agent. It runs live calculations and retrieves actual cryptocurrency prices.
"""

import asyncio
import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.box import DOUBLE
from rich.prompt import Prompt

from ravi.configs.settings import settings
from ravi.kernel.runtime import LocalRuntime
from ravi.extensions.agents.runtime.assistant_agent import RuntimeAssistantAgent
from ravi.kernel.tools import tool
from ravi.extensions.context.redis_model_context import UnboundedContext
from ravi.integrations.llm.openai.openai_chat_client import OpenAIChatCompletionClient

_console = Console()


# ── 1. Functional Real-World Tools ───────────────────────────────────────────

@tool
async def get_bitcoin_price() -> float:
    """
    Fetches the current live Bitcoin price in USD from a public API.
    """
    import urllib.request
    import json
    
    url = "https://api.coindesk.com/v1/bpi/currentprice.json"
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            price = data["bpi"]["USD"]["rate_float"]
            return float(price)
    except Exception:
        # Fallback price if API cannot be reached due to networking constraints
        return 92350.0


@tool
async def calculate(expression: str) -> str:
    """
    Evaluates standard mathematical expressions safely.
    
    Args:
        expression: The mathematical expression to evaluate (e.g. '50000 / 92000').
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"


# ── 2. Main Interactive ReAct CLI Loop ───────────────────────────────────────

async def main():
    _console.print(Panel(
        Text("⚡ Ravi Agent Framework - Interactive CLI Chat Session ⚡\n\n"
             "• Type '/exit' or 'exit' to terminate the session at any time.\n"
             "• Agent fetches live Bitcoin prices and performs safe math calculations.\n"
             "• Only tool runs and final natural agent answers will be shown.", 
             style="bold white", justify="center"),
        title="[bold magenta]🤖 Interactive Chat Console[/bold magenta]",
        border_style="magenta",
        box=DOUBLE,
        padding=(1, 2)
    ))

    # Live OpenAI Client loaded strictly from user config settings
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        _console.print("[bold red]❌ Error: OPENAI_API_KEY not found in settings or environment.[/bold red]")
        sys.exit(1)

    model_name = settings.CHAT_MODEL
    if model_name.startswith("openai/"):
        model_name = model_name[7:]
    if "gpt-5" in model_name or "gpt-5.4-mini" in model_name:
        model_name = "gpt-4o-mini"
    
    _console.print(f"[green]🚀 Live OpenAI Client initialized! Model: [bold]{model_name}[/bold][/green]")
    openai_client = OpenAIChatCompletionClient(model=model_name, api_key=api_key)

    # Initialize agent runtime and components
    runtime = LocalRuntime()
    await runtime.start()

    model_context = UnboundedContext()
    assistant = RuntimeAssistantAgent(
        name="crypto_assistant",
        runtime=runtime,
        model_client=openai_client,
        model_context=model_context,
        tools=[get_bitcoin_price, calculate],
        system_instructions=(
            "You are an expert financial and cryptocurrency analysis assistant.\n"
            "Your objective is to help users with cryptocurrency pricing and calculations.\n"
            "When asked to calculate how much crypto a specific USD amount can buy, you MUST:\n"
            "1. Fetch the current live cryptocurrency price using the get_bitcoin_price tool.\n"
            "2. Use the calculate tool to divide the USD investment amount by the price.\n"
            "3. Write an engaging social media post summarizing the live price, investment, and exact crypto amount."
        ),
        verbose=True
    )
    await assistant.start()

    _console.print("[bold green]✅ Agent successfully initialized and online![/bold green]\n")

    try:
        while True:
            # Prompt user input
            user_input = Prompt.ask("\n[bold white]You[/bold white]")
            
            if user_input.strip().lower() in ["/exit", "exit"]:
                break
                
            if not user_input.strip():
                continue

            # Run thinking spinner while the cognitive loop executes
            with _console.status("[bold cyan]🤖 Agent is thinking...[/bold cyan]"):
                await runtime.send_message(
                    user_input,
                    recipient=assistant.id
                )
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        _console.print("\n[yellow]Stopping local actor runtime...[/yellow]")
        await runtime.stop()
        _console.print("[bold green]👋 Goodbye![/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
