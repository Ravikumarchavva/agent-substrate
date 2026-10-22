"""Model servers this process runs or reaches: a pool of ``llama-server`` children (one per GPU, or one on CPU) or
pre-existing remote endpoints, dispatched least-loaded. Standard library only; GPU detection reads NVML when the
``nvidia-ml-py`` package is installed, else ``nvidia-smi``, else assumes CPU."""

from loamz.engine.models.llama.hardware import detect
from loamz.engine.models.llama.pool import LocalLlamaServerPool, RemoteInferencePool
from loamz.engine.models.llama.types import InferenceEndpoint, InferencePool, PoolWorker
from loamz.engine.models.llama.vram import VramLedger

__all__ = [
    "InferenceEndpoint",
    "InferencePool",
    "LocalLlamaServerPool",
    "PoolWorker",
    "RemoteInferencePool",
    "VramLedger",
    "detect",
]
