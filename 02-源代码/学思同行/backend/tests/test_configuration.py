import asyncio

import pytest

from backend.app.config import Settings
from backend.tests.test_ingestion_quality import BatchProvider, private_fixture
from backend.app.ingestion import IngestionWorker
from backend.app.db import loads
from backend.app.retrieval import Retriever


@pytest.mark.parametrize("model", ["", "   "])
def test_blank_embedding_model_disables_semantic_calls_and_uses_lexical_ingestion(tmp_path, model):
    settings, db = private_fixture(tmp_path, count=3)
    settings = Settings(root=tmp_path, api_key="test-only", model="test-chat", embedding_enabled=True, embedding_model=model)
    assert settings.embedding_enabled is False and settings.embedding_model == ""
    provider = BatchProvider()
    asyncio.run(IngestionWorker(db, provider, settings).process(db.one("SELECT * FROM documents WHERE id='d'")))
    doc = db.one("SELECT * FROM documents WHERE id='d'")
    assert provider.calls == 0 and doc["status"] == "ready" and doc["embedding_status"] == "lexical"
    assert "semantic_error" not in loads(doc["quality"])


def test_environment_blank_embedding_model_matches_public_bm25_configuration(monkeypatch, tmp_path):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    from backend.tests.test_api import FakeProvider

    monkeypatch.setenv("EMBEDDING_MODEL", "")
    monkeypatch.setenv("EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUTOR_DATABASE", str(tmp_path / "data" / "test.sqlite3"))
    settings = Settings.from_env()
    settings.seed_file = tmp_path / "nonexistent-catalog.json"
    assert settings.embedding_enabled is False
    with TestClient(create_app(settings, FakeProvider())) as client:
        assert client.get("/api/config/public").json()["embedding_model"] == "disabled / BM25"


def test_provider_neutral_llm_environment_selects_any_openai_compatible_service(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "https://provider.example/v1/")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("LLM_MODEL", "vendor/custom-tool-model")
    monkeypatch.setenv("LLM_MIN_OUTPUT_TOKENS", "6144")
    settings = Settings.from_env()
    assert settings.api_base == "https://provider.example/v1"
    assert settings.api_key == "test-provider-key"
    assert settings.model == "vendor/custom-tool-model"
    assert settings.min_output_tokens == 6144


def test_vision_defaults_to_main_model_and_embedding_can_keep_a_separate_provider(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "mimo-v2.6-flash")
    monkeypatch.setenv("VISION_MODEL", "")
    monkeypatch.setenv("EMBEDDING_API_BASE", "https://vectors.example/v1/")
    monkeypatch.setenv("EMBEDDING_API_KEY", "vector-secret")
    settings = Settings.from_env()
    assert settings.vision_model == "mimo-v2.6-flash"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.embedding_api_base == "https://vectors.example/v1"
    assert settings.embedding_api_key == "vector-secret"


@pytest.mark.parametrize("kwargs", [
    {"embedding_api_base": "https://vectors.example/v1"},
    {"embedding_api_key": "vector-secret"},
])
def test_embedding_endpoint_and_key_must_be_configured_together(kwargs):
    with pytest.raises(ValueError, match="EMBEDDING_API_KEY and EMBEDDING_API_BASE"):
        Settings(**kwargs)


@pytest.mark.parametrize("budget", [-1, 32769, 1.5, True])
def test_invalid_generation_budget_fails_before_upstream_calls(budget):
    with pytest.raises(ValueError, match="LLM_MIN_OUTPUT_TOKENS"):
        Settings(min_output_tokens=budget)


def test_semantic_disabled_skips_query_embedding_even_for_existing_hybrid_document(tmp_path):
    original_settings, db = private_fixture(tmp_path, count=3)
    provider = BatchProvider()
    asyncio.run(IngestionWorker(db, provider, original_settings).process(db.one("SELECT * FROM documents WHERE id='d'")))
    assert provider.calls == 1
    provider.settings = Settings(root=tmp_path, api_key="test-only", model="test", embedding_model="")
    result = asyncio.run(Retriever(db, provider).search("c", "u", "地址转换"))
    assert result["items"] and result["method"] == "BM25 + 中文分词"
    assert "已关闭" in result["notice"] and provider.calls == 1
