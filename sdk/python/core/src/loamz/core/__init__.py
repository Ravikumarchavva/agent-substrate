"""Shared foundation of the Loamz client libraries: naming rules (`tenancy`), and, as the SDK grows, the HTTP
pipeline, retry and paging policies and the error types every service client uses."""

from loamz.core import tenancy

__all__ = ["tenancy"]
