"""``allowed_tools`` / ``knowledge_base`` on a chat request narrow what the assistant can reach; they cannot widen it or cross tenants."""

from __future__ import annotations

from types import SimpleNamespace

from substrate.workspace.layout import knowledge_collection
from substrate_cloud.monolith.routes.chat import _knowledge_tool_for


def tool(name: str):
    return SimpleNamespace(name=name)


def test_the_knowledge_tool_is_pointed_at_a_base_of_the_callers_own_tenant():
    knowledge = SimpleNamespace()
    tools = [tool("calculator"), tool("knowledge")]
    out = _knowledge_tool_for(tools, knowledge, "acme", "faq")
    names = [t.name for t in out]
    assert names == ["calculator", "knowledge"]  # replaced, not duplicated
    chosen = out[-1]
    assert chosen is not tools[1]
    # the collection is built from the token's tenant plus the base id: no other tenant's prefix is reachable
    assert chosen._collection(None) == knowledge_collection("acme", "faq")  # type: ignore[attr-defined]
    assert chosen._collection(None).startswith("tenants/acme/")  # type: ignore[attr-defined]


def test_an_invalid_base_id_changes_nothing():
    tools = [tool("knowledge")]
    for bad in ("../other", "a/b", ""):
        assert _knowledge_tool_for(tools, SimpleNamespace(), "acme", bad) is tools
