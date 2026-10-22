from types import SimpleNamespace
import json

from backend.tests.test_api import setup, register, events, wait_document


def test_independent_review_model_does_not_replace_actual_teacher_model(setup):
    client, app, provider = setup
    register(client)
    app.state.settings.review_model = "test-independent-review"
    app.state.settings.review_thinking = True
    requests = []
    complete = provider.complete

    async def capture(messages, **kwargs):
        requests.append((messages[0]["content"], kwargs))
        return await complete(messages, **kwargs)

    provider.complete = capture
    response = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "为什么栈后进先出？", "mode": "explain"}))
    assert response[-1][0] == "done"
    reviews = [options for prompt, options in requests if prompt.startswith("你是独立事实审查器")]
    assert reviews and all(options["model"] == "test-independent-review" and options["json_mode"] and options["thinking"] for options in reviews)
    assert response[-1][1]["model"] == "fake-test-model"
    assert client.get("/api/config/public").json()["review_model"] == "test-independent-review"


def test_independent_quiz_review_routes_only_the_review_call(setup):
    client, app, provider = setup
    register(client)
    provider.settings = SimpleNamespace(review_model="test-quiz-review", review_thinking=True)
    requests = []
    complete = provider.complete

    async def capture(messages, **kwargs):
        requests.append((messages[0]["content"], kwargs))
        if messages[0]["content"].startswith("根据提供的真实教材出单选题"):
            context = json.loads(messages[1]["content"])
            source_id = context["evidence"][0]["id"]
            return {"content": json.dumps({"questions": [{"stem": f"按教材，栈的访问规则是什么？测试{i}",
                "options": [{"key": key, "text": text} for key, text in zip("ABCD", ["后进先出", "先进先出", "随机访问", "无序访问"])],
                "answer": "A", "explanation": "栈后进先出。", "concept": context["source_allowed_concepts"][source_id][0],
                "source_ids": [source_id]} for i in range(3)]})}
        return await complete(messages, **kwargs)

    provider.complete = capture
    course = client.post("/api/courses", json={"title": "结构测试", "description": "教材"}).json()
    response = client.post(f"/api/courses/{course['id']}/documents", files={"file": ("book.txt", "第1章 栈\n栈遵守后进先出规则。".encode(), "text/plain")}, data={"rights_confirmed": "true"})
    assert response.status_code == 202
    assert wait_document(client, course["id"], response.json()["id"])["status"] == "ready"
    generated = client.post(f"/api/courses/{course['id']}/quizzes", json={"topic": "栈", "difficulty": "基础", "count": 3})
    assert generated.status_code == 200
    reviews = [options for prompt, options in requests if prompt.startswith("你是独立教材练习审查器")]
    assert reviews and all(options["model"] == "test-quiz-review" and options["json_mode"] and options["thinking"] for options in reviews)
    assert all(options.get("model") != "test-quiz-review" for prompt, options in requests if not prompt.startswith("你是独立教材练习审查器"))
