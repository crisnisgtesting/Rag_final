"""Application configuration.

All runtime configuration is environment based. Production secrets are never
persisted to the database and never returned through the API.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / "config" / ".env"

VisionProviderName = Literal["qwen", "gemini", "databricks"]
AppEnvironment = Literal["development", "test", "production"]

_WEAK_JWT_SECRETS = {
    "change-me-generate-a-long-random-value",
    "change-me",
    "secret",
    "dev",
}


def _empty_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnvironment = "development"
    app_name: str = "FMM Knowledge and Operational Assistant"
    log_level: str = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    ui_host: str = "127.0.0.1"
    ui_port: int = 8501
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8501", "http://127.0.0.1:8501"]
    )
    max_upload_mb: int = 200

    database_url: str = "postgresql://postgres@127.0.0.1:55432/fmm_dev"

    jwt_secret: SecretStr = SecretStr("dev-only-insecure-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    auth_max_login_attempts: int = 8
    auth_login_window_seconds: int = 300

    # Vision provider selection (database override applies to new jobs only)
    vision_provider: VisionProviderName = "qwen"

    qwen_vision_base_url: str = "http://127.0.0.1:8001/v1"
    qwen_vision_model: str = "Qwen3-VL-8B-Instruct"
    qwen_vision_api_key: SecretStr | None = None

    gemini_api_key: SecretStr | None = None
    gemini_vision_model: str | None = None
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com"

    databricks_host: str | None = None
    databricks_token: SecretStr | None = None
    databricks_vision_endpoint: str | None = None
    databricks_vision_payload_style: Literal["openai_chat"] = "openai_chat"

    vision_timeout_seconds: float = 120.0
    vision_max_retries: int = 3
    vision_max_output_tokens: int = 4096
    vision_concurrency_qwen: int = 2
    vision_concurrency_gemini: int = 2
    vision_concurrency_databricks: int = 2

    model_dir: Path = Path("./models")
    data_dir: Path = Path("./data")
    log_dir: Path = Path("./logs")
    hf_hub_offline: int = 0

    # Phase 2 - ingestion
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    embedding_max_seq_length: int = 1024
    ingestion_render_dpi: int = 200
    ingestion_vision_mode: Literal["auto", "all", "none"] = "auto"
    ingestion_vision_min_image_coverage: float = 0.015
    ingestion_vision_low_text_chars: int = 120
    ingestion_chunk_max_chars: int = 3200
    ingestion_chunk_overlap_chars: int = 400
    ingestion_page_batch_size: int = 8

    # Phase 2 - optional OCR for scanned pages. OCR supplements the vision
    # analysis and never replaces it. It is disabled by default because the
    # engine is an optional dependency.
    ingestion_ocr_enabled: bool = False
    ingestion_ocr_backend: Literal["auto", "paddleocr"] = "auto"
    ingestion_ocr_language: str = "en"
    ingestion_ocr_use_gpu: bool = False

    # Phase 2 - retrieval
    retrieval_dense_candidates: int = 50
    retrieval_lexical_candidates: int = 50
    retrieval_top_k: int = 8
    retrieval_rrf_k: int = 60
    retrieval_max_context_chars: int = 16000

    # Phase 4 - knowledge-aware retrieval and optional cross-encoder reranking
    retrieval_knowledge_boost: bool = True
    retrieval_rerank_candidates: int = 30
    retrieval_reranker_enabled: bool = False
    retrieval_reranker_model: str = "BAAI/bge-reranker-v2-m3"
    retrieval_reranker_max_length: int = 512

    # Phase 2 - grounded chat
    chat_provider: Literal["gemini", "qwen", "databricks"] = "gemini"
    gemini_chat_model: str | None = None
    chat_temperature: float = 0.2
    chat_max_output_tokens: int = 2048
    chat_timeout_seconds: float = 120.0
    chat_max_history_messages: int = 12

    # Phase 6 - voice input and evaluation
    transcription_provider: Literal["local", "disabled"] = "local"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5
    max_audio_mb: int = 25

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "qwen_vision_api_key",
        "gemini_api_key",
        "databricks_token",
        "gemini_vision_model",
        "gemini_chat_model",
        "databricks_host",
        "databricks_vision_endpoint",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return _empty_to_none(value)

    @field_validator("model_dir", "data_dir", "log_dir", mode="after")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (BASE_DIR / value).resolve()

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        if self.app_env == "production":
            secret = self.jwt_secret.get_secret_value()
            if len(secret) < 32 or secret in _WEAK_JWT_SECRETS:
                raise ValueError(
                    "JWT_SECRET must be a strong random value (>= 32 chars) in production"
                )
            if not self.database_url:
                raise ValueError("DATABASE_URL is required in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def ensure_runtime_dirs(self) -> None:
        for directory in (
            self.model_dir,
            self.data_dir,
            self.log_dir,
            self.data_dir / "documents",
            self.data_dir / "document_versions",
            self.data_dir / "rendered_pages",
            self.data_dir / "temporary",
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def effective_chat_model(self) -> str:
        return (self.gemini_chat_model or self.gemini_vision_model or "").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
