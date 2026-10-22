"""Anything with a token is a credential: strings, functions, objects."""

import asyncio

from loamz.core.credentials import StaticTokenCredential, as_credential


class Provider:
    def get_token(self):
        return "from-object"

    async def aget_token(self):
        return "from-object"


def test_strings_functions_and_objects_all_become_credentials():
    assert as_credential("fixed").get_token() == "fixed"
    assert as_credential(lambda: "from-fn").get_token() == "from-fn"
    assert as_credential(Provider()).get_token() == "from-object"
    assert as_credential(None) is None


def test_a_static_credential_serves_async_callers_too():
    assert asyncio.run(StaticTokenCredential("t").aget_token()) == "t"
