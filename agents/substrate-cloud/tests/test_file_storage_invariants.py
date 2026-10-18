"""What the platform must never hold again: object-store credentials, and tables that duplicate the data service's Files domain."""

from __future__ import annotations

import re

from substrate_cloud.monolith.models import Base
from substrate_cloud.shared.settings import ServerSettings

FILE_TABLES = {"files", "file_versions", "file_metadata", "workspace_quotas"}


def test_the_settings_schema_has_no_s3_or_object_store_keys():
    """Files are the data service's; this process signs tokens for it and holds no bucket, endpoint or access key. The only storage
    setting left is the folder a deployment without a data service keeps files in."""
    object_store = re.compile(
        r"S3|BUCKET|ENDPOINT_URL|ACCESS_KEY|FILE_STORE_(BACKEND|ENDPOINT|REGION|PREFIX|SECRET)|PENDING_UPLOAD|FILE_KEK|FILE_ENCRYPTION"
    )
    assert [k for k in ServerSettings.model_fields if object_store.search(k)] == []
    assert "FILE_STORE_ROOT" in ServerSettings.model_fields


def test_the_platform_database_has_no_file_tables():
    """A file's bytes, versions, quota and ownership are the data service's; the platform keeps no copy in a table."""
    assert FILE_TABLES & set(Base.metadata.tables) == set()


def test_the_s3_extra_and_client_are_gone_from_the_platform_dependencies():
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    agent_substrate = next(d for d in dependencies if d.startswith("agent-substrate["))
    assert "data" in agent_substrate and "s3" not in agent_substrate.replace(
        "sandbox", ""
    )
    assert not any("botocore" in d or "boto3" in d for d in dependencies)
