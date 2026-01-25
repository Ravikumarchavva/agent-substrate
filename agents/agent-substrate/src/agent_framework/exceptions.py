class AgentError(Exception):
    """Base exception for all errors in the Agent Framework."""
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

class ConfigurationError(AgentError):
    """Raised when there is a configuration issue (e.g. missing API keys)."""
    pass

class ModelProviderError(AgentError):
    """Raised when the LLM provider fails (e.g. API error, rate limit)."""
    pass

class ContextLimitExceededError(ModelProviderError):
    """Raised when the prompt exceeds the context window."""
    pass

class ToolError(AgentError):
    """Base class for tool-related errors."""
    def __init__(self, message: str, tool_name: str, details: dict = None):
        super().__init__(message, details)
        self.tool_name = tool_name

class ToolNotFoundError(ToolError):
    """Raised when a requested tool is not found."""
    pass

class ToolExecutionError(ToolError):
    """Raised when a tool fails to execute."""
    pass

class AgentExecutionError(AgentError):
    """Raised when the agent fails to complete its run loop."""
    pass
