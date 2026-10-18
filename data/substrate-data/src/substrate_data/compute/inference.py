"""Model compute adapter for a deployment-configured embedding service."""

from __future__ import annotations

import math

import httpx

from substrate_data.core.models import Settings


def embed(settings: Settings, texts: list[str]):
    if not settings.embedding_url or not settings.embedding_model:
        raise ValueError(
            "Configure an embedding endpoint and model before submitting inference jobs"
        )
    headers = {}
    if token := settings.embedding_token.get_secret_value():
        headers["Authorization"] = "Bearer " + token
    try:
        response = httpx.post(
            settings.embedding_url,
            json={"input": texts, "model": settings.embedding_model},
            headers=headers,
            timeout=settings.timeout_seconds,
            trust_env=False,
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        if [item["index"] for item in data] != list(range(len(texts))):
            raise ValueError("Embedding endpoint returned incomplete results")
        vectors = [item["embedding"] for item in data]
        dimensions = len(vectors[0])
        if not 1 <= dimensions <= 8192 or any(
            len(vector) != dimensions
            or any(
                not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector
            )
            for vector in vectors
        ):
            raise ValueError("Embedding endpoint returned invalid vectors")
        return {"model": settings.embedding_model, "dimensions": dimensions, "embeddings": vectors}
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        raise ValueError("Embedding service request failed") from exc
