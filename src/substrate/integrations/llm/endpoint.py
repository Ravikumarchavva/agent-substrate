"""``InferenceEndpoint`` — the config vocabulary every pluggable model-serving
seam in this codebase speaks.

Not a new abstraction: this names and reuses the exact pattern already proven
by ``factory.py``'s ``"compatible"``/``"vllm"`` chat-completions providers and
(as of this change) ``create_embedding_client``'s ``"openai_compatible"``
provider — "point an OpenAI-compatible client at any ``base_url``, one client
class handles the rest." Anything in this repo that talks to a swappable
local-or-remote model (today a local ``llama-server`` subprocess, tomorrow an
sglang/vLLM deployment or a bare remote URL) should describe *where* to reach
it with one of these, not a bespoke config shape.

Lives here (``integrations/llm/``), not ``kernel/`` — this is deployment
wiring (a URL, a key, a timeout), not a frozen protocol/contract kernel is
reserved for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InferenceEndpoint:
    """Where to reach a pluggable inference backend.

    ``model`` follows the same ``"provider/bare-model-name"`` convention
    ``LLMFactory``/``create_embedding_client`` already use (e.g.
    ``"compatible/PaddleOCR-VL-1.6"``) — the provider prefix selects the
    client class, ``base_url`` says where to send requests. Local subprocess-
    managed backends (e.g. a pooled ``llama-server`` child) and remote
    deployments both resolve to one of these; callers of an
    :class:`InferenceEndpoint` never need to know which.
    """

    model: str
    base_url: str | None = None
    api_key: str = ""
    timeout_s: float = 90.0
