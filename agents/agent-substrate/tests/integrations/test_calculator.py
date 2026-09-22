"""CalculatorTool — arithmetic works, code execution is impossible."""

from __future__ import annotations

import pytest

from substrate.capabilities.tools.compute.calculator import CalculatorTool, safe_eval


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("(42 * 3) / 7", 18.0),
        ("2 ** 10", 1024),
        ("1 + 2 * 3 - 4", 3),
        ("10 % 3", 1),
        ("7 // 2", 3),
        ("-(3 + 4)", -7),
        ("2.5 * 4", 10.0),
    ],
)
def test_safe_eval_arithmetic(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "().__class__.__base__.__subclasses__()",  # the classic sandbox escape
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "a + 1",  # bare name
        "[x for x in range(3)]",
        "lambda: 1",
        "(1).__class__",
        "9 ** 9 ** 9",  # exponent bomb
        "'a' * 3",  # non-numeric literal
        "True + 1",  # bool is not a permitted numeric literal
    ],
)
def test_safe_eval_rejects_non_arithmetic(expr):
    with pytest.raises((ValueError, SyntaxError)):
        safe_eval(expr)


async def test_execute_returns_result():
    result = await CalculatorTool().execute(expression="6 * 7")
    assert not result.is_error
    assert result.content[0].text == "42"


async def test_execute_rejects_escape_without_executing():
    result = await CalculatorTool().execute(
        expression="().__class__.__base__.__subclasses__()"
    )
    assert result.is_error
    assert "Error" in result.content[0].text
