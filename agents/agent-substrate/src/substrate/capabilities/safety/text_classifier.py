"""Prompt-attack (jailbreak/instruction-override) text classifier.

Ships `Llama-Prompt-Guard-2-86M`, not the `Opir-edge-multilang` model this
plan originally targeted — see the plan's "Step 0 result" section for why:
Opir's ONNX export is numerically correct (verified, max abs delta 1.27e-07
vs the original PyTorch model) but ~50x slower than its own model card
claims (685-851ms at 512 tokens vs a 250ms budget), and its INT8
quantization breaks the model outright (two attempts, both >0.7 max abs
delta against a 1e-3 gate). Prompt Guard's community ONNX export was
measured directly against this deployment: 20-35ms/call, correct
directional classification on real jailbreak/benign/multilingual fixtures.

License note: Prompt Guard 2 is Llama Community licensed, not MIT/Apache —
an acknowledged, real obligation (acceptable-use policy, "Built with Llama"
attribution), not an oversight. See the plan's licensing table.

Binary output only (`BENIGN`/`MALICIOUS`) — this model does not distinguish
jailbreak from injection, and does not classify harmful content (sexual/
violence/hate) at all; that is `ContentSafetyClassifier`'s job, a separate
model, not this one pretending to cover both.
"""

from __future__ import annotations

import math

from substrate.kernel.agent.safety import SafetyVerdict, Severity
from substrate.logger import setup_logging

logger = setup_logging("substrate.capabilities.safety.text_classifier")

# Pinned by revision, not "main" — a Hub repo can change underneath us,
# straight into the security layer (see plan's production-hardening notes).
_MODEL_REPO = "gravitee-io/Llama-Prompt-Guard-2-86M-onnx"
_MODEL_REVISION = "45a05fbd5337a864edc608f994911f009c37ca57"
_MODEL_FILE = "model.quant.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_MAX_TOKENS = 512  # the model's own context limit — see docstring above


class PromptGuardClassifier:
    """`TextSafetyClassifier` Protocol implementation (kernel/agent/safety.py).

    Loads once (one shared onnxruntime `InferenceSession`, built eagerly —
    not lazily on first request, which would stall a live turn on a model
    download) and is safe to share across concurrent requests:
    `InferenceSession.run()` is thread-safe, so ``classify()`` needs no lock.

    ``intra_op_num_threads=1``/``inter_op_num_threads=1`` deliberately — see
    the plan's production-hardening notes: ONNX Runtime defaults to
    thread-per-core, which thrashes under concurrent per-request calls;
    relying on request-level concurrency instead is the standard fix for
    in-process ORT serving.
    """

    detector_name = "prompt_guard"

    def __init__(
        self,
        *,
        threshold: float = 0.9,
        model_repo: str = _MODEL_REPO,
        model_revision: str = _MODEL_REVISION,
    ) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self._threshold = threshold

        model_path = hf_hub_download(model_repo, _MODEL_FILE, revision=model_revision)
        tokenizer_path = hf_hub_download(
            model_repo, _TOKENIZER_FILE, revision=model_revision
        )

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )

    def classify(self, text: str) -> SafetyVerdict:
        """Sync, CPU-bound — callers run this via ``asyncio.to_thread``."""
        import numpy as np

        if not text.strip():
            return SafetyVerdict(
                severity=Severity.NONE,
                scores={"malicious": 0.0},
                detector=self.detector_name,
            )

        encoding = self._tokenizer.encode(text)
        input_ids = np.array([encoding.ids], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask], dtype=np.int64)

        (logits,) = self._session.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )
        # softmax over the 2-way {BENIGN, MALICIOUS} logits (id2label in the
        # model config) — done by hand to avoid a torch/scipy dependency for
        # one 2-element softmax.
        row = logits[0]
        m = max(row)
        exps = [math.exp(v - m) for v in row]
        total = sum(exps)
        malicious_score = float(exps[1] / total)

        severity = (
            Severity.HIGH if malicious_score >= self._threshold else Severity.NONE
        )
        return SafetyVerdict(
            severity=severity,
            scores={"malicious": malicious_score},
            detector=self.detector_name,
        )


__all__ = ["PromptGuardClassifier"]
