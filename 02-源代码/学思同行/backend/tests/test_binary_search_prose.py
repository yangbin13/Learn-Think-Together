"""Bounded listed-step validation, plus offline Agent/real-tool integration.

Passing this syntax guard is not a semantic certificate: the independent
review still checks the actual midpoint policy, full steps and returned value.
"""
from __future__ import annotations

import json

import pytest

from backend.app.binary_search import binary_search_trace
from backend.app.db import dumps
from backend.app.fact_review import (
    validate_binary_search_trace_prose,
    validate_computation_assumptions,
)
from backend.app.provider import ProviderError
from backend.tests.test_api import events, register, setup
from backend.tests.test_binary_search_trace import install_binary_protocol


VALUES = [1, 2, 7, 7, 9]
ARGUMENTS = {'values': VALUES, 'target': 7, 'occurrence': 'first'}


def computations(occurrence='first'):
    arguments = {**ARGUMENTS, 'values': VALUES.copy(), 'occurrence': occurrence}
    return [{'tool': 'binary_search_trace', 'arguments': arguments,
             'result': binary_search_trace(**arguments)}]


LEGAL_FIRST = (
    '- **初始区间**：[0, 4]\n'
    '- **中点**：2\n'
    '- 保存的候选索引：2\n'
    '- **新区间**：[0, 1]\n'
    '- **中点**：0\n'
    '- **新区间**：[1, 1]\n'
    '- **中点**：1\n'
    '- **新区间**：[2, 1]\n'
    '- **返回**：先前保存的候选索引2，不再进行中点比较。'
)
BAD_FIRST = LEGAL_FIRST.replace(
    '- **返回**：', '- **中点**：2\n- **返回**：')


def test_observed_first_occurrence_error_probes_after_empty_interval():
    actual = computations()
    assert actual[0]['result']['final_interval'] == [2, 1]
    assert actual[0]['result']['index'] == 2
    assert [step['mid'] for step in actual[0]['result']['steps']] == [2, 0, 1]
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose(BAD_FIRST, actual)
    # A correct final index must not excuse an extra, impossible probe.
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_computation_assumptions(BAD_FIRST, actual)


def test_cached_first_match_is_returned_without_an_extra_probe():
    validate_binary_search_trace_prose(LEGAL_FIRST, computations())
    validate_computation_assumptions(LEGAL_FIRST, computations())


@pytest.mark.parametrize('prefix', ['- ', '* ', '+ ', '1. ', '2) ', '3、'])
@pytest.mark.parametrize('bounds,midpoint', [('[2, 1]', 2), ('[0, -1]', 0)])
def test_list_prefixes_do_not_hide_an_empty_interval_probe(prefix, bounds, midpoint):
    text = f'{prefix}__区间__：{bounds}\n{prefix}**中点索引**：{midpoint}'
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose(text, computations())


@pytest.mark.parametrize('midpoint', [-1, 5])
def test_negative_or_above_high_index_is_outside_a_nonempty_interval(midpoint):
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose(
            f'- 当前区间：[0, 4]\n- 中点索引：{midpoint}', computations())


def test_fullwidth_bounds_and_singleton_midpoint_are_recognized():
    validate_binary_search_trace_prose('- 区间：［2，2］\n- 中点：2', computations())
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose('- 区间：［2，2］\n- 中点：1', computations())


def test_midpoint_value_is_not_mistaken_for_an_array_index():
    # Value7 is outside index interval0..1; this line does not assert index7.
    validate_binary_search_trace_prose('- 区间：[0, 1]\n- 中点值：7', computations())


def test_a_legal_upper_midpoint_variant_is_not_rejected_by_a_bounds_only_guard():
    # An explanation may compare a separate upper-midpoint variant. The real
    # tool uses lower mid3 here; checking that policy remains the review's job.
    tool_result = binary_search_trace(list(range(1, 9)), 8)
    assert tool_result['steps'][0]['mid'] == 3
    validate_binary_search_trace_prose(
        '另说明上取整版本：\n- 区间：[0, 7]\n- 中点：4',
        [{'tool': 'binary_search_trace', 'result': tool_result}])


@pytest.mark.parametrize('order', ['any-first', 'first-any'])
@pytest.mark.parametrize('separator', ['\n', '\n## 另一个搜索策略\n'])
def test_any_and_first_search_segments_start_their_own_intervals(order, separator):
    any_text = '- 初始区间：[0, 4]\n- 中点：2\n- 返回：索引2。'
    segments = [any_text, LEGAL_FIRST] if order == 'any-first' else [LEGAL_FIRST, any_text]
    validate_binary_search_trace_prose(separator.join(segments),
                                      computations('any') + computations('first'))


@pytest.mark.parametrize('ending', ['返回', '结束', '终止'])
def test_empty_interval_can_end_and_another_search_start(ending):
    validate_binary_search_trace_prose(
        f'- 区间：[2, 1]\n- {ending}：返回缓存候选。\n'
        '- 初始区间：[0, 4]\n- 中点：2', computations())


@pytest.mark.parametrize('quoted', [
    '> - 区间：[2, 1]\n> - 中点：2',
    '> 区间：[2, 1]\n> 中点：2',
])
def test_markdown_quoted_student_error_is_not_the_tutors_probe(quoted):
    validate_binary_search_trace_prose('学生之前的错误记录：\n' + quoted + '\n' + LEGAL_FIRST,
                                      computations())


def test_a_quoted_error_does_not_exempt_an_actual_later_bad_probe():
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose(
            '> - 区间：[0, 4]\n> - 中点：2\n'
            '- 区间：[2, 1]\n- 中点：2', computations())


@pytest.mark.parametrize('fence', ['```text', '~~~text'])
def test_fenced_example_or_code_does_not_assert_a_prose_probe(fence):
    close = fence[:3]
    validate_binary_search_trace_prose(
        f'{fence}\n- 区间：[2, 1]\n- 中点：2\n{close}\n' + LEGAL_FIRST,
        computations())


def test_fenced_example_does_not_override_the_real_empty_interval():
    with pytest.raises(ProviderError, match='空区间或区间外'):
        validate_binary_search_trace_prose(
            '- 区间：[2, 1]\n```text\n- 区间：[0, 4]\n- 中点：2\n```\n- 中点：2',
            computations())


@pytest.mark.parametrize('other_tools', [[], [{'tool': 'calculate', 'result': {'result': 2}}]])
def test_no_successful_binary_tool_does_not_trigger_the_specific_guard(other_tools):
    # Other review/execution checks may still reject the answer; this guard
    # must not infer a binary-tool trace from an unrelated computation.
    validate_binary_search_trace_prose(BAD_FIRST, other_tools)


QUESTION = (
    '请使用 binary_search_trace 核验数组[1,2,7,7,9]查找目标7的最左匹配轨迹，'
    '按0起算闭区间和下取整中点，给出结果。'
)


def cited_draft(body):
    def answer(messages):
        prompt = messages[0]['content']
        citations = json.loads(prompt.split('可引用证据（位置说明已由服务器标注）：\n', 1)[1]
                               .split('\n实际工具结果：\n', 1)[0])
        computed = next(item for item in citations if item['source_type'] == 'verified_computation')
        return f"本轮通过 binary_search_trace 工具核验：\n{body} [{computed['id']}]"
    return answer


def test_empty_interval_bad_draft_cannot_reach_approval_or_saved_success(setup):
    client, app, provider = setup
    register(client)
    _, reviews, streams = install_binary_protocol(provider, [ARGUMENTS], [cited_draft(BAD_FIRST)])
    result = events(client.post('/api/chat', json={
        'course_id': 'data-structures', 'message': QUESTION, 'mode': 'solve'}))
    assert result[-1][0] == 'error' and '空区间或区间外' in result[-1][1]['message']
    assert reviews == [] and len(streams) == 2
    assert sum(event == 'answer_reset' for event, _ in result) == 1
    assert not any(event == 'done' for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")['n'] == 0


def test_repaired_cached_candidate_still_sends_real_trace_to_independent_review(setup):
    client, app, provider = setup
    register(client)
    _, reviews, streams = install_binary_protocol(
        provider, [ARGUMENTS], [cited_draft(BAD_FIRST), cited_draft(LEGAL_FIRST)])
    result = events(client.post('/api/chat', json={
        'course_id': 'data-structures', 'message': QUESTION, 'mode': 'solve'}))
    assert result[-1][0] == 'done', result[-1]
    assert len(streams) == 2 and len(reviews) == 1
    assert sum(event == 'answer_reset' for event, _ in result) == 1
    computation = reviews[0]['computations'][0]
    assert computation['arguments'] == ARGUMENTS
    assert computation['result']['final_interval'] == [2, 1]
    assert [step['mid'] for step in computation['result']['steps']] == [2, 0, 1]
    assert reviews[0]['question'] == QUESTION
    cited = [item for item in reviews[0]['evidence']
             if item['id'] in reviews[0]['referenced_evidence_ids']]
    assert len(cited) == 1 and cited[0]['source_type'] == 'verified_computation'
    assert json.loads(cited[0]['excerpt']) == computation['result']
    assert result[-1][1]['grounded'] is True
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")['content'] == result[-1][1]['final_text']


def test_legal_list_syntax_does_not_bypass_a_semantic_rejection(setup):
    client, app, provider = setup
    register(client)
    _, reviews, streams = install_binary_protocol(provider, [ARGUMENTS], [cited_draft(LEGAL_FIRST)])
    previous_complete = provider.complete

    async def complete(messages, **kwargs):
        result = await previous_complete(messages, **kwargs)
        if messages[0]['content'].startswith('你是独立事实审查器'):
            result['content'] = dumps({'approved': False, 'issues': [
                {'kind': 'source_support', 'detail': '离线模拟审查明确拒绝；列表语法通过不能替代语义与来源复核。'}]})
        return result

    provider.complete = complete
    result = events(client.post('/api/chat', json={
        'course_id': 'data-structures', 'message': QUESTION, 'mode': 'solve'}))
    assert result[-1][0] == 'error' and '局部事实复核' in result[-1][1]['message']
    assert len(reviews) == len(streams) == 2
    assert all(review['computations'][0]['result']['index'] == 2 for review in reviews)
    assert not any(event == 'done' for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")['n'] == 0
