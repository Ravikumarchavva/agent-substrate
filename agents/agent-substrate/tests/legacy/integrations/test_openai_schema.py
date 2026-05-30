from __future__ import annotations

from pydantic import BaseModel

from ravi.adapters.llm.openai.openai_client import _normalize_strict_json_schema


def test_normalize_strict_json_schema_sets_additional_properties_false() -> None:
    class NestedSchema(BaseModel):
        city: str

    class RootSchema(BaseModel):
        vendor: str
        nested: NestedSchema

    raw_schema = RootSchema.model_json_schema()
    normalized = _normalize_strict_json_schema(raw_schema)

    assert normalized["additionalProperties"] is False
    assert normalized["$defs"]["NestedSchema"]["additionalProperties"] is False
