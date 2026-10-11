import pytest

from backend.app.practice import validate_generated_questions
from backend.app.provider import ProviderError


def generated(explanation):
    return {"questions": [{
        "stem": "已知factorial(1)=1，按递归定义计算factorial(2)的正确等式是哪项？",
        "options": [
            {"key": "A", "text": "factorial(2)=2×factorial(1)=2×1=2"},
            {"key": "B", "text": "factorial(2)=2+factorial(1)=3"},
            {"key": "C", "text": "factorial(2)=factorial(1)=1"},
            {"key": "D", "text": "factorial(2)=2"},
        ],
        "answer": "A",
        "explanation": explanation,
        "concept": "递归",
        "source_ids": ["source-1"],
    }]}


def test_concatenated_recursive_equations_without_operator_are_rejected():
    with pytest.raises(ProviderError, match="相邻等式缺少明确运算符"):
        validate_generated_questions(
            generated("factorial(2)=2 factorial(1)=2，随后factorial(3)=3 factorial(2)=6。"),
            1,
            {"source-1"},
        )


def test_explicit_recursive_multiplication_chain_is_accepted():
    result = validate_generated_questions(
        generated("factorial(2)=2×factorial(1)=2×1=2。"),
        1,
        {"source-1"},
    )
    assert len(result) == 1
    assert result[0]["answer"] == "A"
