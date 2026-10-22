"""Offline regression for source-bound labels and label-keyed mastery safety."""
import asyncio
import json

import pytest

from backend.app.db import Database, dumps, now
from backend.app.knowledge_map import derive_textbook_tree, extracted_graph
from backend.app.practice import (ConceptScopeError, bind_question_concepts, create_quiz,
                                  default_learning_chunks, load_quiz, make_plan,
                                  source_concept_scopes, submit_quiz)
from backend.app.provider import ProviderError
from backend.app.quiz_review import QUIZ_REVIEW_PREFIX
from backend.tests.quiz_review_helpers import (complete_legacy_quiz_application_decision,
    fake_application_basis, BlindReviewTestMixin)


@pytest.fixture
def textbook(tmp_path):
    # No main import, dotenv, real database, provider, browser or server lifecycle.
    db = Database(tmp_path / 'scope.sqlite3')
    db.execute("INSERT INTO users VALUES ('student','student','Student','unused',?)", (now(),))
    db.execute("INSERT INTO courses VALUES ('scope-course','student','课程','','S','#557766','layers','助教',0,?)", (now(),))
    return db, db.one("SELECT * FROM courses WHERE id='scope-course'")


def add_document(db, document, paths, rows, source_type='uploaded_pdf'):
    quality = {'outline_entries':len(paths),'chapter_source':'PDF内置目录',
               'pdf_outline':[{'page':page,'path':path} for page,path in paths]} if paths else {}
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count,quality) "
               "VALUES (?,'scope-course','student',?,'unused.pdf','/unused','ready',?,'test',?,30,?)",
               (document,document,source_type,now(),dumps(quality)))
    for chunk_id,page,section,text in rows:
        db.execute("INSERT INTO chunks VALUES (?,?,'scope-course','student',?,?,?,NULL)",
                   (chunk_id,document,page,section,text))
    refresh_catalog(db)


def refresh_catalog(db):
    docs = db.all("SELECT * FROM documents WHERE course_id='scope-course'")
    rows = db.all("SELECT * FROM chunks WHERE course_id='scope-course'")
    nodes,_ = extracted_graph(derive_textbook_tree(docs,rows))
    db.execute("DELETE FROM concepts WHERE course_id='scope-course'")
    for node in nodes:
        db.execute("INSERT INTO concepts VALUES (?,'scope-course',?,?,?)",
                   (node['id'],node['label'],node['chapter'],node['source']))


def add_hash_and_sort(db):
    add_document(db,'book',[
        (1,'第1章 散列基础'),(2,'第1章 散列基础 / 1.1 处理冲突'),
        (4,'第1章 散列基础 / 1.2 负载因子'),(10,'第2章 排序基础'),
        (11,'第2章 排序基础 / 2.1 最坏情况')],[
        ('hash',3,'第1章 散列基础 / 1.1 处理冲突','本教材采用桶内无序数组。全部键冲突，顺序扫描不存在的键最坏需访问N个元素。'),
        ('boundary',4,'第1章 散列基础 / 1.2 负载因子','上一节的桶内顺序查找在最坏情景下为线性访问；下节讨论负载因子。'),
        ('alias',5,'第1章 散列基础 / 1.2 负载因子 4','负载因子表示键数和格子数的比值。'),
        ('sort',12,'第2章 排序基础 / 2.1 最坏情况','端点枢轴在有序输入上产生最不平衡的快速排序分区。')])


def evidence(db, *ids):
    return [db.one("SELECT c.*,d.title,d.source_type FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chunk_id,)) for chunk_id in ids]


def scopes(db, rows):
    return source_concept_scopes(db,'scope-course','student',db.all("SELECT * FROM chunks"),rows)


def minimal_question(concept, ids):
    return {'concept':concept,'source_ids':ids}


def test_unrelated_same_course_label_cannot_bind_hash_sources_to_sort_mastery(textbook):
    db,_ = textbook
    add_hash_and_sort(db)
    allowed,_ = scopes(db,evidence(db,'hash','sort'))
    assert '最坏情况' in allowed['sort'] and '最坏情况' not in allowed['hash']
    assert {'处理冲突','散列基础'} <= set(allowed['hash'])
    with pytest.raises(ConceptScopeError,match='第1题'):
        bind_question_concepts([minimal_question('最坏情况',['hash'])],allowed)
    for label in ['处理冲突','散列基础']:
        q = minimal_question(label,['hash'])
        bind_question_concepts([q],allowed)
        assert q['concept_measured'] is True
    with pytest.raises(ConceptScopeError,match='不能共同归属'):
        bind_question_concepts([minimal_question('散列基础',['hash','sort'])],allowed)


def test_physical_page_boundary_uses_real_parent_and_running_header_alias_is_resolved(textbook):
    db,_ = textbook
    add_hash_and_sort(db)
    allowed,notes = scopes(db,evidence(db,'boundary','alias'))
    assert allowed['boundary'] == ['散列基础']
    assert '跨相邻小节' in notes['boundary']
    assert '负载因子' in allowed['alias'] and '负载因子 4' not in allowed['alias']
    with pytest.raises(ConceptScopeError):
        bind_question_concepts([minimal_question('负载因子',['boundary'])],allowed)


def test_repeated_titles_across_chapters_and_documents_are_not_shared_measurements(textbook):
    db,_ = textbook
    add_hash_and_sort(db)
    add_document(db,'second',[(1,'第3章 搜索基础'),(2,'第3章 搜索基础 / 3.1 最坏情况')],
                 [('search',3,'第3章 搜索基础 / 3.1 最坏情况','搜索失败时需要访问全部元素。')])
    allowed,_ = scopes(db,evidence(db,'sort','search'))
    assert '最坏情况' not in allowed['sort'] and '最坏情况' not in allowed['search']
    assert '排序基础' in allowed['sort'] and '搜索基础' in allowed['search']
    with pytest.raises(ConceptScopeError):
        bind_question_concepts([minimal_question('最坏情况',['sort'])],allowed)


def test_repeated_parent_title_with_no_unique_ancestor_is_explicitly_unmeasured(textbook):
    db,_ = textbook
    for document in ['first','second']:
        add_document(db,document,[(1,'第1章 基础'),(2,'第1章 基础 / 1.1 定义')],
                     [(document+'-text',3,'第1章 基础 / 1.1 定义','这一段提供教材的定义和例子。')])
    allowed,_ = scopes(db,evidence(db,'first-text'))
    assert allowed['first-text'] == []
    q = minimal_question('定义',['first-text'])
    bind_question_concepts([q],allowed)
    assert q['concept_measured'] is False and '不计入' in q['concept_notice']


class Retriever:
    def __init__(self, ids):
        self.ids = ids

    async def search(self, *args):
        return {'items':[{'chunk_id':source} for source in self.ids]}


class QuizProvider(BlindReviewTestMixin):
    def __init__(self, labels, sources, decisions=None):
        self.labels,self.sources = labels,sources
        self.decisions = decisions or [{'approved':True,'issues':[]}]
        self.generations,self.reviews = [],[]

    async def complete(self,messages,**kwargs):
        if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
            self.reviews.append((messages[0]['content'],json.loads(messages[1]['content'])))
            decision = self.decisions[min(len(self.reviews)-1,len(self.decisions)-1)]
            return {'content':dumps(complete_legacy_quiz_application_decision(decision, self.reviews[-1][1]['questions']))}
        self.generations.append(messages.copy())
        prompt = json.loads(messages[1]['content'])
        selected = self.labels[min(len(self.generations)-1,len(self.labels)-1)]
        selected = [selected]*prompt['count'] if isinstance(selected,str) else selected
        return {'content':dumps({'questions':[
            {'stem':f'本教材的桶内数组有N个键，顺序查找不存在的键时，最坏访问次数的最紧渐近上界是什么？题{i}',
             'options':[{'key':key,'text':text} for key,text in zip('ABCD',['O(N)','O(1)','O(log N)','O(N²)'])],
             'answer':'A','explanation':'不存在的键必须扫描全部N个元素。','concept':label,
              'source_ids':[self.sources[i % len(self.sources)]],
              'application_basis':fake_application_basis([self.sources[i % len(self.sources)]],i)}
            for i,label in enumerate(selected)]})}


@pytest.mark.parametrize('labels,success', [(['最坏情况','散列基础'],True),(['最坏情况'],False)])
def test_deterministic_scope_rejection_is_bounded_and_repair_still_requires_independent_review(textbook,labels,success):
    db,course = textbook
    add_hash_and_sort(db)
    provider = QuizProvider(labels,['hash'])
    if success:
        result = asyncio.run(create_quiz(db,provider,Retriever(['hash']),course,'student','冲突','进阶',3))
        assert len(provider.generations) == 2 and len(provider.reviews) == 1
        assert result['source_kind'] == 'textbook_generated'
        assert all(q['concept'] == '散列基础' and q['concept_measured'] for q in result['questions'])
        assert all(not {'answer','explanation','source_ids'} & q.keys() for q in result['questions'])
        assert '重生成仍须独立逐题复核' in provider.generations[1][-1]['content']
        payload = json.loads(provider.generations[0][1]['content'])
        assert '最坏情况' not in payload['allowed_concepts']
        assert payload['source_allowed_concepts']['hash'] == ['处理冲突','散列基础']
        assert provider.reviews[0][1]['source_allowed_concepts'] == payload['source_allowed_concepts']
    else:
        with pytest.raises(ProviderError,match='已阻止入库'):
            asyncio.run(create_quiz(db,provider,Retriever(['hash']),course,'student','冲突','进阶',3))
        assert len(provider.generations) == 2 and not provider.reviews
        assert db.one("SELECT COUNT(*) AS n FROM quizzes")['n'] == 0


def test_complexity_and_specific_implementation_conditions_are_reviewed_on_every_regeneration(textbook):
    db,course = textbook
    add_hash_and_sort(db)
    provider = QuizProvider(['处理冲突'],['hash'],[
        {'approved':False,'issues':[{'kind':'condition','detail':'第1题必须明确最坏情景；第3题不能把本书槽内数组当一般分离链接唯一实现'}]},
        {'approved':True,'issues':[]}])
    asyncio.run(create_quiz(db,provider,Retriever(['hash']),course,'student','冲突','进阶',3))
    assert len(provider.generations) == len(provider.reviews) == 2
    assert '最坏/平均/摊还' in provider.generations[0][0]['content']
    assert '本教材采用的实现' in provider.generations[0][0]['content']
    assert all('查找命中位置' in prompt and '分离链接必须用数组' in prompt for prompt,_ in provider.reviews)
    assert '第3题' in provider.generations[1][-1]['content']


def test_unstructured_compatibility_has_visible_notice_and_does_not_pollute_mastery_or_plan(textbook):
    db,course = textbook
    add_document(db,'plain',[],[('plain-text',1,'正文','顺序查找不存在的键需要访问全部N个元素。')],source_type='uploaded_txt')
    rows = db.all("SELECT * FROM chunks")
    assert default_learning_chunks(db,course['id'],'student',rows) == rows
    provider = QuizProvider(['顺序查找'],['plain-text'])
    quiz = asyncio.run(create_quiz(db,provider,Retriever(['plain-text']),course,'student','查找','基础',3))
    assert all(q['concept_measured'] is False and '不计入' in q['concept_notice'] for q in quiz['questions'])
    assert '不计概念掌握度' in quiz['source']
    assert all(not {'answer','explanation','source_ids'} & q.keys() for q in load_quiz(db,quiz['id'],'student')['questions'])
    graded = submit_quiz(db,quiz['id'],'student',{})
    assert graded['unmapped_question_count'] == 3 and '不计入' in graded['measurement_notice']
    assert graded['weak_concepts'] == [] and db.one("SELECT COUNT(*) AS n FROM mastery")['n'] == 0
    assert db.one("SELECT COUNT(*) AS n FROM mistakes")['n'] == 3
    assert '未归属' in graded['next_action']
    plan = make_plan(db,course,'student',7,30,'复习')
    assert '依据 0 个已有练习概念' in plan['basis']
    assert all('顺序查找' not in item['concepts'] for item in plan['items'])


def test_mixed_quiz_accumulates_only_the_genuinely_source_bound_questions(textbook):
    db,course = textbook
    add_hash_and_sort(db)
    add_document(db,'plain',[],[('plain-text',1,'正文','顺序查找不存在的键需要访问全部N个元素。')],source_type='uploaded_txt')
    provider = QuizProvider([['处理冲突','处理冲突','顺序查找']],['hash','hash','plain-text'])
    quiz = asyncio.run(create_quiz(db,provider,Retriever(['hash','plain-text']),course,'student','查找','基础',3))
    assert [q['concept_measured'] for q in quiz['questions']] == [True,True,False]
    graded = submit_quiz(db,quiz['id'],'student',{q['id']:'A' for q in quiz['questions']})
    assert graded['unmapped_question_count'] == 1
    assert db.all("SELECT concept,attempts,correct FROM mastery") == [{'concept':'处理冲突','attempts':2,'correct':2}]


@pytest.mark.parametrize('change',['delete_source','duplicate_label'])
def test_final_persistence_gate_rechecks_sources_and_label_uniqueness_after_model_review(textbook,change):
    db,course = textbook
    add_hash_and_sort(db)

    class ChangedSourceProvider(QuizProvider):
        async def complete(self,messages,**kwargs):
            result = await super().complete(messages,**kwargs)
            if messages[0]['content'].startswith(QUIZ_REVIEW_PREFIX):
                if change == 'delete_source':
                    db.execute("DELETE FROM documents WHERE id='book'")
                else:
                    add_document(db,'duplicate',[(1,'第3章 地址分配'),(2,'第3章 地址分配 / 3.1 处理冲突')],
                                 [('duplicate-text',3,'第3章 地址分配 / 3.1 处理冲突','地址分配冲突的处理原则。')])
            return result

    provider = ChangedSourceProvider(['处理冲突'],['hash'])
    with pytest.raises(ProviderError):
        asyncio.run(create_quiz(db,provider,Retriever(['hash']),course,'student','冲突','基础',3))
    assert len(provider.generations) == len(provider.reviews) == 1
    assert db.one("SELECT COUNT(*) AS n FROM quizzes")['n'] == 0
