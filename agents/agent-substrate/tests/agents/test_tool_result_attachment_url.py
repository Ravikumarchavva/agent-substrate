"""_attachment_url — tool-result image → wire attachment URL.

Covers only the pure mapping in agents/runtime/context/tool.py, not the
surrounding journaled-effect machinery: a durably-backed ImageBlock (has
storage_key) must produce a bare `object:` scheme rather than an inlined data
URI, since inlining is what made every image get re-stored, base64-inflated,
on every tool call that returned it.
"""

from __future__ import annotations

from substrate.agents.runtime.context.tool import _attachment_url
from substrate.kernel.core.content import ImageBlock


def test_image_with_storage_key_becomes_a_bare_object_scheme():
    img = ImageBlock(
        data=b"PNGBYTES",
        media_type="image/png",
        storage_key="users/u1/rag/f9/p1-0.png",
    )

    assert _attachment_url(img) == "object:users/u1/rag/f9/p1-0.png"


def test_image_without_storage_key_still_inlines_as_a_data_uri():
    """Nothing durable behind it (e.g. a matplotlib chart with no file store
    configured) — must survive in the log the old way, not vanish."""
    img = ImageBlock(data=b"PNGBYTES", media_type="image/png")

    url = _attachment_url(img)

    assert url.startswith("data:image/png;base64,")
    assert "storage_key" not in url


def test_object_scheme_url_does_not_encode_the_key():
    """The key rides raw — same convention as `sandbox:<path>`, whose
    frontend resolver strips the scheme and encodes once itself. Encoding
    here too would double-encode every `/` by the time it reaches a real URL
    (see the module comment above _OBJECT_URL_TEMPLATE)."""
    img = ImageBlock(
        data=b"x", media_type="image/png", storage_key="users/u 1/rag/f 9/p1.png"
    )

    assert _attachment_url(img) == "object:users/u 1/rag/f 9/p1.png"


def test_data_uri_defaults_to_png_when_media_type_is_empty():
    img = ImageBlock(data=b"x", media_type="")

    assert _attachment_url(img).startswith("data:image/png;base64,")
