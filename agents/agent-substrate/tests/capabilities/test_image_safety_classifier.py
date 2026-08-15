"""ImageSafetyClassifier — real inference against the downloaded ONNX model.

No NSFW/NSFL fixture images are committed to this repo (deliberately, per
the plan) — these tests validate the plumbing (decode → preprocess →
inference → threshold → SafetyVerdict) with synthetic solid-color images,
not classification accuracy on real adversarial content. Accuracy against
real content is a manual/production-monitoring concern, not something to
commit fixture images for.
"""

from __future__ import annotations

import io

import pytest

from substrate.kernel.agent.safety import Severity

pytestmark = pytest.mark.requires_model_download


def _solid_png(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def classifier():
    from substrate.capabilities.safety.image_classifier import ImageSafetyClassifier

    return ImageSafetyClassifier()


def test_solid_color_image_classifies_without_error(classifier):
    v = classifier.classify(_solid_png((128, 128, 128)))
    assert v.detector == "image_safety"
    assert v.modality == "image"
    assert set(v.scores.keys()) == {"NSFL", "NSFW", "SFW"}
    assert abs(sum(v.scores.values()) - 1.0) < 1e-4  # softmax distribution


def test_undecodable_bytes_fail_open_not_raise(classifier):
    v = classifier.classify(b"not a real image, just garbage bytes")
    assert v.severity == Severity.NONE
    assert not v.flagged
    assert "undecodable" in v.detail


def test_empty_bytes_fail_open_not_raise(classifier):
    v = classifier.classify(b"")
    assert v.severity == Severity.NONE
    assert not v.flagged


def test_thresholds_are_configurable():
    from substrate.capabilities.safety.image_classifier import ImageSafetyClassifier

    # An artificially strict threshold (0.0) must flag ANY nonzero NSFW
    # score — proves the threshold param is actually wired through, not
    # hardcoded, without needing a real NSFW fixture image.
    strict = ImageSafetyClassifier(nsfw_threshold=0.0, nsfl_threshold=0.0)
    v = strict.classify(_solid_png((200, 50, 50)))
    assert v.flagged


def test_different_image_sizes_are_both_resized_to_224(classifier):
    small = classifier.classify(_solid_png((100, 100, 100), size=(16, 16)))
    large = classifier.classify(_solid_png((100, 100, 100), size=(1024, 768)))
    # Both must succeed (proves resize handles non-square/non-224 inputs);
    # exact score equality isn't asserted (aspect-ratio resize differs).
    assert set(small.scores.keys()) == {"NSFL", "NSFW", "SFW"}
    assert set(large.scores.keys()) == {"NSFL", "NSFW", "SFW"}
