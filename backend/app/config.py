"""Server-only configuration. API credentials never enter response objects."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


@dataclass
class Settings:
    root: Path = ROOT
    data_dir: Path | None = None
    database: Path | None = None
    api_key: str = ""
    api_base: str = "https://api.xiaomimimo.com/v1"
    model: str = ""
    quiz_model: str = ""
    quiz_thinking: bool = False
    quiz_thinking_budget: int = 1024
    review_model: str = ""
    review_thinking: bool = False
    review_thinking_budget: int = 1024
    embedding_model: str = "BAAI/bge-m3"
    embedding_enabled: bool = True
    embedding_api_key: str = ""
    embedding_api_base: str = ""
    vision_model: str = ""
    vision_enabled: bool = True
    cookie_secure: bool = False
    session_days: int = 14
    max_agent_rounds: int = 4
    seed_file: Path | None = None
    max_upload_bytes: int = 200 * 1024 * 1024
    provider_timeout: float = 90
    min_output_tokens: int = 0
    json_mode_enabled: bool = True
    chat_recovery_enabled: bool = True

    def __post_init__(self):
        self.data_dir = Path(self.data_dir or self.root / "data")
        self.database = Path(self.database or self.data_dir / "tutor.sqlite3")
        self.seed_file = Path(self.seed_file or self.root / "content" / "catalog.json")
        self.quiz_model = self.quiz_model.strip()
        self.embedding_model = self.embedding_model.strip()
        self.embedding_api_key = self.embedding_api_key.strip()
        self.embedding_api_base = self.embedding_api_base.rstrip("/")
        if bool(self.embedding_api_key) != bool(self.embedding_api_base):
            raise ValueError("EMBEDDING_API_KEY and EMBEDDING_API_BASE must be set together")
        self.vision_model = self.vision_model.strip() or self.model
        if not self.embedding_model:
            self.embedding_enabled = False
        if type(self.min_output_tokens) is not int or not 0 <= self.min_output_tokens <= 32768:
            raise ValueError("LLM_MIN_OUTPUT_TOKENS must be an integer between 0 and 32768")
        if type(self.review_thinking_budget) is not int or not 128 <= self.review_thinking_budget <= 32768:
            raise ValueError("LLM_REVIEW_THINKING_BUDGET must be an integer between 128 and 32768")
        if type(self.quiz_thinking_budget) is not int or not 128 <= self.quiz_thinking_budget <= 32768:
            raise ValueError("LLM_QUIZ_THINKING_BUDGET must be an integer between 128 and 32768")
        if type(self.max_upload_bytes) is not int or not 1 <= self.max_upload_bytes <= 512 * 1024 * 1024:
            raise ValueError("max_upload_bytes must be an integer between 1 byte and 512 MiB")

    @classmethod
    def from_env(cls):
        read_dotenv(ROOT / ".env")
        try:
            upload_mb = int(os.getenv("MAX_UPLOAD_MB", "200"))
        except ValueError:
            raise ValueError("MAX_UPLOAD_MB must be an integer between 1 and 512") from None
        if not 1 <= upload_mb <= 512:
            raise ValueError("MAX_UPLOAD_MB must be an integer between 1 and 512")
        try:
            quiz_thinking_budget = int(os.getenv("LLM_QUIZ_THINKING_BUDGET", "1024"))
        except ValueError:
            raise ValueError("LLM_QUIZ_THINKING_BUDGET must be an integer between 128 and 32768") from None
        return cls(
            data_dir=Path(os.getenv("TUTOR_DATA_DIR", str(ROOT / "data"))),
            database=Path(os.environ["TUTOR_DATABASE"]) if os.getenv("TUTOR_DATABASE") else None,
            api_key=os.getenv("LLM_API_KEY", ""),
            api_base=os.getenv("LLM_API_BASE", "https://api.xiaomimimo.com/v1").rstrip("/"),
            model=os.getenv("LLM_MODEL", ""),
            quiz_model=os.getenv("LLM_QUIZ_MODEL", "").strip(),
            quiz_thinking=os.getenv("LLM_QUIZ_THINKING", "false").strip().lower() in {"1", "true", "yes"},
            quiz_thinking_budget=quiz_thinking_budget,
            review_model=os.getenv("LLM_REVIEW_MODEL", "").strip(),
            review_thinking=os.getenv("LLM_REVIEW_THINKING", "false").lower() in {"1", "true", "yes"},
            review_thinking_budget=int(os.getenv("LLM_REVIEW_THINKING_BUDGET", "1024")),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_enabled=os.getenv("EMBEDDING_ENABLED", "true").lower() in {"1", "true", "yes"},
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_api_base=os.getenv("EMBEDDING_API_BASE", ""),
            vision_model=os.getenv("VISION_MODEL", ""),
            vision_enabled=os.getenv("VISION_ENABLED", "true").lower() in {"1", "true", "yes"},
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            provider_timeout=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "90")),
            min_output_tokens=int(os.getenv("LLM_MIN_OUTPUT_TOKENS", "0")),
            json_mode_enabled=os.getenv("LLM_JSON_MODE_ENABLED", "true").lower() in {"1", "true", "yes"},
            max_upload_bytes=upload_mb * 1024 * 1024,
        )

