from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import sqlite3
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator

from .agent import TutorAgent
from .auth import COOKIE_NAME, create_session, current_user, make_user, public_user, token_hash, verify_password
from .config import Settings
from .citations import with_location
from .db import MAX_QUIZ_DRAFT_VERSION, Database, dumps, loads, now, uid
from .ingestion import IngestionWorker, rebuild_extracted_graph, seed_catalog
from .knowledge_map import build_knowledge_map, build_mindmap_evidence
from .figures import add_manual_figure, figure_index_status, figure_public, queue_figure_index
from .materials import MaterialAccessError, checked_page_range, render_pdf_page
from .practice import create_quiz, load_quiz, make_plan, save_quiz_draft, submit_quiz
from .plan_goals import match_plan_goals
from .provider import Provider, ProviderError
from .chat_recovery import recover_chat
from .retrieval import Retriever


class ManualFigureBody(BaseModel):
    physical_page: int = Field(ge=1, strict=True)
    figure_number: str = Field(default="", max_length=40)
    caption: str = Field(min_length=1, max_length=200)

    @field_validator("figure_number", "caption")
    @classmethod
    def single_line(cls, value, info):
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError("图号和图题只能包含单行文字")
        if info.field_name == "caption" and not value:
            raise ValueError("图题不能为空")
        return value


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_\u4e00-\u9fff.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=40)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CourseBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1200)
    color: str = Field(default="#52796f", pattern=r"^#[0-9a-fA-F]{6}$")


class SessionBody(BaseModel):
    title: str = Field(default="新学习对话", min_length=1, max_length=80)


class ChatBody(BaseModel):
    course_id: str
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["explain", "socratic", "solve"] = "explain"


class QuizBody(BaseModel):
    topic: str | None = Field(default=None, max_length=200)
    difficulty: Literal["基础", "进阶", "挑战"] = "基础"
    count: Literal[1, 3, 5] = 3


class SubmitBody(BaseModel):
    answers: dict[str, str]


class QuizDraftBody(BaseModel):
    model_config = {"extra": "forbid"}
    answers: dict[str, Literal["A", "B", "C", "D"]] = Field(max_length=5)
    expected_version: int = Field(ge=0, le=MAX_QUIZ_DRAFT_VERSION, strict=True)


class PlanBody(BaseModel):
    days: Literal[7, 14, 30] = 7
    daily_minutes: int = Field(default=30, ge=15, le=180)
    goal: str | None = Field(default=None, max_length=500)


class CompleteBody(BaseModel):
    completed: bool


class MindmapAnnotationBody(BaseModel):
    model_config = {"extra": "forbid"}
    display_label: str = Field(max_length=160)
    study_note: str = Field(max_length=2000)

    @field_validator("display_label")
    @classmethod
    def clean_display_label(cls, value):
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
            raise ValueError("显示标题不能包含换行或控制字符")
        return value.strip()

    @field_validator("study_note")
    @classmethod
    def clean_study_note(cls, value):
        if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\r\n" for char in value):
            raise ValueError("学习笔记不能包含无效控制字符")
        return value.strip()


class ClosingStreamingResponse(StreamingResponse):
    """ASGI 2.4 send failures must close the retained generator immediately."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            closer = getattr(self.body_iterator, "aclose", None)
            if closer:
                await closer()


def course_access(db, course_id, user_id):
    course = db.one("SELECT * FROM courses WHERE id=? AND (is_builtin=1 OR user_id=?)", (course_id, user_id))
    if not course:
        raise HTTPException(404, "课程不存在")
    return course


def session_access(db, session_id, user_id):
    session = db.one("SELECT * FROM tutor_sessions WHERE id=? AND user_id=?", (session_id, user_id))
    if not session:
        raise HTTPException(404, "对话不存在")
    return session


def document_access(db, document_id, user_id):
    doc = db.one("SELECT d.* FROM documents d JOIN courses c ON c.id=d.course_id WHERE d.id=? "
                 "AND (d.user_id IS NULL OR d.user_id=?) AND (c.is_builtin=1 OR c.user_id=?)", (document_id, user_id, user_id))
    if not doc:
        raise HTTPException(404, "教材不存在")
    return doc


def document_public(doc):
    result = {key: doc[key] for key in ("id", "course_id", "title", "filename", "status", "page_count", "chunk_count", "error", "source_type", "license", "source_url", "created_at", "embedding_status")}
    result["progress"] = loads(doc.get("progress"), {})
    result["quality"] = loads(doc.get("quality"), {})
    return result


def course_public(db, course, user_id):
    result = {key: course[key] for key in ("id", "title", "description", "code", "color", "icon", "assistant_name")}
    result["is_builtin"] = bool(course["is_builtin"])
    counts = db.one("SELECT COUNT(*) AS n,COALESCE(SUM(chunk_count),0) AS chunks FROM documents "
                    "WHERE course_id=? AND (user_id IS NULL OR user_id=?)", (course["id"], user_id))
    result.update(document_count=counts["n"], chunk_count=counts["chunks"],
                  concept_count=db.one("SELECT COUNT(*) AS n FROM concepts WHERE course_id=?", (course["id"],))["n"])
    return result


def session_public(row):
    return {key: row[key] for key in ("id", "title", "created_at", "updated_at")}


def create_tutor_session(db, course_id, user_id, title):
    created = now()
    session = {"id": uid(), "course_id": course_id, "user_id": user_id, "title": title, "created_at": created, "updated_at": created}
    db.execute("INSERT INTO tutor_sessions VALUES (?,?,?,?,?,?)", tuple(session.values()))
    return session


def weak_concepts(db, user_id, course_id=None):
    sql, args = "SELECT course_id,concept,attempts,correct FROM mastery WHERE user_id=?", [user_id]
    if course_id:
        sql += " AND course_id=?"
        args.append(course_id)
    rows = db.all(sql, args)
    result = [{"concept": row["concept"], "mastery": round(row["correct"] / row["attempts"], 3), "attempts": row["attempts"], "course_id": row["course_id"]}
              for row in rows if row["attempts"] and row["correct"] / row["attempts"] < .8]
    return sorted(result, key=lambda row: (row["mastery"], -row["attempts"]))[:10]


def stats(db, user_id, course_id=None):
    args, where = [user_id], "user_id=?"
    if course_id:
        where += " AND course_id=?"
        args.append(course_id)
    mastery = db.one(f"SELECT COALESCE(SUM(attempts),0) AS attempts,COALESCE(SUM(correct),0) AS correct FROM mastery WHERE {where}", args)
    practice_count = db.one(f"SELECT COUNT(*) AS n FROM quizzes WHERE {where} AND submitted=1", args)["n"]
    chats = db.one("SELECT COUNT(*) AS n FROM messages m JOIN tutor_sessions s ON s.id=m.session_id WHERE s." + where.replace(" AND course_id", " AND s.course_id") + " AND m.role='assistant'", args)["n"]
    activity = db.all(f"SELECT type,label,created_at FROM activity WHERE {where} ORDER BY created_at DESC LIMIT 10", args)
    return {"questions_answered": chats, "practice_count": practice_count,
            "accuracy": round(mastery["correct"] / mastery["attempts"], 3) if mastery["attempts"] else None,
            "mastery": round(mastery["correct"] / mastery["attempts"], 3) if mastery["attempts"] else None,
            "recent_activity": activity, "weak_concepts": weak_concepts(db, user_id, course_id)}


def create_app(settings: Settings | None = None, provider=None):
    settings = settings or Settings.from_env()
    db = Database(settings.database)
    provider = provider or Provider(settings)
    retriever = Retriever(db, provider)
    worker = IngestionWorker(db, provider, settings)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(seed_catalog, db, settings)
        await worker.start()
        try:
            yield
        finally:
            await worker.stop()
            if hasattr(provider, "close"):
                await provider.close()

    app = FastAPI(title="学思同行 · 专业课智能助教", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.db = settings, db
    app.state.provider, app.state.retriever, app.state.worker = provider, retriever, worker
    app.state.agent = TutorAgent(db, provider, retriever, settings)
    app.state.session_locks = {}
    app.state.chat_semaphore = asyncio.Semaphore(4)
    app.state.login_attempts = {}
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        from fastapi.responses import JSONResponse
        fields = [".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()]
        return JSONResponse({"detail": "请求参数无效，请检查：" + "、".join(fields[:8])}, status_code=422)

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "持久化服务暂不可用，请稍后重试"}, status_code=503)

    @app.get("/health")
    @app.get("/api/health")
    def health():
        db.one("SELECT 1 AS ok")
        return {"status": "ok", "service": "study-together", "agent_framework": "LangGraph", "storage": "SQLite"}

    @app.get("/api/config/public")
    @app.get("/config/public")
    def public_config():
        return {"provider": "OpenAI compatible", "model": settings.model, "review_model": settings.review_model or settings.model,
                "quiz_model": settings.quiz_model or settings.model,
                "configured": bool(provider.configured),
                "embedding_model": settings.embedding_model if settings.embedding_enabled else "disabled / BM25", "agent_framework": "LangGraph",
                "vision_model": settings.vision_model if settings.vision_enabled else None, "vision_enabled": settings.vision_enabled,
                "max_upload_bytes": settings.max_upload_bytes}

    @app.post("/api/auth/register")
    def register(body: RegisterBody, request: Request, response: Response):
        username, display = body.username.strip(), body.display_name.strip()
        if not display:
            raise HTTPException(422, "显示名不能为空")
        try:
            user = make_user(db, username, body.password, display)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在") from None
        create_session(request, response, user["id"])
        return user

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        import time
        ip = request.client.host if request.client else "local"
        key = (ip, body.username.strip().casefold())
        attempts = app.state.login_attempts.get(key, [])
        attempts = [timestamp for timestamp in attempts if time.monotonic() - timestamp < 300]
        if len(attempts) >= 12:
            raise HTTPException(429, "登录尝试过于频繁，请五分钟后再试")
        user = db.one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
        if not user or not verify_password(body.password, user["password_hash"]):
            attempts.append(time.monotonic())
            app.state.login_attempts[key] = attempts
            raise HTTPException(401, "用户名或密码不正确")
        app.state.login_attempts.pop(key, None)
        create_session(request, response, user["id"])
        return public_user(user)

    @app.post("/api/auth/demo")
    def demo(request: Request, response: Response):
        user = make_user(db, "demo_" + uid()[:12], secrets.token_urlsafe(32), "体验同学")
        create_session(request, response, user["id"])
        return user

    @app.get("/api/auth/me")
    def me(user=Depends(current_user)):
        return user

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash(token),))
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/courses")
    def courses(user=Depends(current_user)):
        rows = db.all("SELECT * FROM courses WHERE is_builtin=1 OR user_id=? ORDER BY is_builtin DESC,created_at", (user["id"],))
        return [course_public(db, row, user["id"]) for row in rows]

    @app.post("/api/courses")
    def add_course(body: CourseBody, user=Depends(current_user)):
        if not body.title.strip():
            raise HTTPException(422, "课程名称不能为空")
        course_id = uid()
        db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)", (course_id, user["id"], body.title.strip(), body.description.strip(), "CUSTOM", body.color,
                                                                      "layers", body.title.strip() + "助教", 0, now()))
        return course_public(db, course_access(db, course_id, user["id"]), user["id"])

    @app.get("/api/courses/{course_id}")
    def get_course(course_id: str, user=Depends(current_user)):
        return course_public(db, course_access(db, course_id, user["id"]), user["id"])

    @app.delete("/api/courses/{course_id}")
    def remove_course(course_id: str, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        if course["is_builtin"]:
            raise HTTPException(403, "内置课程不能删除")
        docs = db.all("SELECT path FROM documents WHERE course_id=? AND user_id=?", (course_id, user["id"]))
        if any(lock.locked() for session_id, lock in app.state.session_locks.items()
               if db.one("SELECT 1 FROM tutor_sessions WHERE id=? AND course_id=?", (session_id, course_id))):
            raise HTTPException(409, "课程正在生成回答，请等待完成后删除")
        db.execute("DELETE FROM courses WHERE id=? AND user_id=?", (course_id, user["id"]))
        for doc in docs:
            path = Path(doc["path"]).resolve()
            if path.is_relative_to(settings.data_dir.resolve()):
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/courses/{course_id}/documents")
    def documents(course_id: str, user=Depends(current_user)):
        course_access(db, course_id, user["id"])
        return [document_public(row) for row in db.all("SELECT * FROM documents WHERE course_id=? AND (user_id IS NULL OR user_id=?) ORDER BY created_at", (course_id, user["id"]))]

    @app.post("/api/courses/{course_id}/documents", status_code=202)
    async def upload(course_id: str, file: UploadFile = File(...), rights_confirmed: bool = Form(False), user=Depends(current_user)):
        target = None
        try:
            course_access(db, course_id, user["id"])
            if not rights_confirmed:
                raise HTTPException(422, "请确认你拥有上传和使用该教材的权利")
            original = (file.filename or "教材").replace("\\", "/").split("/")[-1][:180]
            extension = Path(original).suffix.lower()
            if extension not in {".pdf", ".docx", ".txt", ".md"}:
                raise HTTPException(415, "仅支持 PDF、DOCX、TXT、MD")
            doc_id, created = uid(), now()
            upload_dir = settings.data_dir / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            target = upload_dir / (doc_id + extension)
            total = 0
            with target.open("wb") as output:
                while piece := await file.read(1024 * 1024):
                    total += len(piece)
                    if total > settings.max_upload_bytes:
                        limit = settings.max_upload_bytes / (1024 * 1024)
                        raise HTTPException(413, f"教材文件最大 {limit:g} MiB（{settings.max_upload_bytes} 字节）")
                    output.write(piece)
            if not total:
                raise HTTPException(422, "教材文件为空")
            with db.connection() as conn:
                conn.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at) VALUES (?,?,?,?,?,?,'queued',?,?,?)",
                             (doc_id, course_id, user["id"], Path(original).stem, original, str(target), "uploaded_" + extension[1:], "用户确认拥有使用权；仅本人可见", created))
                conn.execute("INSERT INTO ingestion_jobs VALUES (?,?,'queued',0,NULL,?,?)", (uid(), doc_id, created, created))
                conn.execute("UPDATE documents SET progress=? WHERE id=?", (dumps({"stage": "queued", "current_page": 0, "total_pages": 0, "chunk_count": 0, "embedding_percent": 0, "updated_at": created}), doc_id))
                db.activity(user["id"], course_id, "upload", f"上传教材 {original}", conn)
        except BaseException:
            if target is not None:
                target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return document_public(document_access(db, doc_id, user["id"]))

    @app.post("/api/documents/{document_id}/retry", status_code=202)
    def retry_document(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["user_id"] != user["id"]:
            raise HTTPException(403, "只能重试本人上传教材")
        if doc["status"] in {"queued", "processing"}:
            raise HTTPException(409, "教材已在处理队列中")
        if doc["status"] == "ready" and doc["embedding_status"] != "lexical_fallback":
            raise HTTPException(409, "教材已完成；当前无需重试")
        if not Path(doc["path"]).is_file():
            raise HTTPException(404, "教材源文件不存在，请重新上传")
        progress = loads(doc.get("progress"), {})
        # Keep the last embedding checkpoint for resumable retry.
        if doc["embedding_status"] != "lexical_fallback":
            progress.update(stage="queued", current_page=0, embedding_percent=0)
        with db.connection() as conn:
            conn.execute("UPDATE documents SET status='queued',error=NULL,progress=? WHERE id=?", (dumps(progress), document_id))
            conn.execute("UPDATE ingestion_jobs SET status='queued',error=NULL,updated_at=? WHERE document_id=?", (now(), document_id))
        return document_public(document_access(db, document_id, user["id"]))

    @app.get("/api/documents/{document_id}/download")
    def download(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        path = Path(doc["path"])
        if not path.is_file():
            raise HTTPException(404, "教材源文件不存在")
        return FileResponse(path, filename=doc["filename"])

    @app.get("/api/documents/{document_id}/preview")
    def preview_pdf(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        path = Path(doc["path"])
        if path.suffix.lower() != ".pdf":
            raise HTTPException(415, "原文预览仅支持 PDF，其他文件可下载核验")
        if not path.is_file():
            raise HTTPException(404, "教材源文件不存在")
        return FileResponse(path, media_type="application/pdf", filename=doc["filename"], content_disposition_type="inline",
                            headers={"Cache-Control": "private", "X-Content-Type-Options": "nosniff", "X-PDF-Page-Count": str(doc["page_count"])})

    @app.delete("/api/documents/{document_id}")
    def remove_document(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["user_id"] != user["id"]:
            raise HTTPException(403, "内置教材不能删除")
        with db.connection() as conn:
            conn.execute("DELETE FROM documents WHERE id=? AND user_id=?", (document_id, user["id"]))
            rebuild_extracted_graph(conn, doc["course_id"])
        path = Path(doc["path"]).resolve()
        if path.is_relative_to(settings.data_dir.resolve()):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/documents/{document_id}/pages/{physical_page}/image")
    async def page_image(document_id: str, physical_page: int, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["status"] != "ready":
            raise HTTPException(409, "教材尚未就绪，请等待解析完成")
        try:
            rendered = await asyncio.to_thread(render_pdf_page, doc, physical_page)
        except MaterialAccessError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        return Response(rendered["png"], media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "X-PDF-Physical-Page": str(physical_page), "X-Image-Width": str(rendered["width"]), "X-Image-Height": str(rendered["height"])})

    @app.get("/api/documents/{document_id}/chunks")
    def chunks(document_id: str, page: int = Query(1, ge=1), user=Depends(current_user)):
        document_access(db, document_id, user["id"])
        return {"items": db.all("SELECT id,page,section,text FROM chunks WHERE document_id=? ORDER BY page,rowid LIMIT 20 OFFSET ?", (document_id, (page - 1) * 20)),
                "total": db.one("SELECT COUNT(*) AS n FROM chunks WHERE document_id=?", (document_id,))["n"]}

    @app.get("/api/documents/{document_id}/figures")
    def document_figures(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["status"] != "ready":
            raise HTTPException(409, "教材尚未就绪，请等待解析完成")
        with db.connection() as conn:
            rows = conn.execute("SELECT * FROM document_figures WHERE document_id=? AND physical_page BETWEEN 1 AND ? "
                                "AND (source_kind='auto' OR (source_kind='manual' AND user_id=?)) "
                                "ORDER BY physical_page,figure_number,id", (document_id, doc["page_count"], user["id"])).fetchall()
            return {"document_id": document_id, "index": figure_index_status(conn, document_id),
                    "items": [figure_public(row) for row in rows]}

    @app.post("/api/documents/{document_id}/figures", status_code=201)
    def create_manual_figure(document_id: str, body: ManualFigureBody, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["status"] != "ready":
            raise HTTPException(409, "教材尚未就绪，请等待解析完成")
        if Path(doc["path"]).suffix.lower() != ".pdf":
            raise HTTPException(415, "图示标注仅支持 PDF 教材")
        if not Path(doc["path"]).is_file():
            raise HTTPException(404, "教材源文件不存在")
        try:
            checked_page_range(doc, body.physical_page, body.physical_page)
        except MaterialAccessError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        with db.connection() as conn:
            return add_manual_figure(conn, doc, user["id"], body.physical_page, body.figure_number, body.caption)

    @app.delete("/api/documents/{document_id}/figures/{figure_id}")
    def delete_manual_figure(document_id: str, figure_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["status"] != "ready":
            raise HTTPException(409, "教材尚未就绪，请等待解析完成")
        with db.connection() as conn:
            row = conn.execute("SELECT source_kind,user_id FROM document_figures WHERE id=? AND document_id=?",
                               (figure_id, document_id)).fetchone()
            if not row or row["source_kind"] == "manual" and row["user_id"] != user["id"]:
                raise HTTPException(404, "当前教材中不存在可删除的个人图示")
            if row["source_kind"] != "manual":
                raise HTTPException(403, "自动图题索引不可作为个人标注删除")
            conn.execute("DELETE FROM document_figures WHERE id=? AND document_id=? AND source_kind='manual' AND user_id=?",
                         (figure_id, document_id, user["id"]))
            conn.execute("DELETE FROM mindmap_annotations WHERE user_id=? AND course_id=? AND node_id=?",
                         (user["id"], doc["course_id"], figure_id))
        return {"ok": True}

    @app.post("/api/documents/{document_id}/figures/reindex", status_code=202)
    def reindex_figures(document_id: str, user=Depends(current_user)):
        doc = document_access(db, document_id, user["id"])
        if doc["user_id"] != user["id"]:
            raise HTTPException(403, "只能重试本人上传教材的图题索引")
        if doc["status"] != "ready":
            raise HTTPException(409, "教材尚未就绪，请等待解析完成")
        if Path(doc["path"]).suffix.lower() != ".pdf":
            raise HTTPException(415, "图题索引仅支持 PDF 教材")
        with db.connection() as conn:
            return {"document_id": document_id, "index": queue_figure_index(conn, doc)}

    @app.get("/api/courses/{course_id}/graph")
    def graph(course_id: str, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        with db.connection() as conn:
            return build_knowledge_map(conn, course, user["id"])

    @app.get("/api/courses/{course_id}/mindmap/nodes/{node_id}/evidence")
    def mindmap_evidence(course_id: str, node_id: str, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        with db.connection() as conn:
            result = build_mindmap_evidence(conn, course, user["id"], node_id)
        if result is None:
            raise HTTPException(404, "当前课程中不存在可查看的思维导图节点")
        return result

    def editable_mindmap_node(conn, course, user_id, node_id):
        tree = build_knowledge_map(conn, course, user_id)["tree"]
        node = next((item for item in tree["nodes"] if item["id"] == node_id), None)
        if not node or node["kind"] == "document" and node["document_id"] is None:
            raise HTTPException(404, "当前课程中不存在可编辑的思维导图节点")
        return node

    @app.get("/api/courses/{course_id}/mindmap/nodes/{node_id}/annotation")
    def get_mindmap_annotation(course_id: str, node_id: str, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        with db.connection() as conn:
            editable_mindmap_node(conn, course, user["id"], node_id)
            row = conn.execute("SELECT display_label,study_note,updated_at FROM mindmap_annotations "
                               "WHERE user_id=? AND course_id=? AND node_id=?",
                               (user["id"], course_id, node_id)).fetchone()
        return {"node_id": node_id, **dict(row)} if row else {
            "node_id": node_id, "display_label": "", "study_note": "", "updated_at": None}

    @app.put("/api/courses/{course_id}/mindmap/nodes/{node_id}/annotation")
    def put_mindmap_annotation(course_id: str, node_id: str, body: MindmapAnnotationBody, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        updated_at = now() if body.display_label or body.study_note else None
        with db.connection() as conn:
            editable_mindmap_node(conn, course, user["id"], node_id)
            if updated_at is None:
                conn.execute("DELETE FROM mindmap_annotations WHERE user_id=? AND course_id=? AND node_id=?",
                             (user["id"], course_id, node_id))
            else:
                conn.execute("INSERT INTO mindmap_annotations VALUES (?,?,?,?,?,?) "
                             "ON CONFLICT(user_id,course_id,node_id) DO UPDATE SET "
                             "display_label=excluded.display_label,study_note=excluded.study_note,updated_at=excluded.updated_at",
                             (user["id"], course_id, node_id, body.display_label, body.study_note, updated_at))
        return {"node_id": node_id, "display_label": body.display_label,
                "study_note": body.study_note, "updated_at": updated_at}

    @app.get("/api/courses/{course_id}/overview")
    def overview(course_id: str, user=Depends(current_user)):
        course = course_public(db, course_access(db, course_id, user["id"]), user["id"])
        return {**stats(db, user["id"], course_id), "documents": course["document_count"], "concepts": course["concept_count"]}

    @app.get("/api/dashboard")
    def dashboard(user=Depends(current_user)):
        aggregate = stats(db, user["id"])
        aggregate.pop("mastery")
        return {**aggregate, "courses": db.one("SELECT COUNT(*) AS n FROM courses WHERE is_builtin=1 OR user_id=?", (user["id"],))["n"],
                "study_days": db.one("SELECT COUNT(DISTINCT substr(created_at,1,10)) AS n FROM activity WHERE user_id=?", (user["id"],))["n"]}

    @app.get("/api/courses/{course_id}/sessions")
    def sessions(course_id: str, user=Depends(current_user)):
        course_access(db, course_id, user["id"])
        return [session_public(row) for row in db.all("SELECT * FROM tutor_sessions WHERE course_id=? AND user_id=? ORDER BY updated_at DESC", (course_id, user["id"]))]

    @app.post("/api/courses/{course_id}/sessions")
    def new_session(course_id: str, body: SessionBody, user=Depends(current_user)):
        course_access(db, course_id, user["id"])
        return session_public(create_tutor_session(db, course_id, user["id"], body.title))

    @app.get("/api/sessions/{session_id}/messages")
    def messages(session_id: str, user=Depends(current_user)):
        session_access(db, session_id, user["id"])
        rows = db.all("SELECT id,role,content,citations,trace,created_at,model FROM messages WHERE session_id=? ORDER BY created_at,rowid", (session_id,))
        for row in rows:
            row["citations"], row["trace"] = loads(row["citations"], []), loads(row["trace"], [])
            if row["role"] == "assistant":
                validation = next((item for item in reversed(row["trace"]) if item.get("node") == "citation_validation" and "grounded" in item and (item["grounded"] is None or type(item["grounded"]) is bool)), None)
                row["grounded"] = validation["grounded"] if validation is not None else bool(row["citations"])
        return rows

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, user=Depends(current_user)):
        session_access(db, session_id, user["id"])
        lock = app.state.session_locks.get(session_id)
        if lock and lock.locked():
            raise HTTPException(409, "对话正在生成回答，请等待完成")
        db.execute("DELETE FROM tutor_sessions WHERE id=? AND user_id=?", (session_id, user["id"]))
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(body: ChatBody, request: Request, user=Depends(current_user)):
        course = course_access(db, body.course_id, user["id"])
        if not body.message.strip():
            raise HTTPException(422, "问题不能为空")
        if body.session_id:
            session = session_access(db, body.session_id, user["id"])
            if session["course_id"] != body.course_id:
                raise HTTPException(422, "对话与课程不匹配")
        else:
            session = create_tutor_session(db, body.course_id, user["id"], body.message.strip()[:40])
        session_id = session["id"]
        lock = app.state.session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "当前对话正在生成，请等待完成")
        await lock.acquire()
        queue = asyncio.Queue(maxsize=256)

        turn_traces = []

        async def emit(event, payload):
            if event == "trace":
                turn_traces.append(payload)
            await queue.put((event, payload))

        async def produce():
            cancelled = False
            try:
                await emit("session", {"session_id": session_id})
                db.execute("INSERT INTO messages (id,session_id,role,content,created_at) VALUES (?,?,'user',?,?)", (uid(), session_id, body.message.strip(), now()))
                previous = db.all("SELECT role,content,citations FROM messages WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 16", (session_id,))[::-1]
                last_assistant = next((message for message in reversed(previous) if message["role"] == "assistant"), None)
                past_citations = loads(last_assistant["citations"], []) if last_assistant else []
                history = [{"role": message["role"], "content": message["content"], "citations": loads(message["citations"], [])} for message in previous]
                async with app.state.chat_semaphore:
                    try:
                        async with asyncio.timeout(settings.provider_timeout):
                            result = await app.state.agent.run(course, user["id"], history, body.mode, emit, past_citations=past_citations)
                    except Exception as failure:
                        if not settings.chat_recovery_enabled:
                            raise
                        result = await recover_chat(provider, course["title"], history, body.mode, failure, emit,
                                                    timeout_seconds=min(30.0, settings.provider_timeout))
                        # Preserve the actual attempted operations alongside the
                        # explicitly unverified continuation, including on refresh.
                        result["traces"] = turn_traces.copy()
                message_id, created = uid(), now()
                with db.connection() as conn:
                    conn.execute("INSERT INTO messages VALUES (?,?,'assistant',?,?,?,?,?)", (message_id, session_id, result["answer"], dumps(result["citations"]), dumps(result["traces"]), created, result["model"]))
                    conn.execute("UPDATE tutor_sessions SET updated_at=? WHERE id=?", (created, session_id))
                    db.activity(user["id"], course["id"], "chat", body.message.strip()[:60], conn)
                await emit("done", {"message_id": message_id, "session_id": session_id, "model": result["model"], "usage": result["usage"], "grounded": result["grounded"], "degraded": result.get("degraded", False), "final_text": result["answer"]})
            except asyncio.CancelledError:
                cancelled = True
                raise
            except ProviderError as exc:
                await emit("error", {"message": str(exc)})
            except Exception:
                await emit("error", {"message": "智能体执行失败，请稍后重试；本次没有保存 AI 回答"})
            finally:
                lock.release()
                if not cancelled:
                    await queue.put(None)

        async def stream_response():
            task = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=12)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if item is None:
                        break
                    event, payload = item
                    yield f"event: {event}\ndata: {dumps(payload)}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return ClosingStreamingResponse(stream_response(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/models")
    async def models(user=Depends(current_user)):
        return {"models": await provider.models(), "current": settings.model}

    @app.post("/api/courses/{course_id}/quizzes")
    async def quizzes(course_id: str, body: QuizBody, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        return await create_quiz(db, provider, retriever, course, user["id"], body.topic, body.difficulty, body.count)

    @app.post("/api/quizzes/{quiz_id}/submit")
    def submit(quiz_id: str, body: SubmitBody, user=Depends(current_user)):
        return submit_quiz(db, quiz_id, user["id"], body.answers)

    @app.get("/api/quizzes/{quiz_id}")
    def get_quiz(quiz_id: str, user=Depends(current_user)):
        return load_quiz(db, quiz_id, user["id"])

    @app.put("/api/quizzes/{quiz_id}/draft")
    def quiz_draft(quiz_id: str, body: QuizDraftBody, user=Depends(current_user)):
        return save_quiz_draft(db, quiz_id, user["id"], body.answers, body.expected_version)

    @app.get("/api/courses/{course_id}/practice")
    def practice(course_id: str, user=Depends(current_user)):
        course_access(db, course_id, user["id"])
        quizzes = db.all("SELECT id,title,created_at,submitted,result,questions FROM quizzes WHERE course_id=? AND user_id=? ORDER BY created_at DESC", (course_id, user["id"]))
        for quiz in quizzes:
            result = loads(quiz.pop("result"), {})
            quiz["score"], quiz["total"] = result.get("score"), len(loads(quiz.pop("questions")))
            quiz["submitted"] = bool(quiz["submitted"])
        mistakes = db.all("SELECT id,concept,stem,user_answer,correct_answer,explanation,created_at,resolved FROM mistakes WHERE course_id=? AND user_id=? ORDER BY created_at DESC", (course_id, user["id"]))
        for mistake in mistakes:
            mistake["resolved"] = bool(mistake["resolved"])
        return {"quizzes": quizzes, "mistakes": mistakes}

    @app.post("/api/mistakes/{mistake_id}/resolve")
    def resolve_mistake(mistake_id: str, user=Depends(current_user)):
        if not db.execute("UPDATE mistakes SET resolved=1 WHERE id=? AND user_id=?", (mistake_id, user["id"])):
            raise HTTPException(404, "错题不存在")
        return {"ok": True}

    @app.post("/api/courses/{course_id}/plans")
    async def plans(course_id: str, body: PlanBody, user=Depends(current_user)):
        course = course_access(db, course_id, user["id"])
        labels = [row["label"] for row in db.all("SELECT label FROM concepts WHERE course_id=?", (course_id,))]
        goal_concepts, notice = await match_plan_goals(retriever, course_id, user["id"], body.goal, labels)
        return make_plan(db, course, user["id"], body.days, body.daily_minutes, body.goal, goal_concepts, notice)

    @app.get("/api/courses/{course_id}/plans")
    def list_plans(course_id: str, user=Depends(current_user)):
        course_access(db, course_id, user["id"])
        return [loads(row["data"]) for row in db.all("SELECT data FROM plans WHERE course_id=? AND user_id=? ORDER BY created_at DESC", (course_id, user["id"]))]

    @app.patch("/api/plans/{plan_id}/items/{item_id}")
    def complete_item(plan_id: str, item_id: str, body: CompleteBody, user=Depends(current_user)):
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM plans WHERE id=? AND user_id=?", (plan_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "复习计划不存在")
            plan = loads(row["data"])
            item = next((item for item in plan["items"] if item["id"] == item_id), None)
            if item is None:
                raise HTTPException(404, "复习任务不存在")
            was = item["completed"]
            item["completed"] = body.completed
            conn.execute("UPDATE plans SET data=? WHERE id=?", (dumps(plan), plan_id))
            if body.completed and not was:
                db.activity(user["id"], row["course_id"], "review", f"完成复习：{item['title']}", conn)
        return plan

    @app.get("/api/export")
    def export(course_id: str | None = None, user=Depends(current_user)):
        if course_id is None:
            rows = db.all("SELECT id,title FROM courses WHERE is_builtin=1 OR user_id=? ORDER BY is_builtin DESC,created_at", (user["id"],))
            sections = [f"# {user['display_name']} · 全部学习档案\n导出时间：{now()}"]
            for row in rows:
                # Reuse the same ownership-filtered export; no server credentials or unsubmitted answers.
                response = export(row["id"], user)
                sections.append(response.body.decode("utf-8"))
            return Response("\n\n---\n\n".join(sections), media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename*=UTF-8''study-archive.md"})
        course = course_access(db, course_id, user["id"])
        lines = [f"# {course['title']} · 学习档案", f"\n学生：{user['display_name']}\n导出时间：{now()}\n"]
        for session in db.all("SELECT * FROM tutor_sessions WHERE course_id=? AND user_id=? ORDER BY created_at", (course_id, user["id"])):
            lines.append(f"\n## 对话：{session['title']}")
            for message in db.all("SELECT * FROM messages WHERE session_id=? ORDER BY created_at,rowid", (session["id"],)):
                lines.append(f"\n### {'学生' if message['role'] == 'user' else '助教'}\n{message['content']}")
                for citation in loads(message["citations"], []):
                    location = citation.get("location_label") or with_location(citation)["location_label"]
                    lines.append(f"\n[{citation['id']}] {citation['title']} · {location} · {citation['section']}\n> {citation['excerpt'][:450]}")
        lines.append("\n## 练习记录")
        for quiz in db.all("SELECT * FROM quizzes WHERE course_id=? AND user_id=? ORDER BY created_at", (course_id, user["id"])):
            result = loads(quiz["result"], {})
            lines.append(f"\n- {quiz['title']}：" + (f"得分 {result['score']}，{result['correct']}/{result['total']}" if quiz["submitted"] else "尚未提交"))
            if quiz["submitted"]:
                questions = {question["id"]: question for question in loads(quiz["questions"], [])}
                for q in result["results"]:
                    question = questions.get(q["question_id"])
                    if question is not None:
                        lines.append(f"  - 题目：{question['stem']}")
                        lines.extend(f"    - {option['key']}. {option['text']}" for option in question["options"])
                    lines.append(f"  - {q['concept']}：{'正确' if q['correct'] else '错误'}；作答 {q['user_answer']}；正确 {q['correct_answer']}；{q['explanation']}")
        lines.append("\n## 复习计划")
        for row in db.all("SELECT data FROM plans WHERE course_id=? AND user_id=? ORDER BY created_at", (course_id, user["id"])):
            plan = loads(row["data"])
            lines.append(f"\n### {plan['goal']}\n{plan['basis']}")
            lines.extend(f"- [{'x' if item['completed'] else ' '}] 第 {item['day']} 天 · {item['title']} · {item['minutes']} 分钟" for item in plan["items"])
        filename = quote(f"{course['title']}-学习档案.md")
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})

    # A built frontend makes the server a single-port, same-origin demo deployment.
    dist = settings.root / "frontend" / "dist"
    if (dist / "index.html").is_file():
        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            target = (dist / path).resolve()
            if not target.is_relative_to(dist.resolve()):
                raise HTTPException(404, "页面不存在")
            return FileResponse(target if target.is_file() else dist / "index.html")
    return app


app = create_app()
