"""Offline routing and truthful creation metadata, not question-quality proof."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.db import dumps, loads, now
from backend.app.knowledge_map import derive_textbook_tree, extracted_graph
from backend.app.quiz_review import QUIZ_REVIEW_PREFIX
from backend.tests.quiz_review_helpers import (fake_quiz_application_review,
    fake_application_basis, BlindReviewTestMixin)


class RoutedQuizProvider(BlindReviewTestMixin):
    configured = True

    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.reject_first = False
        self.malformed_first = False
        self.return_models = True

    async def complete(self, messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        stage = 'review' if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX) else 'generate'
        self.calls.append({'stage': stage, 'kwargs': kwargs, 'payload': payload, 'messages': messages})
        number = sum(call['stage'] == stage for call in self.calls)
        if stage == 'review':
            value = fake_quiz_application_review(payload['questions'])
            if self.reject_first and number == 1:
                value.update(approved=False, issues=[{'kind': 'source_support', 'detail': '测试替身模拟来源尚不支持第1题，须重生成。'}])
                value['question_checks'][0]['source_supported'] = False
        else:
            assert messages[0]['content'].startswith('根据提供的真实教材出单选题')
            source = payload['evidence'][0]['id']
            value = {'questions': [
                {'stem': f'按教材，数组可以根据什么读取元素？离线题{i}稿{number}',
                 'options': [{'key': key, 'text': text} for key, text in zip('ABCD',
                     ['索引', '端口', '进程号', '文件扩展名'])],
                 'answer': 'A', 'explanation': '教材说明数组可以通过索引读取元素。',
                 'concept': payload['source_allowed_concepts'][source][0], 'source_ids': [source],
                 'application_basis': fake_application_basis([source], i)}
                for i in range(payload['count'])]}
            if self.malformed_first and number == 1:
                value = {'questions': []}
        response = {'content': dumps(value)}
        if self.return_models:
            # Deliberately different from configured IDs: the response must retain
            # upstream metadata instead of pretending configuration is evidence.
            response['model'] = f'observed-{stage}-{number}'
        return response

    async def close(self):
        pass


@pytest.fixture
def routed_app(tmp_path, monkeypatch):
    settings = config.Settings(root=tmp_path, model='test-main-model', embedding_enabled=False,
                               seed_file=tmp_path / 'absent-catalog.json')
    # main creates a module app at import; keep that construction and this test's
    # entire lifespan in a temporary workspace, never the user's real database.
    with monkeypatch.context() as import_guard:
        import_guard.setattr(config.Settings, 'from_env', classmethod(lambda cls: settings))
        from backend.app.main import create_app
    provider = RoutedQuizProvider(settings)
    app = create_app(settings, provider)
    with TestClient(app) as client:
        user = client.post('/api/auth/register', json={
            'username': 'routing_student', 'password': 'offline-test-password', 'display_name': '路由测试'}).json()
        course = client.post('/api/courses', json={'title': '离线出题路由课程'}).json()
        db = app.state.db
        db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count) "
                   "VALUES ('route-book',?,?, '离线正文','fixture.txt','/unused/fixture.txt','ready','uploaded_txt','test',?,2)",
                   (course['id'], user['id'], now()))
        db.execute("INSERT INTO chunks VALUES ('route-source','route-book',?,?,2,'第1章 数组 / 1.1 索引读取',?,NULL)",
                   (course['id'], user['id'], '数组可以通过索引读取元素。'))
        nodes, _ = extracted_graph(derive_textbook_tree(
            db.all('SELECT * FROM documents WHERE course_id=?', (course['id'],)),
            db.all('SELECT * FROM chunks WHERE course_id=?', (course['id'],))))
        for node in nodes:
            db.execute('INSERT INTO concepts VALUES (?,?,?,?,?)',
                       (node['id'], course['id'], node['label'], node['chapter'], node['source']))
        yield client, app, provider, course


def create_custom(client, course):
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '基础'})
    assert response.status_code == 200, response.text
    return response.json()


def test_quiz_override_changes_generation_only_and_returns_actual_response_models(routed_app):
    client, app, provider, course = routed_app
    settings = app.state.settings
    settings.quiz_model, settings.quiz_thinking, settings.quiz_thinking_budget = 'test-quiz-model', True, 2048
    settings.review_model, settings.review_thinking, settings.review_thinking_budget = 'test-review-model', False, 512
    settings.vision_model = 'test-vision-model'
    quiz = create_custom(client, course)
    generation, review = provider.calls
    assert generation['stage'] == 'generate' and review['stage'] == 'review'
    assert generation['kwargs'] == {'max_tokens': 6248, 'json_mode': True, 'model': 'test-quiz-model',
                                     'thinking': True, 'thinking_budget': 2048}
    assert review['kwargs'] == {'max_tokens': 4000, 'json_mode': True, 'model': 'test-review-model',
                                 'thinking': False, 'thinking_budget': 512}
    public = client.get('/api/config/public').json()
    assert public['model'] == settings.model == 'test-main-model'
    assert public['quiz_model'] == 'test-quiz-model' and public['review_model'] == 'test-review-model'
    assert public['vision_model'] == 'test-vision-model'
    assert quiz['generation_model'] == 'observed-generate-1'
    assert quiz['review_model'] == 'observed-review-1'
    assert all(not {'answer', 'explanation', 'source_ids'} & question.keys() for question in quiz['questions'])
    row = app.state.db.one('SELECT * FROM quizzes WHERE id=?', (quiz['id'],))
    assert not {'generation_model', 'review_model'} & row.keys()
    assert all(not {'generation_model', 'review_model'} & question.keys() for question in loads(row['questions']))
    assert not {'generation_model', 'review_model'} & client.get(f"/api/quizzes/{quiz['id']}").json().keys()
    assert not {'generation_model', 'review_model'} & client.get(f"/api/courses/{course['id']}/practice").json()['quizzes'][0].keys()


def test_unset_quiz_model_retains_main_and_default_disabled_thinking(routed_app):
    client, app, provider, course = routed_app
    assert app.state.settings.quiz_model == ''
    quiz = create_custom(client, course)
    generation, review = provider.calls
    assert generation['kwargs']['model'] == app.state.settings.model
    assert generation['kwargs']['thinking'] is False and generation['kwargs']['thinking_budget'] == 1024
    assert review['kwargs']['model'] is None and review['kwargs']['thinking'] is False
    assert client.get('/api/config/public').json()['quiz_model'] == 'test-main-model'
    assert quiz['generation_model'] == 'observed-generate-1'


def test_quiz_and_review_thinking_settings_do_not_override_each_other(routed_app):
    client, app, provider, course = routed_app
    app.state.settings.quiz_thinking = False
    app.state.settings.quiz_thinking_budget = 128
    app.state.settings.review_thinking = True
    app.state.settings.review_thinking_budget = 16384
    create_custom(client, course)
    assert provider.calls[0]['kwargs']['thinking'] is False
    assert provider.calls[0]['kwargs']['thinking_budget'] == 128
    assert provider.calls[1]['kwargs']['thinking'] is True
    assert provider.calls[1]['kwargs']['thinking_budget'] == 16384
    assert provider.calls[1]['kwargs']['max_tokens'] == 20384


def test_regeneration_uses_same_routing_but_only_accepted_attempt_metadata(routed_app):
    client, app, provider, course = routed_app
    app.state.settings.quiz_model = 'test-quiz-model'
    provider.reject_first = True
    quiz = create_custom(client, course)
    assert [call['stage'] for call in provider.calls] == ['generate', 'review', 'generate', 'review']
    assert all(call['kwargs']['model'] == 'test-quiz-model' for call in provider.calls if call['stage'] == 'generate')
    assert quiz['generation_model'] == 'observed-generate-2' and quiz['review_model'] == 'observed-review-2'
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 1


def test_malformed_question_count_gets_one_bounded_repair_before_review(routed_app):
    client, app, provider, course = routed_app
    provider.malformed_first = True
    quiz = create_custom(client, course)
    assert [call['stage'] for call in provider.calls] == ['generate', 'generate', 'review']
    assert 'questions数组必须恰好包含3题' in provider.calls[1]['messages'][-1]['content']
    assert quiz['generation_model'] == 'observed-generate-2'
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 1


def test_missing_upstream_metadata_stays_unknown_instead_of_configured(routed_app):
    client, app, provider, course = routed_app
    app.state.settings.quiz_model, app.state.settings.review_model = 'test-quiz-model', 'test-review-model'
    provider.return_models = False
    quiz = create_custom(client, course)
    assert quiz['generation_model'] is None and quiz['review_model'] is None


@pytest.mark.parametrize('difficulty,count,minimum', [('基础', 3, 0), ('进阶', 3, 2), ('进阶', 5, 3), ('挑战', 3, 3)])
def test_actual_requested_cognitive_contract_reaches_both_quiz_stages(routed_app, difficulty, count, minimum):
    """Transport/contract proof only: fake approval does not prove difficulty."""
    client, _, provider, course = routed_app
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': count, 'difficulty': difficulty})
    assert response.status_code == 200
    contracts = [call['payload']['difficulty_contract'] for call in provider.calls]
    assert len(contracts) == 2 and contracts[0] == contracts[1]
    assert contracts[0]['provenance'] == 'server'
    assert contracts[0]['requested_difficulty'] == difficulty
    assert contracts[0]['requested_count'] == count
    assert contracts[0]['minimum_application_questions'] == minimum
    if difficulty != '基础':
        assert '不同于来源原例的一组具体输入' in contracts[0]['application_definition']
        assert '必须实际使用该新输入展示关键结论' in contracts[0]['application_definition']
    assert len(response.json()['questions']) == count


def test_explicit_unmet_cognitive_contract_rejection_never_persists_quiz(routed_app):
    """An independent explicit quality rejection still obeys atomicity and retry bound."""
    client, app, provider, course = routed_app
    original = provider.complete

    async def reject(messages, **kwargs):
        response = await original(messages, **kwargs)
        if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
            value = json.loads(response['content'])
            value.update(approved=False, issues=[{'kind': 'coverage', 'detail': '本组均为术语回忆，未满足明确的进阶应用任务合同。'}])
            response['content'] = dumps(value)
        return response

    provider.complete = reject
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 5, 'difficulty': '进阶'})
    assert response.status_code == 502
    assert '进阶应用任务合同' in response.json()['detail']
    assert [call['stage'] for call in provider.calls] == ['generate', 'review', 'generate', 'review']
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 0


def test_both_quiz_stages_receive_method_scope_and_rehash_condition_contract(routed_app):
    """Prompt delivery regression only; the fake ledger does not prove semantics."""
    client, _, provider, course = routed_app
    captured = []
    original = provider.complete

    async def capture(messages, **kwargs):
        captured.append(messages[0]['content'])
        return await original(messages, **kwargs)

    provider.complete = capture
    create_custom(client, course)
    assert len(captured) == 2 and captured[1].startswith(QUIZ_REVIEW_PREFIX)
    for prompt in captured:
        assert '教材未介绍某个方法不等于该方法错误' in prompt
        assert '明确限定' in prompt and '原文支持' in prompt
        assert '映射值域与重新散列是否配套改变' in prompt
        assert '不能仅凭槽数增加保证碰撞减少' in prompt
        assert '多步解析必须明确' in prompt or '多步数学表达必须明确' in prompt
    assert '递归代入、数值计算等多步解析必须明确写出运算符和等号' in captured[0]
    assert '不能仅以原文未提判incorrect' in captured[1]
    assert '无法排除则标uncertain并拒绝' in captured[1]


def test_builtin_bank_never_calls_either_configured_model(routed_app):
    client, app, provider, _ = routed_app
    app.state.settings.quiz_model, app.state.settings.review_model = 'test-quiz-model', 'test-review-model'
    app.state.settings.quiz_thinking = True
    app.state.db.execute('INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)',
                         ('offline-bank', None, '离线题库', '', 'BANK', '#52796f', 'layers', '助教', 1, now()))
    for index in range(3):
        question = {'id': str(index), 'stem': f'题库测试{index}',
                    'options': [{'key': key, 'text': f'选项{key}'} for key in 'ABCD'],
                    'answer': 'A', 'explanation': '仅测试题库流程。', 'concept': '数组', 'difficulty': '基础'}
        app.state.db.execute('INSERT INTO question_bank VALUES (?,?,?)', (str(index), 'offline-bank', dumps(question)))
    response = client.post('/api/courses/offline-bank/quizzes', json={'count': 3, 'difficulty': '基础'})
    assert response.status_code == 200, response.text
    quiz = response.json()
    assert provider.calls == [] and quiz['source_kind'] == 'curated_bank'
    assert quiz['generation_model'] is None and quiz['review_model'] is None


@pytest.mark.parametrize('budget', [127, 32769, 1.5, True, '1024', None])
def test_quiz_thinking_budget_rejects_invalid_settings_before_any_provider(budget):
    with pytest.raises(ValueError, match='LLM_QUIZ_THINKING_BUDGET'):
        config.Settings(quiz_thinking_budget=budget)


@pytest.mark.parametrize('budget', [128, 1024, 32768])
def test_quiz_thinking_budget_accepts_bounded_integer_settings(budget):
    assert config.Settings(quiz_thinking_budget=budget).quiz_thinking_budget == budget


@pytest.mark.parametrize('thinking,enabled', [('true', True), ('YES', True), ('1', True), ('false', False), ('0', False)])
def test_quiz_environment_parsing_is_optional_and_independent(monkeypatch, thinking, enabled):
    monkeypatch.setattr(config, 'read_dotenv', lambda path: None)
    monkeypatch.setenv('LLM_MODEL', 'env-main-model')
    monkeypatch.setenv('LLM_REVIEW_MODEL', 'env-review-model')
    monkeypatch.setenv('LLM_QUIZ_MODEL', '  env-quiz-model  ')
    monkeypatch.setenv('LLM_QUIZ_THINKING', thinking)
    monkeypatch.setenv('LLM_QUIZ_THINKING_BUDGET', '2048')
    settings = config.Settings.from_env()
    assert settings.model == 'env-main-model' and settings.review_model == 'env-review-model'
    assert settings.quiz_model == 'env-quiz-model' and settings.quiz_thinking is enabled
    assert settings.quiz_thinking_budget == 2048


def test_absent_quiz_environment_defaults_keep_compatibility(monkeypatch):
    monkeypatch.setattr(config, 'read_dotenv', lambda path: None)
    for key in ('LLM_QUIZ_MODEL', 'LLM_QUIZ_THINKING', 'LLM_QUIZ_THINKING_BUDGET'):
        monkeypatch.delenv(key, raising=False)
    settings = config.Settings.from_env()
    assert settings.quiz_model == '' and settings.quiz_thinking is False and settings.quiz_thinking_budget == 1024
    assert config.Settings(quiz_model='   ').quiz_model == ''


@pytest.mark.parametrize('budget', ['127', '32769', '1.5', 'not-an-integer', ''])
def test_quiz_environment_invalid_budget_has_named_error(monkeypatch, budget):
    monkeypatch.setattr(config, 'read_dotenv', lambda path: None)
    monkeypatch.setenv('LLM_QUIZ_THINKING_BUDGET', budget)
    with pytest.raises(ValueError, match='LLM_QUIZ_THINKING_BUDGET'):
        config.Settings.from_env()


def _add_separate_original_case(app, course):
    """Original temporary DB fixture; it carries no real difficulty proof."""
    db = app.state.db
    owner = db.one("SELECT user_id FROM documents WHERE id='route-book'")['user_id']
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count) "
               "VALUES ('route-baseline-book',?,?, '机械原例上下文','example.txt','/unused/example.txt','ready','uploaded_txt','test',?,2)",
               (course['id'], owner, now()))
    db.execute("INSERT INTO chunks VALUES ('route-baseline','route-baseline-book',?,?,2,'第2章 记录 / 2.1 原例情形',?,NULL)",
               (course['id'], owner, '原创夹具记录原例情形，仅用于来源范围和所有权边界。'))
    nodes, _ = extracted_graph(derive_textbook_tree(
        db.all('SELECT * FROM documents WHERE course_id=?', (course['id'],)),
        db.all('SELECT * FROM chunks WHERE course_id=?', (course['id'],))))
    db.execute('DELETE FROM concepts WHERE course_id=?', (course['id'],))
    for node in nodes:
        db.execute('INSERT INTO concepts VALUES (?,?,?,?,?)',
                   (node['id'], course['id'], node['label'], node['chapter'], node['source']))
    return owner


def _use_separate_original_case(provider, after_full_review=None):
    original = provider.complete

    async def complete(messages, **kwargs):
        result = await original(messages, **kwargs)
        if provider.calls[-1]['stage'] == 'generate':
            payload = provider.calls[-1]['payload']
            assert {'route-source', 'route-baseline'} <= {item['id'] for item in payload['evidence']}
            value = json.loads(result['content'])
            for index, question in enumerate(value['questions']):
                question.update(source_ids=['route-source'],
                    concept=payload['source_allowed_concepts']['route-source'][0],
                    application_basis=fake_application_basis(['route-baseline'], index))
            result['content'] = dumps(value)
        elif after_full_review is not None:
            after_full_review()
        return result

    provider.complete = complete


def test_uncited_original_case_reaches_full_scope_and_private_basis_never_leaks(routed_app):
    """Scope/whitelist proof only: mechanical fixtures do not certify novelty."""
    client, app, provider, course = routed_app
    _add_separate_original_case(app, course)
    _use_separate_original_case(provider)
    response = client.post(f"/api/courses/{course['id']}/quizzes",
        json={'count': 3, 'difficulty': '进阶'})
    assert response.status_code == 200, response.text
    generation, full_review = provider.calls
    generation_ids = {item['id'] for item in generation['payload']['evidence']}
    assert {item['id'] for item in full_review['payload']['evidence']} == generation_ids
    assert len(full_review['payload']['evidence']) == len(generation['payload']['evidence'])
    assert all(q['source_ids'] == ['route-source'] for q in full_review['payload']['questions'])
    assert all(q['application_basis']['original_source_ids'] == ['route-baseline']
               for q in full_review['payload']['questions'])
    assert 'route-baseline' not in {source for q in full_review['payload']['questions'] for source in q['source_ids']}
    quiz = response.json()
    assert all('application_basis' not in q for q in quiz['questions'])
    restored = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert all('application_basis' not in q for q in restored['questions'])
    stored = loads(app.state.db.one('SELECT questions FROM quizzes WHERE id=?', (quiz['id'],))['questions'])
    assert all(q['application_basis']['original_source_ids'] == ['route-baseline'] for q in stored)


@pytest.mark.parametrize('basis_mode', ['missing', 'null'])
def test_missing_application_basis_cannot_persist_advanced_quiz_or_get_third_generation(routed_app, basis_mode):
    """A fake approval cannot turn absent metadata into a qualifying application."""
    client, app, provider, course = routed_app
    original = provider.complete

    async def complete(messages, **kwargs):
        result = await original(messages, **kwargs)
        value = json.loads(result['content'])
        if provider.calls[-1]['stage'] == 'generate':
            for question in value['questions']:
                if basis_mode == 'missing':
                    question.pop('application_basis')
                else:
                    question['application_basis'] = None
        else:
            # Explicitly simulate the more dangerous false positive: a reviewer
            # claims all flags true, while server-held metadata is absent/null.
            for item in value['application_checks']:
                for flag in ('has_new_condition', 'requires_inference', 'explanation_uses_new_condition', 'requires_multiple_steps'):
                    item[flag] = True
        result['content'] = dumps(value)
        return result

    provider.complete = complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '进阶'})
    assert response.status_code == 502, response.text
    assert '未计入' in response.json()['detail']
    assert [call['stage'] for call in provider.calls] == ['generate', 'review', 'generate', 'review']
    assert '逐题应用检查' in provider.calls[2]['messages'][-1]['content']
    for table in ('quizzes', 'mastery', 'mistakes'):
        assert app.state.db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE course_id=?', (course['id'],))['n'] == 0


@pytest.mark.parametrize('flag,difficulty', [('has_new_condition', '进阶'), ('requires_inference', '进阶'),
    ('explanation_uses_new_condition', '进阶'), ('requires_multiple_steps', '挑战')])
def test_independent_application_failure_obeys_semantic_retry_and_persistence_bound(routed_app, flag, difficulty):
    """Scripted negative application flags exercise the gate, not content judging."""
    client, app, provider, course = routed_app
    original = provider.complete

    async def complete(messages, **kwargs):
        result = await original(messages, **kwargs)
        if provider.calls[-1]['stage'] == 'review':
            value = json.loads(result['content'])
            for item in value['application_checks']:
                item[flag] = False
                item['reason'] = '机械夹具故意报告该认知任务未达标。'
            result['content'] = dumps(value)
        return result

    provider.complete = complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': difficulty})
    assert response.status_code == 502, response.text
    assert '复核未通过' in response.json()['detail']
    assert [call['stage'] for call in provider.calls] == ['generate', 'review', 'generate', 'review']
    assert flag in provider.calls[2]['messages'][-1]['content']
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 0


@pytest.mark.parametrize('malformation', ['unknown_source', 'missing_field'])
def test_malformed_application_basis_stops_before_any_review_or_regeneration(routed_app, malformation):
    client, app, provider, course = routed_app
    original = provider.complete

    async def complete(messages, **kwargs):
        result = await original(messages, **kwargs)
        if provider.calls[-1]['stage'] == 'generate':
            value = json.loads(result['content'])
            basis = value['questions'][0]['application_basis']
            if malformation == 'unknown_source':
                basis['original_source_ids'] = ['outside-generation-context']
            else:
                basis.pop('new_condition')
            result['content'] = dumps(value)
        return result

    provider.complete = complete
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '进阶'})
    assert response.status_code == 502, response.text
    assert '应用协议无效' in response.json()['detail']
    assert [call['stage'] for call in provider.calls] == ['generate']
    assert app.state.db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 0


@pytest.mark.parametrize('change', ['delete_original_source', 'document_owner_changes', 'document_not_ready'])
def test_original_case_source_rechecked_after_full_review_await(routed_app, change):
    """Only the uncited baseline changes; valid question sources cannot hide it."""
    client, app, provider, course = routed_app
    owner = _add_separate_original_case(app, course)
    db = app.state.db
    db.execute("INSERT INTO users VALUES ('another-fixture-owner','another-fixture-owner','Other','unused',?)", (now(),))

    def after_full_review():
        if change == 'delete_original_source':
            db.execute("DELETE FROM chunks WHERE id='route-baseline'")
        elif change == 'document_owner_changes':
            # Change only the authoritative document owner. A stale chunk owner
            # must not authorize continued use of this original-case source.
            db.execute("UPDATE documents SET user_id='another-fixture-owner' WHERE id='route-baseline-book'")
            assert db.one("SELECT user_id FROM chunks WHERE id='route-baseline'")['user_id'] == owner
        else:
            db.execute("UPDATE documents SET status='failed' WHERE id='route-baseline-book'")

    _use_separate_original_case(provider, after_full_review)
    response = client.post(f"/api/courses/{course['id']}/quizzes", json={'count': 3, 'difficulty': '进阶'})
    assert response.status_code == 502, response.text
    assert '教材来源已变化' in response.json()['detail']
    assert [call['stage'] for call in provider.calls] == ['generate', 'review']
    assert db.one("SELECT COUNT(*) AS n FROM chunks WHERE id='route-source'")['n'] == 1
    assert db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 0
