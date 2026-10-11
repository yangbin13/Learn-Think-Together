import pytest

from backend.app.fact_review import validate_computation_assumptions
from backend.app.provider import ProviderError


def test_a_separately_validated_unsolved_question_is_not_an_arithmetic_claim():
    question = '如果一个算法固定需要100步，另一个需N步，你会写出什么相等式？'
    validate_computation_assumptions(question, [], arithmetic_claim_text='')
    # Ordinary numerical examples retain the existing check.
    with pytest.raises(ProviderError, match='确定性工具'):
        validate_computation_assumptions('若有100个域和10个对象，合计1000个表项。', [])


def test_socratic_numeric_feedback_does_not_borrow_an_unsolved_question_exception():
    feedback = '若有100个域和10^5个对象，则10^8个表项。'
    with pytest.raises(ProviderError, match='确定性工具'):
        validate_computation_assumptions(feedback + '你下一步要核对什么？', [],
                                        arithmetic_claim_text=feedback)


def test_reference_checks_still_use_the_entire_socratic_reply():
    computation = {'tool': 'hydrostatic_vertical_plate',
                   'result': {'moment_about_supplied_reference': None}}
    with pytest.raises(ProviderError, match='参照点'):
        validate_computation_assumptions('底部有一个铰接支座，它提供反力，你会怎样取矩？',
                                        [computation], arithmetic_claim_text='')
