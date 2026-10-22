"""The composition root: one typed settings tree and the container that builds everything from it."""

from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.bootstrap.settings import Settings

__all__ = ["Container", "Settings"]
