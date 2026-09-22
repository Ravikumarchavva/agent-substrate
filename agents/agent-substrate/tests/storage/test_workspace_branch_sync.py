from __future__ import annotations

import pytest
from pathlib import Path

from substrate.agents.workspace.layout import conversation_workspace_prefix
from substrate.agents.storage.local_object_store import (
    WorkspaceFileStore,
    WorkspaceQuotaExceededError,
)


@pytest.mark.asyncio
async def test_workspace_branch_file_synchronization_and_isolation(tmp_path: Path):
    """copy_prefix as a generic ObjectStore primitive, still used for cases
    outside conversation-branch forking (e.g. duplicating a workspace) even
    after Phase 3 switches branch forking itself to the O(1) CAS-manifest
    fork instead of copying bytes."""
    store = WorkspaceFileStore(tmp_path, user_quota_bytes=100_000)
    await store.connect()

    tenant_id = "tenant-1"
    user_id = "user-1"
    conversation_id = "conv-1"

    main_prefix = conversation_workspace_prefix(tenant_id, user_id, conversation_id, "main")

    # Upload files to main
    await store.upload(f"{main_prefix}/shared/script.py", b"print('main branch')")
    await store.upload(f"{main_prefix}/shared/notes.txt", b"notes for main")

    assert await store.exists(f"{main_prefix}/shared/script.py")
    assert await store.exists(f"{main_prefix}/shared/notes.txt")

    # Fork to experiment branch
    exp_prefix = conversation_workspace_prefix(tenant_id, user_id, conversation_id, "experiment")
    assert exp_prefix != main_prefix

    copied = await store.copy_prefix(main_prefix, exp_prefix)
    assert copied == 2

    # Verify files exist in experiment branch
    assert await store.exists(f"{exp_prefix}/shared/script.py")
    assert await store.exists(f"{exp_prefix}/shared/notes.txt")
    assert await store.download(f"{exp_prefix}/shared/script.py") == b"print('main branch')"

    # Mutate in experiment branch
    await store.upload(f"{exp_prefix}/shared/script.py", b"print('experiment branch modified')")

    # Main branch is isolated and unchanged
    assert await store.download(f"{main_prefix}/shared/script.py") == b"print('main branch')"
    assert await store.download(f"{exp_prefix}/shared/script.py") == b"print('experiment branch modified')"


@pytest.mark.asyncio
async def test_workspace_copy_prefix_quota_enforcement(tmp_path: Path):
    # Quota is only 50 bytes
    store = WorkspaceFileStore(tmp_path, user_quota_bytes=50)
    await store.connect()

    tenant_id = "tenant-quota"
    user_id = "user-quota"
    conv_id = "conv-quota"

    main_p = conversation_workspace_prefix(tenant_id, user_id, conv_id, "main")
    exp_p = conversation_workspace_prefix(tenant_id, user_id, conv_id, "exp")

    await store.upload(f"{main_p}/file1.txt", b"x" * 30)

    # Copying 30 bytes more will exceed the 50 byte quota (30 + 30 = 60 > 50)
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.copy_prefix(main_p, exp_p)

