"""Client-side TLS for calls between this system's own services (API, controller, workers)."""

from __future__ import annotations

import ssl

from loamz.server.data.kernel.settings import Settings


def internal_client_context(settings: Settings) -> ssl.SSLContext | bool:
    """Verification for outbound internal calls: the operator's CA (else the system store), TLS 1.2+
    and, when configured, this service's own certificate for mutual TLS. Plain-HTTP loopback
    addresses ignore it."""
    context = ssl.create_default_context(
        cafile=str(settings.internal_ca_file) if settings.internal_ca_file else None
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if settings.internal_cert_file and settings.internal_key_file:
        context.load_cert_chain(str(settings.internal_cert_file), str(settings.internal_key_file))
    return context
