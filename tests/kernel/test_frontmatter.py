"""The standard-library codec behind OKF frontmatter: forgiving to read, and what it writes is plain YAML everyone reads the same.

The format's first rule is that a consumer never rejects a file for what it cannot understand — so the reader is tested on what real
producers write (block lists of mappings, flow collections with single quotes, ``|``/``>`` scalars, comments, CRLF) and on
garbage, where the contract is "keep the raw text, read the rest"."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from substrate.documents._frontmatter import dump_mapping, load_mapping


def test_what_producers_write_is_read() -> None:
    text = """type: Memory
title: "A: b"
verified: { by: 'human:ravi', at: 2026-09-13T10:00:00Z }
tags:
  - a
  - b c
sources:
  - file: x.pdf
    pages: [1, 2]
  - file: y.pdf
description: |
  line one
  line two
note: >
  folded a
  folded b
n: 12
f: 1.5
flag: true
nothing:
vendor_specific: keep-me # a comment
"""
    got = load_mapping(text)
    assert got["title"] == "A: b"
    assert got["verified"] == {
        "by": "human:ravi",
        "at": "2026-09-13T10:00:00Z",
    }  # timestamps stay strings
    assert got["tags"] == ["a", "b c"]
    assert got["sources"] == [{"file": "x.pdf", "pages": [1, 2]}, {"file": "y.pdf"}]
    assert (
        got["description"] == "line one\nline two\n"
        and got["note"] == "folded a folded b\n"
    )
    assert (got["n"], got["f"], got["flag"], got["nothing"]) == (12, 1.5, True, None)
    assert got["vendor_specific"] == "keep-me"


def test_a_list_may_sit_at_the_same_indent_as_its_key_and_crlf_is_fine() -> None:
    assert load_mapping("tags:\r\n- a\r\n- b\r\ntype: X\r\n") == {
        "tags": ["a", "b"],
        "type": "X",
    }


def test_a_colon_without_a_space_is_part_of_a_plain_scalar() -> None:
    assert load_mapping("by: human:ravi\nurl: http://h:8080/x") == {
        "by": "human:ravi",
        "url": "http://h:8080/x",
    }


@pytest.mark.parametrize(
    "garbage",
    [
        "key: [1, 2",
        "key: {a: 1",
        'key: "unterminated',
        "key: 'also",
        "- stray\nkey: v",
        "\tkey: v",
        "{{{{",
        "key: [[[",
        "a: b: c",
    ],
)
def test_garbage_never_raises_and_what_can_be_read_is(garbage: str) -> None:
    got = load_mapping("type: Memory\n" + garbage + "\nafter: ok\n")
    assert (
        got["type"] == "Memory"
    )  # the keys either side survive whatever sat between them
    assert got.get("after") == "ok" or "after" in str(got)


@given(st.text(max_size=300))
@settings(max_examples=200, deadline=None)
def test_any_text_at_all_is_accepted(text: str) -> None:
    assert isinstance(load_mapping(text), dict)


# ------------------------------------------------------------------------------------------------------------ round trips

_KEY = st.from_regex(r"[A-Za-z_][A-Za-z0-9_.-]{0,12}", fullmatch=True)
_LEAF = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=40),
)
_VALUE = st.recursive(
    _LEAF,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(st.text(max_size=8), inner, max_size=3),
    ),
    max_leaves=8,
)
_MAPPING = st.dictionaries(_KEY, _VALUE, max_size=6)


@given(_MAPPING)
@settings(max_examples=300, deadline=None)
def test_whatever_is_written_is_read_back_identically(data: dict) -> None:
    assert load_mapping(dump_mapping(data)) == data


_SAFE_TEXT = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=0xD7FF,
        blacklist_categories=("Cs", "Cc", "Zl", "Zp"),
    ),
    max_size=30,
)
_YAML_LEAF = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    _SAFE_TEXT,
)
_YAML_VALUE = st.recursive(
    _YAML_LEAF,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3), st.dictionaries(_SAFE_TEXT, inner, max_size=3)
    ),
    max_leaves=6,
)


@given(st.dictionaries(_KEY, _YAML_VALUE, max_size=5))
@settings(max_examples=200, deadline=None)
def test_what_is_written_means_the_same_to_a_real_yaml_parser(data: dict) -> None:
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(dump_mapping(data) or "{}") == (data or {})


def test_strings_that_yaml_would_reinterpret_are_quoted() -> None:
    data = {
        "a": "true",
        "b": "null",
        "c": "12",
        "d": "2030-01-01T00:00:00Z",
        "e": "no",
        "f": "~",
        "g": "",
        "h": "a: b",
        "i": " x",
    }
    out = dump_mapping(data)
    assert load_mapping(out) == data
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(out) == data
