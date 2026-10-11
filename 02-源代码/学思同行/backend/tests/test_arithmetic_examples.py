"""Sourced ordinal identifiers are distinct from computed numeric examples."""
import pytest

from backend.app.fact_review import validate_computation_assumptions
from backend.app.provider import ProviderError


@pytest.mark.parametrize('text', [
    '例如，在物理页79中，bad和dab的散列值都是8，因此它们都被存储在第8个槽位中。槽位8的内容如下。',
    '例如，原页79图中的第8个槽位保存两组键值，第8格仍存在原值。',
    '若查看第12个槽位，会看到键1和值7；这些数字是标识。',
    '例如，图中有4个已给定的键，标注分别是1、2、3、4。',
])
def test_page_slot_and_given_labels_do_not_invent_arithmetic(text):
    validate_computation_assumptions(text, [])


@pytest.mark.parametrize('text', [
    '若有100个域和10个对象，合计1000个表项。',
    '如果有10个对象，每项4字节，需要40字节。',
    '例如100 KB换算得到102400 bytes。',
    '假设每项4字节，100个对象共需400字节。',
    '若有100个域和10^5个对象，则10^8个表项。',
    '例如2**10等于1024。',
])
def test_derived_quantities_still_require_an_actual_calculator(text):
    with pytest.raises(ProviderError, match='确定性工具核验'):
        validate_computation_assumptions(text, [])
    validate_computation_assumptions(text, [{'tool': 'calculate', 'result': {'value': 1000}}])


def test_sources_and_other_successful_tools_are_not_arithmetic_verification():
    text = '若有100个域和10个对象，合计1000个表项[1]。'
    with pytest.raises(ProviderError, match='确定性工具核验'):
        validate_computation_assumptions(text, [{'tool': 'shortest_path', 'result': {}}])
