"""Tests for kernel/chain.py value types."""

from __future__ import annotations

import pytest

from substrate.tools import ChainPolicy


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tool_calls": -1},
        {"call_timeout_s": -1},
        {"approval_timeout_s": -1},
        {"total_timeout_s": -1},
        {"max_inline_result_bytes": -1},
    ],
)
def test_chain_policy_rejects_negative_limits(kwargs):
    with pytest.raises(ValueError):
        ChainPolicy(**kwargs)
