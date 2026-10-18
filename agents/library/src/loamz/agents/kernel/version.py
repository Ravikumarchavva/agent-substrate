"""The installed version of loamz-agents: what a run is pinned to and what a store records it was created with."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loamz-agents")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
