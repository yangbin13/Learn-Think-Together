"""Offline algorithm/oracle and real Agent-tool plumbing; no model evaluation."""
from __future__ import annotations

import copy
import json
import random

import pytest

from backend.app.binary_search import binary_search_trace
from backend.app.db import dumps
from backend.app.fact_review import validate_failed_binary_search_claims
from backend.app.provider import ProviderError
from backend.app.tools import DETERMINISTIC, TOOL_SPECS
from backend.tests.test_api import events, register, setup


@pytest.mark.parametrize('target,found,index,last_action', [
    (8, True, 7, 'return_match'), (9, False, None, 'search_right')])
def test_eight_values_searches_end_target_with_four_exact_probes(target, found, index, last_action):
    result = binary_search_trace(list(range(1, 9)), target)
    assert result['found'] is found and result['index'] == index
    assert result['comparison_count'] == result['worst_case_probe_bound'] == 4
    assert [step['mid'] for step in result['steps']] == [3, 5, 6, 7]
    assert [step['mid_value'] for step in result['steps']] == [4, 6, 7, 8]
    assert [step['interval'] for step in result['steps']] == [[0, 7], [4, 7], [6, 7], [7, 7]]
    assert [step['probe'] for step in result['steps']] == [1, 2, 3, 4]
    assert result['steps'][-1]['action'] == last_action
    assert result['steps'][-1]['next_interval'] == (None if found else [8, 7])
    assert result['termination_reason'] == ('match' if found else 'empty_interval')


@pytest.mark.parametrize('target,expected_values,index', [(4, [4], 3), (6, [4, 6], 5)])
def test_reported_intermediate_values_are_not_the_original_end_target(target, expected_values, index):
    result = binary_search_trace(list(range(1, 9)), target)
    assert result['found'] is True and result['index'] == index
    assert result['comparison_count'] == len(expected_values)
    assert [step['mid_value'] for step in result['steps']] == expected_values


@pytest.mark.parametrize('occurrence', ['any', 'first'])
def test_empty_input_has_no_probe_and_zero_bound(occurrence):
    result = binary_search_trace([], 1, occurrence)
    assert result['found'] is False and result['index'] is None
    assert result['steps'] == [] and result['comparison_count'] == result['worst_case_probe_bound'] == 0
    assert result['final_interval'] == [0, -1] and result['termination_reason'] == 'empty_interval'


@pytest.mark.parametrize('target,found,final_interval', [(7, True, [0, 0]), (6, False, [0, -1]), (8, False, [1, 0])])
def test_singleton_found_or_missing_needs_one_probe(target, found, final_interval):
    result = binary_search_trace([7], target)
    assert result['found'] is found and result['index'] == (0 if found else None)
    assert result['comparison_count'] == 1 and result['steps'][0]['mid_value'] == 7
    assert result['final_interval'] == final_interval


def test_duplicate_any_can_return_middle_while_first_returns_leftmost():
    values = [1, 2, 2, 2, 3]
    any_match = binary_search_trace(values, 2)
    first = binary_search_trace(values, 2, 'first')
    assert any_match['found'] and any_match['index'] == 2 and any_match['comparison_count'] == 1
    assert any_match['first_occurrence_guaranteed'] is False
    assert first['index'] == 1 and first['first_occurrence_guaranteed'] is True
    assert [step['action'] for step in first['steps']] == ['keep_match_search_left', 'search_right', 'keep_match_search_left']
    assert first['termination_reason'] == 'empty_interval'


def test_all_equal_values_keep_first_semantics_explicit():
    assert binary_search_trace([3] * 8, 3)['index'] == 3
    first = binary_search_trace([3] * 8, 3, 'first')
    assert first['index'] == 0 and first['comparison_count'] == 3
    assert binary_search_trace([3] * 8, 4, 'first')['found'] is False


def test_finite_fractional_values_and_inclusive_value_bounds_are_supported():
    values = [-1e15, -0.5, 0, 0.25, 1e15]
    for index, value in enumerate(values):
        result = binary_search_trace(values, value, 'first')
        assert result['found'] is True and result['index'] == index
        json.dumps(result, allow_nan=False)


def test_fixed_seed_sorted_arrays_and_duplicates_match_independent_linear_oracle():
    rng = random.Random(20261004)
    lengths = [0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64, 255, 256]
    lengths += [rng.randint(0, 256) for _ in range(50)]
    saw_duplicate = saw_found = saw_missing = False
    for length in lengths:
        values = sorted(rng.randint(-40, 40) for _ in range(length))
        saw_duplicate |= len(set(values)) < len(values)
        targets = [-41, 41, rng.randint(-40, 40)]
        if values:
            targets.extend([values[0], values[-1], rng.choice(values)])
        before = values.copy()
        for target in targets:
            matches = [index for index, value in enumerate(values) if value == target]
            saw_found |= bool(matches)
            saw_missing |= not matches
            for occurrence in ('any', 'first'):
                result = binary_search_trace(values, target, occurrence)
                assert result['found'] == bool(matches)
                if not matches:
                    assert result['index'] is None
                elif occurrence == 'first':
                    assert result['index'] == matches[0]
                else:
                    assert result['index'] in matches
                assert result['comparison_count'] == len(result['steps']) <= length.bit_length()
                assert [step['probe'] for step in result['steps']] == list(range(1, len(result['steps']) + 1))
                assert all(values[step['mid']] == step['mid_value'] for step in result['steps'])
                assert values == before and result['values'] == before and result['target'] == target
    assert saw_duplicate and saw_found and saw_missing


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), float('-inf'), '1', None, [], {}, 1e15 + 1, -1e15 - 1])
def test_invalid_numeric_target_is_rejected_even_on_empty_input(bad):
    with pytest.raises(ValueError, match='有限数值'):
        binary_search_trace([], bad)


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), float('-inf'), '1', None, [], {}, 1e15 + 1, -1e15 - 1])
def test_invalid_array_element_is_not_coerced_or_counted(bad):
    with pytest.raises(ValueError, match='有限数值'):
        binary_search_trace([bad], 1)


@pytest.mark.parametrize('bad', [None, (), (1, 2), '1,2', {}, [0] * 257])
def test_nonlist_or_excessive_input_is_rejected(bad):
    with pytest.raises(ValueError, match='0–256'):
        binary_search_trace(bad, 1)


@pytest.mark.parametrize('bad', [[3, 1, 2], [2, 1], [1, 2, 1]])
def test_unsorted_input_is_rejected_instead_of_sorted_or_treated_as_missing(bad):
    before = bad.copy()
    with pytest.raises(ValueError, match='当前输入无序'):
        binary_search_trace(bad, 99)
    assert bad == before


@pytest.mark.parametrize('occurrence', ['', 'last', 'FIRST', True, 1, None, [], {}])
def test_occurrence_mode_must_be_explicit_supported_string(occurrence):
    with pytest.raises(ValueError, match='occurrence'):
        binary_search_trace([1, 1], 1, occurrence)


def test_maximum_size_is_accepted_with_validation_excluded_from_probe_count():
    values = list(range(256))
    found = binary_search_trace(values, 127)
    missing = binary_search_trace(values, 256)
    assert found['comparison_count'] == 1  # 255 order checks are not probes.
    assert missing['comparison_count'] == missing['worst_case_probe_bound'] == 9


def test_returned_input_copy_and_each_invocation_are_isolated():
    values = list(range(1, 9))
    before = values.copy()
    result = binary_search_trace(values, 8)
    other = binary_search_trace(values, 8)
    assert values == before and result['values'] is not values
    result['values'].append(99)
    result['steps'][0]['interval'][0] = 100
    assert values == before and other['values'] == before
    assert other['steps'][0]['interval'] == [0, 7]
    values.append(9)
    assert other['values'] == before


def test_registry_schema_and_metadata_preserve_original_input_and_counting_contract():
    specs = [spec['function'] for spec in TOOL_SPECS if spec['function']['name'] == 'binary_search_trace']
    assert len(specs) == 1 and DETERMINISTIC['binary_search_trace'] is binary_search_trace
    schema = specs[0]['parameters']
    assert schema['additionalProperties'] is False and set(schema['required']) == {'values', 'target'}
    assert set(schema['properties']) == {'values', 'target', 'occurrence'}
    assert schema['properties']['values']['maxItems'] == 256
    assert schema['properties']['values']['items'] == {'type': 'number', 'minimum': -1e15, 'maximum': 1e15}
    assert schema['properties']['target'] == {'type': 'number', 'minimum': -1e15, 'maximum': 1e15}
    assert schema['properties']['occurrence']['enum'] == ['any', 'first']
    assert '不含O(N)输入检查' in specs[0]['description'] and '原始数组与目标' in specs[0]['description']
    result = DETERMINISTIC['binary_search_trace'](values=list(range(1, 9)), target=4)
    assert result['comparison_count'] == 1
    assert result['comparison_unit'] == 'one target-versus-mid-value three-way probe'
    assert result['interval_convention'] == 'zero_based_closed'
    assert result['midpoint_rule'] == 'low + floor((high-low)/2)'
    assert any('不计循环条件、O(N)有序性验证或复制开销' in assumption for assumption in result['assumptions'])
    assert result['verified_by'] and result['first_occurrence_guaranteed'] is False


def install_binary_protocol(provider, arguments, drafts, *, include_material=False):
    """Script only provider decisions; execute the real registry and Agent graph."""
    planners, reviews, streams = [], [], []

    async def complete(messages, tools=None, **kwargs):
        if messages[0]['content'].startswith('你是独立事实审查器'):
            reviews.append(json.loads(messages[1]['content']))
            return {'content': dumps({'approved': True, 'issues': []}), 'model': 'offline-review-model'}
        planners.append(copy.deepcopy(messages))
        tool_names = {tool['function']['name'] for tool in tools or []}
        completed = sum(message['role'] == 'tool' and message['tool_call_id'].startswith('binary-') for message in messages)
        if completed >= len(arguments):
            return {'content': 'DONE', 'tool_calls': [], 'model': 'offline-planner-model'}
        assert 'binary_search_trace' in tool_names
        calls = []
        if include_material and not completed:
            calls.append({'id': 'material-stack', 'name': 'search_materials', 'arguments': dumps({'query': '栈 后进先出', 'limit': 3})})
        calls.append({'id': f'binary-{completed}', 'name': 'binary_search_trace', 'arguments': dumps(arguments[completed])})
        return {'tool_calls': calls, 'model': 'offline-planner-model'}

    async def stream(messages, **kwargs):
        streams.append(copy.deepcopy(messages))
        draft = drafts[min(len(streams) - 1, len(drafts) - 1)]
        yield {'text': draft(messages) if callable(draft) else draft, 'model': 'offline-teaching-model'}

    provider.complete, provider.stream = complete, stream
    return planners, reviews, streams


def tool_events(result):
    return [payload for event, payload in result if event == 'trace' and payload['node'] == 'binary_search_trace']


def test_fake_planner_executes_real_tool_and_review_receives_original_array_target_and_sources(setup):
    client, app, provider = setup
    register(client)
    arguments = {'values': list(range(1, 9)), 'target': 8, 'occurrence': 'any'}

    def answer(messages):
        citations = json.loads(messages[0]['content'].split('可引用证据（位置说明已由服务器标注）：\n', 1)[1].split('\n实际工具结果：\n', 1)[0])
        computed = next(citation for citation in citations if citation['source_type'] == 'verified_computation')
        material = next(citation for citation in citations if citation['source_type'] != 'verified_computation' and '后进先出' in citation['excerpt'])
        return (f"本轮通过 binary_search_trace 工具核验：原数组[1,2,3,4,5,6,7,8]查找目标8，"
                f"依次探测中点值4、6、7、8，共4次，命中0起算下标7 [{computed['id']}]。"
                f"教材中栈遵守后进先出规则 [{material['id']}]。")

    planners, reviews, _ = install_binary_protocol(provider, [arguments], [answer], include_material=True)
    question = ('请使用 binary_search_trace 核验升序数组[1,2,3,4,5,6,7,8]查找目标8的探测次数。'
                '我追踪到先比较4，再比较6，这仍是同一次查找；按0起算闭区间和下取整中点。也说明教材中栈的访问规则。')
    result = events(client.post('/api/chat', json={'course_id': 'data-structures', 'message': question, 'mode': 'solve'}))
    assert result[-1][0] == 'done', result[-1]
    assert len(planners) == 2 and [trace['status'] for trace in tool_events(result)] == ['completed']
    citations = next(payload for event, payload in result if event == 'citations')['items']
    computed = next(citation for citation in citations if citation['source_type'] == 'verified_computation')
    actual = json.loads(computed['excerpt'])
    assert actual['values'] == arguments['values'] and actual['target'] == 8 and actual['comparison_count'] == 4
    assert [step['mid_value'] for step in actual['steps']] == [4, 6, 7, 8]
    assert len(reviews) == 1 and reviews[0]['question'] == question and reviews[0]['tool_failures'] == []
    assert len(reviews[0]['computations']) == 1
    computation = reviews[0]['computations'][0]
    assert computation['tool'] == 'binary_search_trace' and computation['arguments'] == arguments
    assert computation['result'] == actual
    material = next(citation for citation in reviews[0]['evidence'] if citation['source_type'] != 'verified_computation')
    assert material['chunk_id'] and '后进先出' in material['excerpt']
    assert app.state.db.one('SELECT content FROM messages WHERE role=\'assistant\'')['content'] == result[-1][1]['final_text']


def test_multiple_binary_search_boundary_results_are_supported_by_tool_cards_without_textbook_claims(setup):
    client, app, provider = setup
    register(client)
    arguments = [
        {'values': list(range(1, 9)), 'target': 9, 'occurrence': 'any'},
        {'values': [], 'target': 9, 'occurrence': 'any'},
        {'values': [8], 'target': 8, 'occurrence': 'any'},
    ]

    def answer(messages):
        citations = json.loads(messages[0]['content'].split('可引用证据（位置说明已由服务器标注）：\n', 1)[1]
                               .split('\n实际工具结果：\n', 1)[0])
        computed = [item for item in citations if item['source_type'] == 'verified_computation']
        assert len(computed) == 3
        ids = [item['id'] for item in computed]
        return (f'本次工具逐个核验：数组[1,2,3,4,5,6,7,8]查9未找到，探测4、6、7、8共4次 [{ids[0]}]；'
                f'空数组0次 [{ids[1]}]；数组[8]查8在索引0一次命中 [{ids[2]}]。')

    _, reviews, streams = install_binary_protocol(provider, arguments, [answer])
    question = '请用工具核对三种边界：数组[1,2,3,4,5,6,7,8]查9、空数组查9、单元素[8]查8，各自结果和比较次数是什么？'
    result = events(client.post('/api/chat', json={'course_id': 'data-structures', 'message': question, 'mode': 'solve'}))
    assert result[-1][0] == 'done', result[-1]
    assert len(streams) == 1 and len(reviews) == 1
    assert len(reviews[0]['computations']) == 3
    assert [item['arguments'] for item in reviews[0]['computations']] == arguments
    assert [item['result']['comparison_count'] for item in reviews[0]['computations']] == [4, 0, 1]
    assert [item['result']['found'] for item in reviews[0]['computations']] == [False, False, True]
    final_citations = next(payload for name, payload in result if name == 'citations')['items']
    assert len(final_citations) == 3
    assert all(item['source_type'] == 'verified_computation' for item in final_citations)
    assert '教材' not in result[-1][1]['final_text']
    assert app.state.db.one('SELECT content FROM messages WHERE role=\'assistant\'')['content'] == result[-1][1]['final_text']


UNSORTED_QUESTION = '请使用 binary_search_trace 核验数组[3,1,2]中目标3是否存在，不能重排原数组。'
FALSE_VERIFIED = '本轮通过 binary_search_trace 工具核验结果：当前数组中目标不存在。'
HONEST_FAILURE = 'binary_search_trace 未完成核验：原数组无序，不适用二分查找。缺少成功计算依据，不能据此断言目标不存在。'


def test_unsorted_failed_execution_never_creates_success_citation_or_saves_forged_verification(setup):
    client, app, provider = setup
    register(client)
    bad = {'values': [3, 1, 2], 'target': 3}
    planners, reviews, streams = install_binary_protocol(provider, [bad, bad], [FALSE_VERIFIED])
    result = events(client.post('/api/chat', json={'course_id': 'data-structures', 'message': UNSORTED_QUESTION, 'mode': 'solve'}))
    assert result[-1][0] == 'error' and '成功执行结果' in result[-1][1]['message']
    assert len(planners) == len(streams) == 2 and reviews == []
    assert "学生报告了具体数组与二分查找结果时，必须对原数组和目标调用binary_search_trace" in planners[0][0]["content"]
    assert [trace['status'] for trace in tool_events(result)] == ['failed', 'failed']
    assert all('当前输入无序' in trace['detail'] for trace in tool_events(result))
    assert not any(event in {'done', 'citations'} for event, _ in result)
    assert sum(event == 'answer_reset' for event, _ in result) == 1
    assert app.state.db.one('SELECT COUNT(*) AS n FROM messages WHERE role=\'assistant\'')['n'] == 0


def test_unsorted_failure_can_be_reported_honestly_but_review_has_no_success_computation(setup):
    client, app, provider = setup
    register(client)
    bad = {'values': [3, 1, 2], 'target': 3}
    _, reviews, _ = install_binary_protocol(provider, [bad, bad], [HONEST_FAILURE])
    result = events(client.post('/api/chat', json={'course_id': 'data-structures', 'message': UNSORTED_QUESTION, 'mode': 'solve'}))
    assert result[-1][0] == 'done', result[-1]
    assert result[-1][1]['final_text'] == HONEST_FAILURE and result[-1][1]['grounded'] is False
    assert [trace['status'] for trace in tool_events(result)] == ['failed', 'failed']
    assert len(reviews) == 1 and reviews[0]['computations'] == []
    assert [item['tool'] for item in reviews[0]['tool_failures']] == ['binary_search_trace', 'binary_search_trace']
    assert next(payload for event, payload in result if event == 'citations')['items'] == []
    assert app.state.db.one('SELECT content FROM messages WHERE role=\'assistant\'')['content'] == HONEST_FAILURE


def test_failed_unsorted_binary_search_cannot_be_described_as_an_executed_trace():
    with pytest.raises(ProviderError, match="没有生成有效搜索轨迹"):
        validate_failed_binary_search_claims(
            "数组无序时，中间值如9或1比较后会返回不存在。", [], {"binary_search_trace"})
    validate_failed_binary_search_claims(
        "工具拒绝了无序输入，没有生成有效轨迹；题给数组本身包含7，不能据此说目标不存在。",
        [], {"binary_search_trace"})
