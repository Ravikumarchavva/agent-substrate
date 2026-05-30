"""Supervision trees for fault-tolerant agent execution.

Inspired by Erlang OTP, this module provides Supervisors and Failure Policies
to catch crashing agents and automatically restart or escalate them, preventing
the entire workflow from collapsing due to LLM hallucinations or transient errors.
"""

from .supervisor import Supervisor
from .policies import FailurePolicy, RetryPolicy

__all__ = [
    "Supervisor",
    "FailurePolicy",
    "RetryPolicy",
]
