"""The ``FileStoreProvider`` seam: locally every run is handed the one shared object store."""

from __future__ import annotations

from substrate.types.run import RunScope

from substrate_cloud.factory import shared_file_store


def test_the_local_provider_hands_every_run_the_shared_store() -> None:
    shared = object()
    provide = shared_file_store(shared)  # type: ignore[arg-type]
    assert provide(RunScope(tenant_id="acme")) is shared
    assert provide(RunScope(tenant_id="other", user_id="u")) is shared
