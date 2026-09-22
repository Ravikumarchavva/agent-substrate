"""NSFW/NSFL image classifier — `OwenElliott/image-safety-classifier-xs`.

MIT licensed, SwiftFormer-XS, 3.5M params, 13MB ONNX — the one detector in
this pipeline genuinely small enough that image models need no vocabulary
(unlike the text side; see text_classifier.py's docstring for why a
multilingual text model can't shrink this far).

Answers only SFW/NSFW/NSFL — it has no opinion on prompt-attack content
rendered as pixels (T4 in the plan's threat model). That's covered
separately by OCR'ing the image and running the extracted text through
``PromptGuardClassifier`` — this class does not attempt to do both.

Preprocessing note: the ONNX graph's input is a float32 NCHW tensor, not raw
uint8 pixels — despite secondhand research claiming preprocessing was fully
baked into the graph, the input shape itself contradicts that (a graph that
truly baked in normalization would accept raw pixel bytes, not an
already-scaled float tensor). Standard `timm` ImageNet preprocessing
(resize→224, bicubic, /255, mean/std normalize) is applied here explicitly,
using the exact mean/std this model's own `config.json` documents — that
config block only makes sense to publish if a caller is expected to apply
it, which is exactly the giveaway that it isn't already inside the graph.
"""

from __future__ import annotations

import io

from substrate.kernel.agent.safety import SafetyVerdict, Severity
from substrate.logger import setup_logging

logger = setup_logging("substrate.capabilities.safety.image_classifier")

_MODEL_REPO = "OwenElliott/image-safety-classifier-xs"
_MODEL_REVISION = "54f4560bd9c5ee92d45dc30418a8f8680e80de6d"
_MODEL_FILE = "onnx/image-safety-classifier-xs.onnx"

# Order matches this model's own config.json `label_names` — NOT
# alphabetical, do not "clean up" this ordering.
_LABELS = ["NSFL", "NSFW", "SFW"]
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)
_INPUT_SIZE = 224


class ImageSafetyClassifier:
    """`ImageSafetyClassifier` Protocol implementation (kernel/agent/safety.py).

    Same eager-load-once, thread-safe-session pattern as
    ``PromptGuardClassifier`` — see that class's docstring for the
    ORT-thread-pinning rationale.
    """

    detector_name = "image_safety"

    def __init__(
        self,
        *,
        nsfw_threshold: float = 0.5,
        nsfl_threshold: float = 0.3,
        model_repo: str = _MODEL_REPO,
        model_revision: str = _MODEL_REVISION,
    ) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        self._nsfw_threshold = nsfw_threshold
        self._nsfl_threshold = nsfl_threshold

        model_path = hf_hub_download(model_repo, _MODEL_FILE, revision=model_revision)

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )

    def _preprocess(self, image_bytes: bytes):
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((_INPUT_SIZE, _INPUT_SIZE), Image.BICUBIC)
        arr = np.asarray(img).astype(np.float32) / 255.0
        mean = np.array(_MEAN, dtype=np.float32)
        std = np.array(_STD, dtype=np.float32)
        arr = (arr - mean) / std
        arr = arr.transpose(2, 0, 1)[None, ...]  # HWC -> NCHW
        return arr

    def classify(self, image_bytes: bytes) -> SafetyVerdict:
        """Sync, CPU-bound — callers run this via ``asyncio.to_thread``.

        Malformed/undecodable image bytes fail open (returns NONE severity
        with an empty scores dict and a detail message) rather than raising —
        a corrupt attachment must not crash the guardrail pipeline; the
        upload/extraction layer already validates file integrity separately.
        """
        try:
            tensor = self._preprocess(image_bytes)
        except Exception as exc:
            logger.warning("ImageSafetyClassifier: could not decode image: %s", exc)
            return SafetyVerdict(
                severity=Severity.NONE,
                detector=self.detector_name,
                modality="image",
                detail=f"undecodable image: {exc}",
            )

        (probs,) = self._session.run(None, {"image": tensor})
        scores = {label: float(p) for label, p in zip(_LABELS, probs[0])}

        if scores["NSFL"] >= self._nsfl_threshold:
            severity = Severity.CRITICAL
        elif scores["NSFW"] >= self._nsfw_threshold:
            severity = Severity.HIGH
        else:
            severity = Severity.NONE

        return SafetyVerdict(
            severity=severity,
            scores=scores,
            detector=self.detector_name,
            modality="image",
        )


__all__ = ["ImageSafetyClassifier"]
