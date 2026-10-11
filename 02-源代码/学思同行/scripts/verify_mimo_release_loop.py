"""Two-stage, opt-in real-model acceptance on an isolated demonstration student.

--generate makes one paid textbook-quiz request. Review the generated original
material/question privately before --finish. Neither stage touches an existing
browser login, existing student account or server configuration. Cookies and
unsubmitted answer keys stay in the ignored private directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
MATERIAL = """# 交付验收原创讲义：栈与队列

## 栈

栈是一种后进先出的线性数据结构。元素只允许在栈顶插入和删除。
入栈把元素添加到栈顶；出栈移除并返回当前栈顶元素。
在空栈中依次压入 A、B、C 后，栈顶为 C，依次出栈的顺序为 C、B、A。
厨房叠放盘子时，最后放上去的盘子最先被拿走，可帮助理解后进先出。
空栈没有可弹出的元素，不能把出栈失败理解为成功弹出了一个值。

## 队列

队列是一种先进先出的线性数据结构。元素从队尾加入，从队首移除。
在空队列中依次加入 A、B、C 后，依次移除的顺序为 A、B、C。
按先到先服务排队可帮助理解先进先出。
栈与队列的主要区别是受限插入、删除的位置及由此形成的取出顺序。
"""


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--generate", action="store_true")
    action.add_argument("--capture-existing", action="store_true", help="Read only the already generated, owned quiz; never generates another group")
    action.add_argument("--readback-final", action="store_true", help="Verify an already submitted loop using GET only; no repeated grading or plan creation")
    action.add_argument("--finish", action="store_true")
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--private-dir", type=Path, default=ROOT / "data/private-validation/mimo-release-loop-20261011")
    parser.add_argument("--database", type=Path, default=ROOT / "data/tutor.sqlite3")
    parser.add_argument("--report", type=Path, default=ROOT / "data/validation/mimo-learning-loop.json")
    parser.add_argument("--independently-approved-answer", choices=["A", "B", "C", "D"])
    parser.add_argument("--review-note", default="")
    args = parser.parse_args()
    if not args.base.startswith("http://127.0.0.1:"):
        raise ValueError("This script permits only the local application")
    private = args.private_dir.resolve()
    state_file = private / "state.json"
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "New isolated demonstration student; original stack/queue Markdown; one real Mimo textbook question",
              "checks": [], "completed": False, "passed": False,
              "browser_login_changed": False, "original_student_modified": False,
              "boundaries": ["One basic generated question is an acceptance case, not a question-bank accuracy evaluation.",
                             "A separate human-style review precedes the deliberately incorrect submission.",
                             "Input material is original synthetic teaching content; cookies and hidden keys remain private."]}

    def check(name, condition, details=None):
        item = {"name": name, "passed": bool(condition), "details": details}
        report["checks"] = [prior for prior in report["checks"] if prior["name"] != name]
        report["checks"].append(item)
        write(args.report, report)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if not condition:
            raise AssertionError(name)

    def api(client, method, path, **kwargs):
        response = client.request(method, "/api" + path, **kwargs)
        if response.status_code >= 400:
            write(private / "last-error.json", {"status": response.status_code, "body": response.text})
        response.raise_for_status()
        return response.json()

    try:
        if args.generate:
            if state_file.exists():
                raise RuntimeError("Existing generated quiz will not be replaced or silently regenerated")
            with httpx.Client(base_url=args.base, timeout=240, trust_env=False) as client:
                config = api(client, "GET", "/config/public")
                check("public_configuration_is_current_mimo", config["quiz_model"] == "mimo-v2.6-flash" and config["review_model"] == "mimo-v2.6-flash" and config["configured"] is True,
                      {"quiz_model": config["quiz_model"], "review_model": config["review_model"]})
                user = api(client, "POST", "/auth/demo")
                course = api(client, "POST", "/courses", json={"title": "交付验收原创栈与队列", "description": "独立验收账户原创材料，不修改原学习账户"})
                uploaded = api(client, "POST", f"/courses/{course['id']}/documents",
                               files={"file": ("原创栈与队列讲义.md", MATERIAL.encode("utf-8"), "text/markdown")}, data={"rights_confirmed": "true"})
                state = {"user_id": user["id"], "course_id": course["id"], "document_id": uploaded["id"], "cookies": dict(client.cookies)}
                write(state_file, state)
                (private / "original-material.md").write_text(MATERIAL, encoding="utf-8")
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    document = next(item for item in api(client, "GET", f"/courses/{course['id']}/documents") if item["id"] == uploaded["id"])
                    if document["status"] in {"ready", "failed"}:
                        break
                    time.sleep(0.5)
                check("original_material_is_ready", document["status"] == "ready" and document["chunk_count"] > 0,
                      {"chunks": document["chunk_count"], "source_type": document["source_type"]})
                started = time.perf_counter()
                quiz = api(client, "POST", f"/courses/{course['id']}/quizzes", json={"topic": "栈", "difficulty": "基础", "count": 1})
                state["quiz_id"] = quiz["id"]
                write(state_file, state)
                write(private / "creation-response.json", quiz)
                check("actual_mimo_generated_and_reviewed_one_textbook_question", quiz["source_kind"] == "textbook_generated" and len(quiz["questions"]) == 1
                      and quiz.get("generation_model") == "mimo-v2.6-flash" and quiz.get("review_model") == "mimo-v2.6-flash",
                      {"generation_model": quiz.get("generation_model"), "review_model": quiz.get("review_model"), "seconds": round(time.perf_counter() - started, 2)})
                check("student_view_hides_unsubmitted_answer", all(not any(key in q for key in ("answer", "correct_answer", "explanation", "source_ids")) for q in quiz["questions"]))
                with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                    connection.row_factory = sqlite3.Row
                    row = connection.execute("SELECT questions FROM quizzes WHERE id=? AND user_id=? AND course_id=?", (quiz["id"], user["id"], course["id"])).fetchone()
                    questions = json.loads(row["questions"])
                    chunk_rows = connection.execute("SELECT id,text,section,page FROM chunks WHERE document_id=? AND user_id=?", (uploaded["id"], user["id"])).fetchall()
                source_ids = {row["id"] for row in chunk_rows}
                check("persisted_question_uses_only_uploaded_original_sources", all(q.get("source_ids") and set(q["source_ids"]) <= source_ids for q in questions))
                write(private / "generated-question-review.json", {"public_quiz": quiz, "questions": questions, "source_chunks": [dict(row) for row in chunk_rows]})
                report["generation_model"] = quiz.get("generation_model")
                report["review_model"] = quiz.get("review_model")
                report["requires_independent_semantic_review"] = True
        elif args.capture_existing:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            report = json.loads(args.report.read_text(encoding="utf-8"))
            write(private / "capture-before-correction.json", report)
            with httpx.Client(base_url=args.base, timeout=30, trust_env=False, cookies=state["cookies"]) as client:
                check("existing_generated_quiz_is_read_without_new_generation", api(client, "GET", "/auth/me")["id"] == state["user_id"])
                quiz = api(client, "GET", f"/quizzes/{state['quiz_id']}")
            with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT questions FROM quizzes WHERE id=? AND user_id=? AND course_id=?", (state["quiz_id"], state["user_id"], state["course_id"])).fetchone()
                questions = json.loads(row["questions"])
                chunk_rows = connection.execute("SELECT id,text,section,page FROM chunks WHERE document_id=? AND user_id=?", (state["document_id"], state["user_id"])).fetchall()
            source_ids = {row["id"] for row in chunk_rows}
            check("persisted_question_uses_only_uploaded_original_sources", all(q.get("source_ids") and set(q["source_ids"]) <= source_ids for q in questions))
            check("captured_existing_quiz_remains_unsubmitted", quiz["submitted"] is False and [q["stem"] for q in quiz["questions"]] == [q["stem"] for q in questions])
            write(private / "generated-question-review.json", {"public_quiz": quiz, "questions": questions, "source_chunks": [dict(row) for row in chunk_rows]})
            creation = next(item["details"] for item in report["checks"] if item["name"] == "actual_mimo_generated_and_reviewed_one_textbook_question" and item["passed"])
            report["generation_model"] = creation["generation_model"]
            report["review_model"] = creation["review_model"]
            report["requires_independent_semantic_review"] = True
            report.pop("failure_type", None)
            report["harness_correction"] = "Read-only SQL capture corrected to actual section/page columns; no model request or business mutation repeated"
        elif args.readback_final:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            generated = json.loads((private / "generated-question-review.json").read_text(encoding="utf-8"))
            question = generated["questions"][0]
            report = json.loads(args.report.read_text(encoding="utf-8"))
            if not (private / "first-finish-harness-result.json").exists():
                write(private / "first-finish-harness-result.json", report)
            prior_check = [item for item in report["checks"] if item["name"] == "export_contains_the_actual_wrong_answer_and_review_record"]
            if prior_check and prior_check[-1]["passed"] is False:
                report["checks"] = [item for item in report["checks"] if item["name"] != "export_contains_the_actual_wrong_answer_and_review_record"]
                report["harness_export_correction"] = "Export contract contains assessed choices/explanations and plans; it currently omits the question stem. No write or model request repeated."
            report.pop("failure_type", None)
            with httpx.Client(base_url=args.base, timeout=30, trust_env=False, cookies=state["cookies"]) as client:
                check("final_readback_uses_only_the_new_validation_student", api(client, "GET", "/auth/me")["id"] == state["user_id"])
                quiz = api(client, "GET", f"/quizzes/{state['quiz_id']}")
                grade = quiz["result"]
                check("graded_result_survives_readback", quiz["submitted"] and grade["total"] == 1 and grade["correct"] == 0)
                practice = api(client, "GET", f"/courses/{state['course_id']}/practice")
                mistakes = [item for item in practice["mistakes"] if item["stem"] == question["stem"]]
                check("actual_mistake_survives_readback", len(mistakes) == 1 and mistakes[0]["correct_answer"] == question["answer"])
                plans = api(client, "GET", f"/courses/{state['course_id']}/plans")
                plan = next(item for item in plans if item["items"][0]["completed"])
                check("actual_completed_review_plan_survives_readback", plan["days"] == 7 and len(plan["items"]) == 7)
                exported = client.get("/api/export", params={"course_id": state["course_id"]})
                check("export_contains_the_actual_wrong_answer_and_review_record", exported.status_code == 200 and "attachment" in exported.headers.get("content-disposition", "")
                      and question["stem"] in exported.text and all(f"{option['key']}. {option['text']}" in exported.text for option in question["options"])
                      and question["explanation"] in exported.text and "错误" in exported.text and "正确 " + question["answer"] in exported.text and "- [x]" in exported.text,
                      {"downloaded_bytes": len(exported.content), "question_stem_present": question["stem"] in exported.text})
                write(private / "loop-result.json", {"grade": grade, "mistakes": mistakes, "plan": plan, "export": exported.text})
            report["requires_independent_semantic_review"] = False
            if "harness_export_correction" in report:
                report["historical_harness_export_correction"] = report.pop("harness_export_correction")
            report["latest_export_contains_submitted_question_context"] = True
            report["completed"] = True
            report["passed"] = True
        else:
            if not args.independently_approved_answer or not args.review_note.strip():
                raise ValueError("Finish requires an independently checked answer and substantive review note")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            generated = json.loads((private / "generated-question-review.json").read_text(encoding="utf-8"))
            question = generated["questions"][0]
            report = json.loads(args.report.read_text(encoding="utf-8"))
            check("independent_semantic_review_matches_generated_key", question["answer"] == args.independently_approved_answer,
                  {"question_count": 1, "review_note": args.review_note.strip(), "basis": "Original material and each option, checked independently after generation"})
            options = [item["key"] for item in question["options"]]
            incorrect = next(key for key in options if key != args.independently_approved_answer)
            with httpx.Client(base_url=args.base, timeout=120, trust_env=False, cookies=state["cookies"]) as client:
                check("only_the_new_validation_student_is_authenticated", api(client, "GET", "/auth/me")["id"] == state["user_id"])
                grade = api(client, "POST", f"/quizzes/{state['quiz_id']}/submit", json={"answers": {question["id"]: incorrect}})
                check("deliberately_wrong_answer_is_actually_graded_wrong", grade["total"] == 1 and grade["correct"] == 0)
                again = api(client, "POST", f"/quizzes/{state['quiz_id']}/submit", json={"answers": {question["id"]: args.independently_approved_answer}})
                check("repeat_submit_returns_the_same_authoritative_result", again == grade)
                practice = api(client, "GET", f"/courses/{state['course_id']}/practice")
                mistakes = [item for item in practice["mistakes"] if item["stem"] == question["stem"]]
                check("one_actual_mistake_contains_the_correct_answer_and_explanation", len(mistakes) == 1 and mistakes[0]["correct_answer"] == args.independently_approved_answer
                      and mistakes[0]["explanation"] == question["explanation"])
                plan = api(client, "POST", f"/courses/{state['course_id']}/plans", json={"days": 7, "daily_minutes": 15})
                state["plan_id"] = plan["id"]
                write(state_file, state)
                check("actual_seven_day_review_plan_has_usable_tasks", len(plan["items"]) >= 1 and plan["days"] == 7,
                      {"tasks": len(plan["items"]), "basis": plan["basis"], "concept_measured": question.get("concept_measured")})
                updated = api(client, "PATCH", f"/plans/{plan['id']}/items/{plan['items'][0]['id']}", json={"completed": True})
                reloaded_plan = next(item for item in api(client, "GET", f"/courses/{state['course_id']}/plans") if item["id"] == plan["id"])
                check("actual_review_completion_survives_readback", updated["items"][0]["completed"] and reloaded_plan["items"][0]["completed"])
                exported = client.get("/api/export", params={"course_id": state["course_id"]})
                check("export_contains_the_actual_wrong_answer_and_review_record", exported.status_code == 200 and "attachment" in exported.headers.get("content-disposition", "")
                      and question["stem"] in exported.text and all(f"{option['key']}. {option['text']}" in exported.text for option in question["options"])
                      and question["explanation"] in exported.text and "错误" in exported.text and "正确 " + question["answer"] in exported.text and "- [x]" in exported.text,
                      {"downloaded_bytes": len(exported.content), "question_stem_present": question["stem"] in exported.text})
                write(private / "loop-result.json", {"grade": grade, "mistakes": mistakes, "plan": plan, "updated_plan": reloaded_plan, "export": exported.text})
            report["requires_independent_semantic_review"] = False
            report["completed"] = True
            report["passed"] = True
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        report["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["passed_checks"] = sum(item["passed"] for item in report["checks"])
        report["total_checks"] = len(report["checks"])
        write(args.report, report)


if __name__ == "__main__":
    main()
