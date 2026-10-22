import asyncio
import json
from pathlib import Path

import pytest

from backend.app.db import Database, dumps, loads, now
from backend.app.ingestion import IngestionWorker, extract_pages, normalize_text, pdf_sections, usable_outline
from backend.app.config import Settings
from backend.app.provider import ProviderError


def test_cjk_nfkc_preserves_math_english_and_heading_boundaries():
    raw = "第 二 章  进 程 管 理\n⽤户需维护进程表\n并按PID找到进程\nx² + CO₂ = 1/2\nEnglish words stay separate"
    cleaned = normalize_text(raw, pdf=True)
    assert cleaned.startswith("第二章进程管理\n")
    assert "用户需维护进程表并按PID找到进程" in cleaned
    assert "x² + CO₂ = 1/2" in cleaned
    assert "English words stay separate" in cleaned
    assert "⽤" not in cleaned


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class FakeReader:
    is_encrypted = False
    outline = [{"/Title": "text00000", "/Page": 0}, {"/Title": "text00001", "/Page": 2}]

    def __init__(self, path):
        self.pages = [FakePage("第 二 章 进 程 管 理\n2.1 进程的概念\n⽤户进程与系统进程。"), FakePage(""), FakePage("2.2 进程控制\n⼈们使⽤操作系统创建进程。")]

    def get_destination_page_number(self, destination):
        return destination["/Page"]


def test_pdf_physical_pages_quality_progress_and_fake_outline(monkeypatch, tmp_path):
    from backend.app import ingestion
    monkeypatch.setattr(ingestion, "PdfReader", FakeReader)
    updates = []
    pages = extract_pages(tmp_path / "book.pdf", updates.append)
    assert [page for page, _ in pages] == [1, 2, 3]
    assert pages.quality["blank_pages"] == 1 and pages.quality["text_pages"] == 2
    assert pages.quality["outline_entries"] == 0
    assert "进程管理" in pages.sections[3] and "进程控制" in pages.sections[3]
    assert "人们使用" in pages[2][1]
    assert updates[0]["total_pages"] == 3 and updates[-1]["current_page"] == 3
    assert any("图表" in warning for warning in pages.quality["warnings"])


def test_real_outline_parent_sections_preserve_destinations():
    reader = FakeReader(None)
    reader.outline = [{"/Title": "第一章引论", "/Page": 0}, [{"/Title": "1.1 操作系统目标", "/Page": 1}]]
    outline = usable_outline(reader)
    assert outline == [(1, "第一章引论"), (2, "第一章引论 / 1.1 操作系统目标")]
    sections = pdf_sections([(1, "a"), (2, "b"), (3, "c")], outline)
    assert sections[3] == "第一章引论 / 1.1 操作系统目标"


class BatchProvider:
    def __init__(self, fail_on_call=None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    async def embed(self, texts):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise ProviderError("模型服务凭证失效或无访问权限（HTTP 401）")
        return [[1.0, .2, .3] for _ in texts]


def private_fixture(tmp_path, count=40):
    settings = Settings(root=tmp_path, api_key="test-only", model="test", embedding_enabled=True)
    db = Database(settings.database)
    stamp = now()
    db.execute("INSERT INTO users VALUES ('u','u','u','unused',?)", (stamp,))
    db.execute("INSERT INTO courses VALUES ('c','u','c','','c','#557766','layers','a',0,?)", (stamp,))
    path = tmp_path / "book.md"
    path.write_text("\n".join(f"## 第{i}节\n进程调度与地址转换内容{i}。" for i in range(count)), encoding="utf-8")
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at) VALUES ('d','c','u','x','x.md',?,'queued','uploaded_md','own',?)", (str(path), stamp))
    db.execute("INSERT INTO ingestion_jobs VALUES ('j','d','queued',0,NULL,?,?)", (stamp, stamp))
    return settings, db


def test_embedding_failure_is_visible_and_retry_resumes_missing_batches(tmp_path):
    settings, db = private_fixture(tmp_path)
    first = BatchProvider(fail_on_call=2)
    worker = IngestionWorker(db, first, settings)
    asyncio.run(worker.process(db.one("SELECT * FROM documents WHERE id='d'")))
    doc = db.one("SELECT * FROM documents WHERE id='d'")
    assert doc["status"] == "ready" and doc["embedding_status"] == "lexical_fallback"
    quality, progress = loads(doc["quality"]), loads(doc["progress"])
    assert "401" in quality["semantic_error"]
    assert progress["embedding_completed"] == 32 and 0 < progress["embedding_percent"] < 100
    assert db.one("SELECT COUNT(*) AS n FROM chunks WHERE document_id='d' AND embedding IS NOT NULL")["n"] == 32
    second = BatchProvider()
    asyncio.run(IngestionWorker(db, second, settings).process(doc))
    updated = db.one("SELECT * FROM documents WHERE id='d'")
    assert second.calls == 1  # Saved first batch is not paid for again.
    assert updated["embedding_status"] == "hybrid"
    assert loads(updated["progress"])["embedding_percent"] == 100
    assert "semantic_error" not in loads(updated["quality"])
    assert not any(warning.startswith("语义向量尚未完整生成") for warning in loads(updated["quality"])["warnings"])


def test_processing_job_resumes_checkpointed_vectors_after_restart(tmp_path):
    settings, db = private_fixture(tmp_path)
    first = BatchProvider(fail_on_call=2)
    asyncio.run(IngestionWorker(db, first, settings).process(db.one("SELECT * FROM documents WHERE id='d'")))
    doc = db.one("SELECT * FROM documents WHERE id='d'")
    progress = loads(doc["progress"])
    progress["stage"] = "embedding"
    db.execute("UPDATE documents SET status='processing',progress=? WHERE id='d'", (dumps(progress),))
    db.execute("UPDATE ingestion_jobs SET status='processing' WHERE id='j'")
    provider = BatchProvider()

    async def resume():
        worker = IngestionWorker(db, provider, settings)
        await worker.start()
        for _ in range(100):
            if db.one("SELECT status FROM documents WHERE id='d'")["status"] == "ready":
                break
            await asyncio.sleep(.025)
        await worker.stop()
    asyncio.run(resume())
    assert provider.calls == 1
    assert db.one("SELECT embedding_status FROM documents WHERE id='d'")["embedding_status"] == "hybrid"


def test_graph_hierarchy_samples_all_chapters_and_normalizes_mastery_labels(tmp_path):
    from backend.app.ingestion import rebuild_extracted_graph
    _, db = private_fixture(tmp_path)
    db.execute("UPDATE documents SET status='ready',source_type='uploaded_pdf' WHERE id='d'")
    with db.connection() as conn:
        for chapter in range(1, 11):
            for section in range(1, 21):
                conn.execute("INSERT INTO chunks VALUES (?,'d','c','u',?,?,?,NULL)", (
                    f"chunk-{chapter}-{section}", (chapter - 1) * 20 + section,
                    f"第{chapter}章 主题{chapter} / {chapter}.{section} 小节{chapter}-{section}", "本节真实示例文字"))
        rebuild_extracted_graph(conn, "c")
    nodes = db.all("SELECT * FROM concepts WHERE course_id='c'")
    assert len(nodes) == 180
    for chapter in range(1, 11):
        assert any(node["label"] == f"主题{chapter}" for node in nodes)
        assert any(node["label"].startswith(f"小节{chapter}-") for node in nodes)
    assert all("物理页" in node["source"] and "待人工核验" in node["source"] for node in nodes)
    assert db.one("SELECT COUNT(*) AS n FROM edges WHERE relation='章节包含'")["n"] == 170
    assert db.one("SELECT COUNT(*) AS n FROM edges WHERE relation='教材相邻章节'")["n"] == 9
