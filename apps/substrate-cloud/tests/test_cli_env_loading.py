"""cmd_start must forward HF_TOKEN (parsed by ServerSettings from .env) into
the uvicorn subprocess's own env — third-party libraries that read it
directly (e.g. huggingface_hub/tokenizers) never see a value that only lives
on the Settings object, and subprocess.run()/Popen() only inherit this
process's actual os.environ unless given an explicit env= kwarg."""

from __future__ import annotations

import argparse
from unittest.mock import patch

from substrate.cli import cmd_start


def _base_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        host="127.0.0.1",
        port=8000,
        reload=False,
        workers=1,
        foreground=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_cmd_start_forwards_hf_token_from_settings_into_subprocess_env(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with (
        patch("substrate.cli._read_pid", return_value=None),
        patch("substrate.cli.subprocess.run") as mock_run,
        patch(
            "substrate.serving.shared.settings.settings.HF_TOKEN",
            "hf_test_token",
        ),
    ):
        cmd_start(_base_args())

    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["HF_TOKEN"] == "hf_test_token"


def test_cmd_start_omits_hf_token_when_settings_has_none(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with (
        patch("substrate.cli._read_pid", return_value=None),
        patch("substrate.cli.subprocess.run") as mock_run,
        patch("substrate.serving.shared.settings.settings.HF_TOKEN", ""),
    ):
        cmd_start(_base_args())

    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert "HF_TOKEN" not in kwargs["env"]
