"""substrate.runtimes — independently-deployable, heavy-dependency,
HTTP-only-consumed first-party services (see docs/claude_docs/decisions.md
for the placement rule). Each package under here ships a thin,
always-importable client module; only its service/ subpackage carries
heavy/optional dependencies.
"""

from __future__ import annotations
