"""Credentials for the Loamz client libraries: a service account, or whatever the environment provides."""

from loamz.core.credentials import StaticTokenCredential, TokenCredential
from loamz.identity.credentials import (
    ClientSecretCredential,
    DefaultLoamzCredential,
    default_endpoint,
)

__all__ = [
    "ClientSecretCredential",
    "DefaultLoamzCredential",
    "StaticTokenCredential",
    "TokenCredential",
    "default_endpoint",
]
