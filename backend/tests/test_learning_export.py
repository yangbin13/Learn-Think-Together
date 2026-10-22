"""Submitted exercise exports are useful for recall without leaking drafts."""
from backend.tests.test_api import register, setup


def make_quiz(client, count=1):
    response = client.post("/api/courses/data-structures/quizzes", json={"count": count, "difficulty": "基础"})
    assert response.status_code == 200
    return response.json()


def test_submitted_export_contains_the_question_choices_and_feedback(setup):
    client, _, _ = setup
    register(client)
    quiz = make_quiz(client)
    question = quiz["questions"][0]
    grade = client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {question["id"]: "B"}}).json()
    response = client.get("/api/export", params={"course_id": "data-structures"})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "题目：" + question["stem"] in response.text
    for option in question["options"]:
        assert f"{option['key']}. {option['text']}" in response.text
    feedback = grade["results"][0]
    assert "作答 " + feedback["user_answer"] in response.text
    assert "正确 " + feedback["correct_answer"] in response.text
    assert feedback["explanation"] in response.text


def test_unsubmitted_export_hides_question_choices_keys_and_saved_drafts(setup):
    client, _, _ = setup
    register(client)
    quiz = make_quiz(client, count=3)
    first = quiz["questions"][0]
    saved = client.put(f"/api/quizzes/{quiz['id']}/draft", json={"answers": {first["id"]: "B"}, "expected_version": 0})
    assert saved.status_code == 200
    for params in ({"course_id": "data-structures"}, {}):
        response = client.get("/api/export", params=params)
        assert response.status_code == 200
        assert "尚未提交" in response.text
        for question in quiz["questions"]:
            assert question["stem"] not in response.text
            for option in question["options"]:
                assert f"{option['key']}. {option['text']}" not in response.text
        assert "作答 B" not in response.text
        assert "栈后进先出。" not in response.text


def test_submitted_question_context_is_not_exported_to_a_different_student(setup):
    client, _, _ = setup
    register(client, "export_alice")
    quiz = make_quiz(client)
    question = quiz["questions"][0]
    assert client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {question["id"]: "B"}}).status_code == 200
    register(client, "export_bob")
    response = client.get("/api/export", params={"course_id": "data-structures"})
    assert response.status_code == 200
    assert question["stem"] not in response.text
    for option in question["options"]:
        assert f"{option['key']}. {option['text']}" not in response.text
    assert "作答 B" not in response.text
