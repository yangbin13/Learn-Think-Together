"""Verify an isolated source checkout over real HTTP without paid model calls.

The process owns its port, data directory and child Uvicorn process. It never
uses the student's database or the original running application. The same
script can check an extracted submission by passing --project-root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    data_dir = args.data_dir.resolve()
    report_path = args.report.resolve()
    if not 1024 <= args.port <= 65535 or args.port in {8765, 8766}:
        raise ValueError("Choose an independent high port, excluding 8765 and 8766")
    if data_dir == (project / "data").resolve() or data_dir == project:
        raise ValueError("Choose a separate, fresh data directory")
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("Data directory must be empty; no existing database is reused")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("Selected port is already in use; no process was stopped")
    data_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    report = {
        "started_at_utc": started,
        "scope": "Fresh data directory and owned Uvicorn process; real HTTP, real files, no model or embedding provider calls",
        "port": args.port,
        "checks": [],
        "paid_provider_requests": 0,
        "mocked_api_responses": 0,
        "original_database_used": False,
        "original_service_modified": False,
        "completed": False,
        "passed": False,
        "boundaries": [
            "Uses the available Python interpreter and installed dependencies, not a newly downloaded runtime.",
            "Frontend resources are served and hash-checked over HTTP; this is not rendered browser interaction.",
            "Curated-bank grading checks actual persistence and idempotence, not independent question semantic correctness.",
            "No-key continuation is an honest static teaching prompt, never a fabricated model answer.",
        ],
    }
    env = os.environ.copy()
    env.update({
        "TUTOR_DATA_DIR": str(data_dir),
        "TUTOR_DATABASE": str(data_dir / "tutor.sqlite3"),
        "LLM_API_KEY": "",
        "LLM_API_BASE": "https://api.xiaomimimo.com/v1",
        "LLM_MODEL": "mimo-v2.6-flash",
        "LLM_REVIEW_MODEL": "",
        "LLM_QUIZ_MODEL": "",
        "EMBEDDING_ENABLED": "false",
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_API_BASE": "",
        "COOKIE_SECURE": "false",
        "MAX_UPLOAD_MB": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    server = None
    log = (data_dir / "server.log").open("ab")

    def save() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, condition, details=None):
        item = {"name": name, "passed": bool(condition), "details": details}
        report["checks"].append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if not condition:
            raise AssertionError(name)

    def start_server() -> None:
        nonlocal server
        server = subprocess.Popen(
            [str(args.python.resolve()), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
            cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 150
        with httpx.Client(base_url=base, timeout=3, trust_env=False) as connection:
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Owned service exited during startup; diagnostic log remains private")
                try:
                    if connection.get("/api/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)
        raise TimeoutError("Owned service did not become ready")

    def stop_server() -> None:
        nonlocal server
        if server is None:
            return
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server = None

    def api(client, method, path, **kwargs):
        response = client.request(method, "/api" + path, **kwargs)
        response.raise_for_status()
        return response.json()

    base = f"http://127.0.0.1:{args.port}"
    try:
        start_server()
        check("fresh_service_health", True)
        with httpx.Client(base_url=base, timeout=45, trust_env=False) as owner:
            config = api(owner, "GET", "/config/public")
            check("public_config_hides_credentials_and_disables_paid_providers", config.get("configured") is False
                  and not any(re.search(r"key|secret|password|token", key, re.I) for key in config)
                  and config.get("embedding_model") == "disabled / BM25")
            page = owner.get("/")
            local_index = project / "frontend/dist/index.html"
            check("same_port_frontend_matches_current_build", page.status_code == 200 and page.content == local_index.read_bytes())
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
            asset_details = []
            for asset in assets:
                response = owner.get(asset)
                local = project / "frontend/dist" / asset.lstrip("/")
                asset_details.append({"path": asset, "status": response.status_code,
                    "bytes": len(response.content), "sha256_matches_build": response.content == local.read_bytes()})
            check("actual_js_and_css_match_current_build", bool(assets) and all(item["status"] == 200 and item["sha256_matches_build"] for item in asset_details), asset_details)
            check("anonymous_api_is_rejected", owner.get("/api/courses").status_code == 401)
            user = api(owner, "POST", "/auth/demo")
            courses = api(owner, "GET", "/courses")
            check("fresh_demo_has_four_course_assistants", len([course for course in courses if course["is_builtin"]]) == 4
                  and all(course["document_count"] > 0 for course in courses))
            check("blank_course_title_is_rejected", owner.post("/api/courses", json={"title": "   "}).status_code == 422)
            course = api(owner, "POST", "/courses", json={"title": "交付验收原创资料", "description": "仅此临时数据目录"})
            upload_path = f"/api/courses/{course['id']}/documents"
            check("upload_requires_rights_confirmation", owner.post(upload_path,
                  files={"file": ("source.md", b"original teaching text")}).status_code == 422)
            check("unsupported_upload_extension_is_rejected", owner.post(upload_path,
                  files={"file": ("source.exe", b"not executable")}, data={"rights_confirmed": "true"}).status_code == 415)
            check("empty_upload_is_rejected", owner.post(upload_path,
                  files={"file": ("empty.txt", b"")}, data={"rights_confirmed": "true"}).status_code == 422)
            check("one_mib_upload_boundary_is_enforced", owner.post(upload_path,
                  files={"file": ("oversized.txt", b"x" * (1024 * 1024 + 1))}, data={"rights_confirmed": "true"}).status_code == 413)
            source_text = "# 交付验收原创教材\n\n## 栈\n\n栈是一种后进先出的数据结构，只在栈顶进行插入和删除。入栈添加元素，出栈移除栈顶元素。\n\n## 队列\n\n队列先进先出，队尾加入、队首移除。\n"
            uploaded = api(owner, "POST", f"/courses/{course['id']}/documents",
                           files={"file": ("交付验收原创教材.md", source_text.encode("utf-8"), "text/markdown")}, data={"rights_confirmed": "true"})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                document = next(item for item in api(owner, "GET", f"/courses/{course['id']}/documents") if item["id"] == uploaded["id"])
                if document["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.3)
            check("original_markdown_is_ingested_without_embedding", document["status"] == "ready" and document["chunk_count"] > 0,
                  {"chunks": document["chunk_count"], "embedding_status": document["embedding_status"]})
            downloaded = owner.get(f"/api/documents/{uploaded['id']}/download")
            check("uploaded_source_download_is_byte_identical", downloaded.status_code == 200 and downloaded.content == source_text.encode("utf-8"))
            graph = api(owner, "GET", f"/courses/{course['id']}/graph")
            check("original_material_creates_persisted_concept_nodes", len(graph["nodes"]) >= 2)
            os_books = api(owner, "GET", "/courses/operating-systems/documents")
            book = next(item for item in os_books if item["source_type"] == "open_textbook")
            image = owner.get(f"/api/documents/{book['id']}/pages/1/image")
            check("real_pdf_page_renders_to_png", image.status_code == 200 and image.content.startswith(b"\x89PNG\r\n\x1a\n") and len(image.content) > 1000,
                  {"physical_pages": book["page_count"], "png_bytes": len(image.content)})
            quiz = api(owner, "POST", "/courses/data-structures/quizzes", json={"count": 3, "difficulty": "基础"})
            check("curated_quiz_hides_answers_before_submission", len(quiz["questions"]) == 3 and all(
                not any(key in question for key in ("answer", "correct_answer", "explanation", "source_ids")) for question in quiz["questions"]))
            first = quiz["questions"][0]
            answer = first["options"][0]["key"]
            draft = api(owner, "PUT", f"/quizzes/{quiz['id']}/draft", json={"answers": {first["id"]: answer}, "expected_version": 0})
            pending = api(owner, "GET", f"/quizzes/{quiz['id']}")
            check("quiz_draft_is_saved_without_grading", pending["draft_answers"] == {first["id"]: answer} and not pending["submitted"])
            conflict = owner.put(f"/api/quizzes/{quiz['id']}/draft", json={"answers": {}, "expected_version": 0})
            check("stale_draft_version_is_rejected", conflict.status_code == 409)
            answers = {question["id"]: question["options"][0]["key"] for question in quiz["questions"]}
            grade = api(owner, "POST", f"/quizzes/{quiz['id']}/submit", json={"answers": answers})
            grade_again = api(owner, "POST", f"/quizzes/{quiz['id']}/submit", json={"answers": {}})
            check("actual_submission_is_idempotent", grade == grade_again and grade["total"] == 3,
                  {"total": grade["total"], "correct": grade["correct"]})
            check("review_plan_lower_minutes_boundary_is_enforced", owner.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 14}).status_code == 422)
            plan = api(owner, "POST", "/courses/data-structures/plans", json={"days": 7, "daily_minutes": 15})
            completed = api(owner, "PATCH", f"/plans/{plan['id']}/items/{plan['items'][0]['id']}", json={"completed": True})
            check("review_plan_completion_is_persisted", completed["items"][0]["completed"] is True)
            exported = owner.get("/api/export", params={"course_id": "data-structures"})
            check("real_learning_record_export_is_downloadable", exported.status_code == 200 and "attachment" in exported.headers.get("content-disposition", "")
                  and "数据结构" in exported.text and len(exported.content) > 100)
            with httpx.Client(base_url=base, timeout=30, trust_env=False) as other:
                api(other, "POST", "/auth/demo")
                statuses = {"course": other.get(f"/api/courses/{course['id']}").status_code,
                            "document": other.get(f"/api/documents/{uploaded['id']}/download").status_code,
                            "quiz": other.get(f"/api/quizzes/{quiz['id']}").status_code}
                check("foreign_student_cannot_read_private_resources", all(status == 404 for status in statuses.values()), statuses)
            message = owner.post("/api/chat", json={"course_id": course["id"], "message": "请解释栈", "mode": "explain"})
            events = []
            for block in message.text.replace("\r\n", "\n").split("\n\n"):
                event = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "message")
                payload = "\n".join(line[6:] for line in block.splitlines() if line.startswith("data: "))
                if payload:
                    events.append((event, json.loads(payload)))
            done = next((payload for event, payload in events if event == "done"), None)
            check("no_key_returns_honest_learning_continuation", message.status_code == 200 and done is not None and done.get("degraded") is True
                  and done.get("model") == "server/learning-continuation" and not any(event == "error" for event, _ in events))
            cookies = dict(owner.cookies)
            stop_server()
            start_server()
            owner.cookies.update(cookies)
            check("restart_keeps_owned_student_session", api(owner, "GET", "/auth/me")["id"] == user["id"])
            check("restart_keeps_uploaded_source", owner.get(f"/api/documents/{uploaded['id']}/download").content == source_text.encode("utf-8"))
            check("restart_keeps_quiz_result", api(owner, "GET", f"/quizzes/{quiz['id']}")["result"] == grade)
            persisted_plan = next(item for item in api(owner, "GET", "/courses/data-structures/plans") if item["id"] == plan["id"])
            check("restart_keeps_completed_review_task", persisted_plan["items"][0]["completed"] is True)
        with sqlite3.connect(data_dir / "tutor.sqlite3") as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            foreign_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        check("isolated_database_integrity_and_foreign_keys", integrity == "ok" and not foreign_errors)
        report["completed"] = True
        report["passed"] = True
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        stop_server()
        log.close()
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["passed_checks"] = sum(check["passed"] for check in report["checks"])
        report["total_checks"] = len(report["checks"])
        report["owned_process_stopped"] = True
        save()


if __name__ == "__main__":
    main()
