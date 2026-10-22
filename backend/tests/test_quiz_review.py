import json

import pytest

from backend.app.db import dumps
from backend.app.provider import ProviderError
from backend.app.quiz_review import QUIZ_REVIEW_PREFIX
from backend.tests.quiz_review_helpers import (complete_legacy_quiz_application_decision,
    fake_quiz_application_review, fake_application_basis, BlindReviewTestMixin)
from backend.tests.test_api import setup, register, wait_document


@pytest.mark.parametrize('decisions,success,attempts', [
    ([{'approved': True, 'issues': []}], True, 1),
    ([{'approved': False, 'issues': [{'kind': 'source_support', 'detail': '第3题来源只有标题'}]},
      {'approved': True, 'issues': []}], True, 2),
    ([{'approved': False, 'issues': [{'kind': 'condition', 'detail': '第3题缺剪力符号约定'}]}], False, 2),
    ([ProviderError('模型服务调用失败（HTTP 429）')], False, 1),
    ([{}], False, 1),
])
def test_custom_quiz_review_is_bounded_and_cannot_persist_rejected_or_unreviewed_questions(setup, decisions, success, attempts):
    client, app, provider = setup
    user = register(client)
    cid = client.post('/api/courses', json={'title': '力学来源复核测试'}).json()['id']
    upload = client.post(f'/api/courses/{cid}/documents',
        files={'file': ('book.txt', '均布荷载的等效合力等于荷载强度与覆盖长度的乘积，作用于分布的形心。'.encode())},
        data={'rights_confirmed': 'true'}).json()
    assert wait_document(client, cid, upload['id'])['status'] == 'ready'
    generated, reviews = [], []

    async def complete(messages, **kwargs):
        if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
            reviews.append(json.loads(messages[1]['content']))
            decision = decisions[min(len(reviews) - 1, len(decisions) - 1)]
            if isinstance(decision, Exception):
                raise decision
            return {'content': dumps(complete_legacy_quiz_application_decision(decision, reviews[-1]['questions']))}
        generated.append(messages.copy())
        data = json.loads(messages[1]['content'])
        qs = [{'stem': f'均布荷载的合力原则是什么？问题{i}',
               'options': [{'key': 'A', 'text': '强度乘覆盖长度'}, {'key': 'B', 'text': '强度除长度'},
                           {'key': 'C', 'text': '长度平方'}, {'key': 'D', 'text': '只等于强度'}],
               'answer': 'A', 'explanation': '合力对应荷载强度曲线下面积。',
               'concept': next(iter(data['allowed_concepts']), '均布荷载'), 'source_ids': [data['evidence'][0]['id']],
               'application_basis': None}
              for i in range(3)]
        return {'content': dumps({'questions': qs})}

    provider.complete = complete
    response = client.post(f'/api/courses/{cid}/quizzes', json={'count': 3, 'topic': '均布荷载', 'difficulty': '基础'})
    assert len(generated) == len(reviews) == attempts
    assert reviews[0]['questions'][0]['source_ids'][0] in {d['id'] for d in reviews[0]['evidence']}
    stored = app.state.db.all('SELECT * FROM quizzes WHERE course_id=? AND user_id=?', (cid, user['id']))
    if success:
        assert response.status_code == 200 and len(stored) == 1
        assert all('answer' not in q and 'explanation' not in q for q in response.json()['questions'])
        if attempts == 2:
            assert generated[1][-2]['role'] == 'assistant'
            assert '第3题来源只有标题' in generated[1][-1]['content']
    else:
        assert response.status_code == 502 and not stored


@pytest.fixture
def custom_quiz(setup):
    client, app, _ = setup
    user = register(client)
    course = client.post('/api/courses', json={'title': '逐选项协议边界测试'}).json()
    upload = client.post(f"/api/courses/{course['id']}/documents",
        files={'file': ('array.txt', '数组可以通过索引读取元素。顺序查找不存在的键需要扫描全部元素。'.encode())},
        data={'rights_confirmed': 'true'}).json()
    assert wait_document(client, course['id'], upload['id'])['status'] == 'ready'
    return client, app, user, course


class LedgerProvider(BlindReviewTestMixin):
    """Scripted protocol outcomes, deliberately not a semantic judge."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.calls, self.generations, self.reviews = [], [], []

    async def complete(self, messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        if not messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
            self.calls.append('generate')
            self.generations.append(messages.copy())
            return {'content': dumps({'questions': [
                {'stem': f'数组可以根据什么读取元素？协议测试题{i}',
                 'options': [{'key': key, 'text': text} for key, text in zip('ABCD',
                     ['元素索引', '网络端口', '进程编号', '文件扩展名'])],
                 'answer': 'A', 'explanation': '教材说明数组可以按索引读取元素。',
                 'concept': next(iter(payload['allowed_concepts']), '数组读取'),
                 'source_ids': [payload['evidence'][0]['id']],
                 'application_basis': fake_application_basis([payload['evidence'][0]['id']], i)}
                for i in range(payload['count'])]})}
        self.calls.append('review')
        self.reviews.append(payload)
        # Enough output budget for five complete four-option ledgers, still bounded.
        assert 1600 < kwargs['max_tokens'] <= 5000
        mode = self.decisions[min(len(self.reviews) - 1, len(self.decisions) - 1)]
        value = fake_quiz_application_review(payload['questions'])
        if mode == 'service-error':
            raise ProviderError('模型服务调用失败（HTTP 429）')
        if mode == 'missing-content':
            return {'usage': {'total_tokens': 1}}
        if mode == 'non-object-response':
            return None
        if mode == 'broken-json':
            return {'content': '{'}
        if mode == 'total-only':
            value.pop('question_checks')
        elif mode == 'missing-question':
            value['question_checks'].pop()
        elif mode == 'unknown-id':
            value['question_checks'][0]['question_id'] = 'not-the-generated-question'
        elif mode == 'duplicate-key':
            value['question_checks'][0]['options'][1]['key'] = 'A'
        elif mode == 'uncertain':
            value['question_checks'][0]['options'][1]['verdict'] = 'uncertain'
        elif mode == 'multiple-correct':
            value['question_checks'][0]['options'][1]['verdict'] = 'correct'
        elif mode == 'answer-mismatch':
            value['question_checks'][0]['options'][0]['verdict'] = 'incorrect'
            value['question_checks'][0]['options'][1]['verdict'] = 'correct'
        elif mode == 'unsupported-approved':
            value['question_checks'][0]['source_supported'] = False
        elif mode == 'incomplete-approved':
            value['question_checks'][0]['conditions_complete'] = False
        elif mode == 'partial-rejection':
            value.update(approved=False, issues=[{'kind': 'condition', 'detail': '第1题条件不完整。'}])
            value['question_checks'].pop()
        elif mode == 'stale-review':
            value = fake_quiz_application_review(self.reviews[0]['questions'])
        elif mode == 'semantic-overlap':
            value.update(approved=False, issues=[{'kind': 'logic', 'detail': '第1题A与B在条件下可能同时成立，不能作为单选。'}])
            value['question_checks'][0]['options'][1].update(verdict='correct', reason='测试替身明确模拟B与A重叠，需重生成。')
        elif mode == 'semantic-source':
            value.update(approved=False, issues=[{'kind': 'source_support', 'detail': '第1题引用原文尚不能支持该选项结论。'}])
            value['question_checks'][0]['source_supported'] = False
        else:
            assert mode == 'approved'
        return {'content': dumps(value)}


@pytest.mark.parametrize('fatal', [
    'service-error', 'missing-content', 'non-object-response', 'broken-json', 'total-only',
    'missing-question', 'unknown-id', 'duplicate-key', 'uncertain', 'multiple-correct',
    'answer-mismatch', 'unsupported-approved', 'incomplete-approved', 'partial-rejection',
])
def test_fatal_review_never_spends_a_regeneration_or_persists_any_student_result(custom_quiz, fatal):
    client, app, user, course = custom_quiz
    provider = LedgerProvider([fatal])
    app.state.provider.complete = provider.complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '基础'})
    assert response.status_code == 502
    assert provider.calls == ['generate', 'review']
    if fatal in {'missing-content', 'non-object-response', 'broken-json', 'total-only'}:
        assert '应用协议无效' in response.json()['detail']
    elif fatal != 'service-error':
        assert '逐题复核协议无效' in response.json()['detail']
    for table in ['quizzes', 'mastery', 'mistakes']:
        assert app.state.db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE course_id=? AND user_id=?',
                               (course['id'], user['id']))['n'] == 0


@pytest.mark.parametrize('second', ['service-error', 'broken-json', 'total-only', 'stale-review', 'semantic-source'])
def test_failed_second_review_never_reuses_first_ids_or_gets_a_third_generation(custom_quiz, second):
    client, app, user, course = custom_quiz
    provider = LedgerProvider(['semantic-overlap', second])
    app.state.provider.complete = provider.complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '进阶'})
    assert response.status_code == 502
    assert provider.calls == ['generate', 'review', 'generate', 'review']
    assert {q['id'] for q in provider.reviews[0]['questions']}.isdisjoint(
        q['id'] for q in provider.reviews[1]['questions'])
    assert 'A与B' in provider.generations[1][-1]['content']
    assert '"verdict":"correct"' in provider.generations[1][-1]['content']
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=? AND user_id=?',
                            (course['id'], user['id']))['n'] == 0


@pytest.mark.parametrize('count,decisions', [(5, ['approved']), (3, ['semantic-overlap', 'approved'])])
def test_only_current_complete_approved_ledger_can_persist_and_public_payload_keeps_answers_hidden(custom_quiz, count, decisions):
    client, app, user, course = custom_quiz
    provider = LedgerProvider(decisions)
    app.state.provider.complete = provider.complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': count, 'difficulty': '基础'})
    assert response.status_code == 200
    assert provider.calls == ['generate', 'review'] * len(decisions)
    public = response.json()['questions']
    assert len(public) == count and all(not {'answer', 'explanation', 'source_ids'} & q.keys() for q in public)
    assert {q['id'] for q in public} == {q['id'] for q in provider.reviews[-1]['questions']}
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=? AND user_id=?',
                            (course['id'], user['id']))['n'] == 1
