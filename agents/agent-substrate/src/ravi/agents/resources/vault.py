from __future__ import annotations

from typing import Protocol


class SecretVault(Protocol):
    """Secure storage for API keys and tokens.

    Agents retrieve credentials at runtime so raw keys never appear in
    message history or logs.
    """

    async def get_secret(self, key: str) -> str | None:
        """Return the secret value for *key*, or ``None`` if absent."""
        ...

    async def list_keys(self) -> list[str]:
        """List secret keys this agent is authorized to access."""
        ...
