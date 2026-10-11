import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.app.db import dumps, now
from backend.app.practice import create_quiz
from backend.app.quiz_review import QUIZ_REVIEW_PREFIX
from backend.tests.quiz_review_helpers import fake_quiz_application_review, BlindReviewTestMixin
from backend.tests.test_api import setup, register


class RecordingQuizProvider(BlindReviewTestMixin):
    def __init__(self):
        self.evidence = None
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
            return {'content': dumps(fake_quiz_application_review(json.loads(messages[1]['content'])['questions']))}
        prompt = json.loads(messages[1]['content'])
        self.evidence = prompt['evidence']
        return {'content': dumps({'questions': [
            {'stem': f'数组读取的课堂测试题 {i}', 'options': [{'key': key, 'text': f'选项 {key}'} for key in 'ABCD'],
             'answer': 'A', 'explanation': '数组可以通过索引读取元素。', 'concept': '数组', 'difficulty': '基础',
             'source_ids': [self.evidence[0]['id']], 'application_basis': None}
            for i in range(prompt['count'])]})}


@pytest.mark.parametrize('has_body', [True, False])
def test_default_quiz_skips_outline_front_and_back_matter_before_paid_generation(setup, has_body):
    client, app, _ = setup
    user = register(client)
    course = client.post('/api/courses', json={'title': '真实目录采样边界', 'description': ''}).json()
    db = app.state.db
    quality = {'outline_entries': 3, 'chapter_source': 'PDF内置目录', 'pdf_outline': [
        {'page': 1, 'path': '版权声明'}, {'page': 2, 'path': '第1章 数据结构'}, {'page': 4, 'path': '看完了'}]}
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count,quality) VALUES (?,?,?,?,?,?,'ready','uploaded_pdf','test',?,4,?)",
               ('sampling-book', course['id'], user['id'], '目录采样', 'book.pdf', '/unused/book.pdf', now(), dumps(quality)))
    rows = [(1, '版权声明', '本教材的数字版权声明和授权条件。'), (4, '看完了', '恭喜读完，推荐其他图书。')]
    if has_body:
        rows.append((2, '第1章 数据结构', '数组是一种基础数据结构，可以通过索引读取元素。'))
    for page, section, text in rows:
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)',
                   (f'sampling-{page}', 'sampling-book', course['id'], user['id'], page, section, text))
    provider = RecordingQuizProvider()
    if has_body:
        result = asyncio.run(create_quiz(db, provider, app.state.retriever, course, user['id'], '', '基础', 3))
        assert result['source_kind'] == 'textbook_generated'
        assert len(result['questions']) == 3
        assert {chunk['page'] for chunk in provider.evidence} == {2}
        assert all('版权' not in chunk['section'] and '看完了' not in chunk['section'] for chunk in provider.evidence)
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(create_quiz(db, provider, app.state.retriever, course, user['id'], '', '基础', 3))
        assert error.value.status_code == 409
        assert provider.calls == 0
        assert db.one('SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?', (course['id'],))['n'] == 0
