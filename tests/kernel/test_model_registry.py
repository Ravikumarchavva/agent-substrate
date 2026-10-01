"""Sanity tests for the model registry in agents.llm.models.

Ensures that:
- Every registered model has valid, internally-consistent fields.
- Alias lookups resolve to the correct canonical profile.
- Cost estimation produces non-negative values.
- The registry is not accidentally empty.
"""

from __future__ import annotations

import pytest

from substrate.agents.llm.models import (
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)

KNOWN_PROVIDERS = {"openai", "anthropic", "gemini", "google", "groq"}


# ── Registry completeness ────────────────────────────────────────────────────


def test_registry_is_not_empty() -> None:
    models = list_models()
    assert len(models) >= 10, f"Expected at least 10 models, got {len(models)}"


def test_known_models_present() -> None:
    for name in (
        "gpt-4o",
        "gpt-4o-mini",
        "claude-sonnet-4-20250514",
        "gemini-2.5-flash",
    ):
        profile = get_model_profile(name)
        assert profile is not None, f"Expected model {name!r} to be in registry"


# ── Per-profile field invariants ─────────────────────────────────────────────


@pytest.mark.parametrize("profile", list_models())
def test_profile_fields_are_valid(profile: ModelProfile) -> None:
    assert profile.name, f"Model has empty name: {profile}"
    assert profile.provider in KNOWN_PROVIDERS, (
        f"Model {profile.name!r} has unknown provider {profile.provider!r}"
    )
    assert profile.context_length > 0, (
        f"Model {profile.name!r} has non-positive context_length"
    )
    is_embedding = "embedding" in profile.name or profile.default_dimensions is not None
    if not is_embedding:
        assert profile.max_output_tokens > 0, (
            f"Model {profile.name!r} has non-positive max_output_tokens"
        )
        assert profile.max_output_tokens <= profile.context_length, (
            f"Model {profile.name!r}: max_output_tokens ({profile.max_output_tokens}) "
            f"> context_length ({profile.context_length})"
        )
    assert profile.input_cost_per_mtok >= 0, (
        f"Model {profile.name!r} has negative input cost"
    )
    assert profile.output_cost_per_mtok >= 0, (
        f"Model {profile.name!r} has negative output cost"
    )
    assert len(profile.modalities) >= 1, (
        f"Model {profile.name!r} has empty modalities tuple"
    )
    assert "text" in profile.modalities, (
        f"Model {profile.name!r} missing 'text' in modalities"
    )


# ── Alias resolution ─────────────────────────────────────────────────────────


def test_aliases_resolve_to_canonical() -> None:
    for profile in list_models():
        for alias in profile.aliases:
            resolved = get_model_profile(alias)
            assert resolved is not None, (
                f"Alias {alias!r} for {profile.name!r} returns None from get_model_profile"
            )
            assert resolved.name == profile.name, (
                f"Alias {alias!r} resolved to {resolved.name!r}, expected {profile.name!r}"
            )


# ── Cost estimation ──────────────────────────────────────────────────────────


def test_cost_estimation_non_negative() -> None:
    for profile in list_models():
        cost = estimate_cost(profile.name, input_tokens=1000, output_tokens=500)
        assert cost >= 0.0, f"Negative cost for {profile.name!r}: {cost}"


def test_cost_estimation_unknown_model_returns_zero() -> None:
    cost = estimate_cost("nonexistent-model-xyz", input_tokens=1000, output_tokens=500)
    assert cost == 0.0


def test_cost_estimation_scales_linearly() -> None:
    profile = get_model_profile("gpt-4o")
    assert profile is not None
    cost_1k = estimate_cost("gpt-4o", input_tokens=1_000, output_tokens=0)
    cost_2k = estimate_cost("gpt-4o", input_tokens=2_000, output_tokens=0)
    assert abs(cost_2k - 2 * cost_1k) < 1e-10, "Cost should scale linearly with tokens"


# ── Provider filtering ───────────────────────────────────────────────────────


def test_list_models_by_provider() -> None:
    openai_models = list_models(provider="openai")
    assert all(m.provider == "openai" for m in openai_models)
    assert len(openai_models) >= 5

    anthropic_models = list_models(provider="anthropic")
    assert all(m.provider == "anthropic" for m in anthropic_models)
    assert len(anthropic_models) >= 2


def test_no_duplicate_names() -> None:
    names = [m.name for m in list_models()]
    assert len(names) == len(set(names)), "Duplicate model names in registry: " + str(
        [n for n in names if names.count(n) > 1]
    )
