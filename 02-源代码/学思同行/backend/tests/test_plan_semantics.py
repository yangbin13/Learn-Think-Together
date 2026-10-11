import re

import pytest

from backend.app.db import dumps, now
from backend.app.practice import make_plan
from backend.app.provider import ProviderError
from backend.app.plan_goals import goal_queries
from backend.tests.test_api import setup, register


def test_unpracticed_custom_plan_uses_source_order_not_opaque_concept_ids(setup):
    client, app, provider = setup
    user = register(client)
    course = client.post('/api/courses', json={'title': '结构顺序测试', 'description': ''}).json()
    db = app.state.db
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at) VALUES (?,?,?,?,?,?,'ready','uploaded_txt','test',?)",
               ('ordered-book', course['id'], user['id'], '顺序教材', 'book.txt', '/unused/book.txt', now()))
    for index, (section, text) in enumerate([
        ('第1章 数据结构', '本章先介绍数据结构的基本思想。'),
        ('第1章 数据结构 / 1.1 数组', '数组的读取、插入、删除与搜索。'),
        ('第2章 大O记法', '在学习基本操作之后，描述操作次数增长趋势。'),
    ], 1):
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)',
                   (f'ordered-{index}', 'ordered-book', course['id'], user['id'], index, section, text))
    # Opaque cache IDs deliberately put the last chapter first in lexical order.
    for id_, label, chapter in [('900', '数据结构', '第1章 数据结构'), ('800', '数组', '第1章 数据结构'), ('100', '大O记法', '第2章 大O记法'), ('000', '版权声明', '版权声明')]:
        db.execute('INSERT INTO concepts VALUES (?,?,?,?,?)', (id_, course['id'], label, chapter, 'test'))
    response = client.post(f"/api/courses/{course['id']}/plans", json={'days': 7, 'daily_minutes': 15})
    assert response.status_code == 200
    items = response.json()['items']
    assert [item['concepts'] for item in items[:3]] == [['数据结构'], ['数组'], ['大O记法']]
    assert all('版权声明' not in item['concepts'] for item in items)
    assert all(item['minutes'] == 15 for item in items)
    assert provider.histories == []
    # Actual weak points and explicit existing targets still take priority.
    db.execute('INSERT INTO mastery VALUES (?,?,?,?,?)', (user['id'], course['id'], '大O记法', 4, 1))
    plan = make_plan(db, course, user['id'], 7, 30, '巩固数组', goal_concepts=['数组'])
    assert [item['concepts'] for item in plan['items'][:2]] == [['大O记法'], ['数组']]
    assert '大O记法' in plan['items'][5]['concepts']


def test_weekly_plan_revisits_measured_weakness_and_covers_existing_goal(setup):
    client, app, provider = setup
    user = register(client)
    db = app.state.db
    db.execute("INSERT INTO mastery VALUES (?, 'data-structures', '栈', 4, 1)", (user["id"],))
    before = db.all("SELECT * FROM mastery WHERE user_id=?", (user["id"],))
    response = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 45, "goal": "理解队列的入队和出队"})
    assert response.status_code == 200
    plan = response.json()
    weak_days = [item["day"] for item in plan["items"] if "栈" in item["concepts"]]
    assert weak_days[0] == 1 and 6 in weak_days and len(set(weak_days)) >= 2
    review = next(item for item in plan["items"] if item["day"] == 6)
    assert review["title"] == "间隔复习 · 栈" and "队列" in review["concepts"]
    assert all(item["minutes"] == 45 for item in plan["items"])
    assert "1 个真实薄弱概念" in plan["basis"] and "目标" in plan["basis"]
    assert db.all("SELECT * FROM mastery WHERE user_id=?", (user["id"],)) == before
    assert provider.histories == []  # Deterministic plan never asks an LLM to invent mastery.


def test_goal_retrieval_maps_existing_section_instead_of_inventing_concept(setup):
    client, app, provider = setup
    user = register(client)
    calls = []

    async def multilingual_matches(course_id, user_id, query, limit):
        calls.append((course_id, user_id, query, limit))
        return {"items": [{"section": "课程 / 2.3 队列"}, {"section": "不存在的知识点"}], "method": "hybrid", "notice": "真实章节匹配测试协议"}

    app.state.retriever.search = multilingual_matches
    result = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 30, "goal": "Learn queue operations"})
    assert result.status_code == 200
    plan = result.json()
    assert calls == [("data-structures", user["id"], "Learn queue operations", 6)]
    assert plan["items"][0]["concepts"] == ["队列"]
    assert all(set(item["concepts"]) <= {"栈", "队列"} for item in plan["items"])
    assert "hybrid" in plan["basis"] and "1 个已存在的目标相关概念" in plan["basis"]
    assert client.get("/api/courses/data-structures/overview").json()["mastery"] is None
    assert provider.histories == []


def test_goal_retrieval_failure_keeps_direct_match_and_offline_plan(setup):
    client, app, _ = setup
    register(client)

    async def unavailable(*args, **kwargs):
        raise ProviderError("embedding query unavailable")

    app.state.retriever.search = unavailable
    response = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 15, "goal": "复习队列"})
    assert response.status_code == 200
    plan = response.json()
    assert plan["items"][0]["concepts"] == ["队列"]
    assert "目标检索暂不可用" in plan["basis"] and "离线规则计划" in plan["basis"]
    assert len(plan["items"]) == 7 and all(item["minutes"] == 15 for item in plan["items"])


def test_unmatched_goal_is_disclosed_and_does_not_create_mastery_or_concepts(setup):
    client, app, _ = setup
    user = register(client)

    async def no_match(*args, **kwargs):
        return {"items": [], "method": "BM25", "notice": "无命中"}

    app.state.retriever.search = no_match
    before = dumps(app.state.db.all("SELECT * FROM concepts WHERE course_id='data-structures'"))
    plan = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 30, "goal": "学习尚未上传的无关课程"}).json()
    assert "目标未匹配" in plan["basis"] and "0 个已存在的目标相关概念" in plan["basis"]
    assert all(set(item["concepts"]) <= {"栈", "队列"} for item in plan["items"])
    assert not app.state.db.all("SELECT * FROM mastery WHERE user_id=?", (user["id"],))
    assert dumps(app.state.db.all("SELECT * FROM concepts WHERE course_id='data-structures'")) == before


def test_multiple_intents_each_keep_their_primary_existing_concept_without_ancestor_flood(setup):
    client, app, _ = setup
    user = register(client)
    app.state.db.execute("INSERT INTO mastery VALUES (?, 'data-structures', '栈', 4, 1)", (user["id"],))
    calls = []

    async def matches(course_id, user_id, query, limit):
        calls.append(query)
        return {"items": [{"section": "队列 / 栈"}] * 6 if query == "纠正出栈错误" else [{"section": "栈 / 队列"}], "method": "hybrid", "notice": "测试协议"}

    app.state.retriever.search = matches
    goal = "纠正出栈错误，复习排队结构与本次练习中的薄弱点"
    plan = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 30, "goal": goal}).json()
    assert calls == ["纠正出栈错误", "复习排队结构"]
    assert "队列" in set(concept for item in plan["items"] for concept in item["concepts"])
    assert "2 个独立学习意图" in plan["basis"]
    assert goal_queries("学习主题一、主题二") == ["学习主题一、主题二"]
    assert len(goal_queries("理解甲，理解乙，理解丙，理解丁，理解戊")) == 4


@pytest.mark.parametrize("minutes", [15, 30, 180])
def test_weekly_plan_reviews_both_real_weaknesses_with_one_shared_daily_budget(setup, minutes):
    client, app, _ = setup
    user = register(client)
    db = app.state.db
    db.execute("INSERT INTO mastery VALUES (?, 'data-structures', '栈', 4, 1)", (user["id"],))
    db.execute("INSERT INTO mastery VALUES (?, 'data-structures', '队列', 3, 1)", (user["id"],))
    before = db.all("SELECT * FROM mastery WHERE user_id=? ORDER BY concept", (user["id"],))
    plan = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": minutes, "goal": "理解栈与队列"}).json()
    for concept in ("栈", "队列"):
        days = {item["day"] for item in plan["items"] if concept in item["concepts"]}
        assert 6 in days and len(days) >= 2
    assert len(plan["items"]) == 7 and all(item["minutes"] == minutes for item in plan["items"])
    review = next(item for item in plan["items"] if item["day"] == 6)
    assert set(review["concepts"]) == {"栈", "队列"}
    assert sum(int(value) for value in re.findall(r"」(\d+)分钟", review["description"])) == minutes
    assert "不另增加课时" in review["description"]
    assert db.all("SELECT * FROM mastery WHERE user_id=? ORDER BY concept", (user["id"],)) == before


@pytest.mark.parametrize("days", [7, 14, 30])
def test_short_plan_does_not_introduce_more_weaknesses_than_it_can_revisit(setup, days):
    client, app, _ = setup
    user = register(client)
    db = app.state.db
    for index in range(35):
        db.execute("INSERT INTO mastery VALUES (?, 'data-structures', ?, 2, 0)", (user["id"], f"真实已测概念{index}"))
    course = db.one("SELECT * FROM courses WHERE id='data-structures'")
    plan = make_plan(db, course, user["id"], days, 15, "复习当前薄弱点")
    scheduled = {concept for item in plan["items"] for concept in item["concepts"] if concept.startswith("真实已测概念")}
    assert scheduled and len(scheduled) < 35
    for concept in scheduled:
        assert len({item["day"] for item in plan["items"] if concept in item["concepts"]}) >= 2
    for item in plan["items"]:
        assert item["minutes"] == 15
        shares = [int(value) for value in re.findall(r"」(\d+)分钟", item["description"])]
        if shares:
            assert min(shares) > 0 and sum(shares) == 15
    assert "有限时间内暂未引入" in plan["basis"]
