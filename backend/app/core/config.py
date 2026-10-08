"""Central settings. All secrets come from environment (spec §38.15-16)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./dev.db"
    JWT_SECRET: str = "dev-only-secret-change-in-prod-min-32-chars!!"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    STORAGE_BACKEND: str = "local"  # local | minio (S3-compatible)
    STORAGE_DIR: str = "./storage"
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_BUCKET: str = "contracts"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_REGION: str = "us-east-1"

    REDIS_URL: str = "redis://localhost:6379/0"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "semantic_objects"

    # Phase 6: semantic retrieval. Model is configurable; thresholds below
    # are engineering defaults requiring evaluation — similarity is NOT
    # legal equivalence (see retrieval/semantic_matcher.py).
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_VERSION: str = "minilm-l6-v2"
    SEMANTIC_MATCH_THRESHOLD: float = 0.55
    SEMANTIC_STRONG_THRESHOLD: float = 0.80

    # Part 2: contract DISCOVERY search (filename / fuzzy / as-you-type).
    # Elasticsearch is a SEARCH COPY only — PostgreSQL stays the source of
    # truth for every contract record (see app/modules/search/). Kept
    # deliberately separate from the Qdrant settings above: Qdrant answers
    # "which clauses mean something similar", Elasticsearch answers "which
    # contract/file am I looking for".
    # Empty URL = not configured: /api/v1/search/* fails clearly with 503
    # instead of silently falling back to a different search algorithm.
    ELASTICSEARCH_URL: str = ""
    # Alias used by every query; it points at the versioned physical index
    # contract_search_v1 (see app/modules/search/index.py).
    ELASTICSEARCH_INDEX: str = "contract_search"
    # One bounded timeout for every ES call: a hung cluster can never hang a
    # request or an indexing hook indefinitely.
    ELASTICSEARCH_TIMEOUT_S: float = 2.0
    # Default/max result sizes for GET /api/v1/search/contracts.
    ELASTICSEARCH_SEARCH_LIMIT: int = 20
    ELASTICSEARCH_SEARCH_MAX_LIMIT: int = 100

    # LLM: configurable OpenAI-compatible provider (Phase 7). Never hard-coded.
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "openai-compatible"  # openai-compatible | fake (tests only)
    LLM_TIMEOUT_S: int = 90
    EXPLANATION_MAX_CHARS: int = 6000  # documented in llm_explanation/context.py
    EXPLANATION_HISTORY_TOP_K: int = 3

    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"
    MAX_UPLOAD_MB: int = 50

    ALLOWED_UPLOAD_EXTENSIONS: tuple = (".pdf", ".docx")
    # Magic bytes: PDF %PDF, DOCX PK\x03\x04 (zip container)
    ALLOWED_UPLOAD_MAGIC: tuple = (b"%PDF", b"PK\x03\x04")

    # Phase 3: clause classification. Base model is configurable, never hard-coded
    # into model code (train.py / classifier.py take it as a parameter).
    MODEL_NAME: str = "nlpaueb/legal-bert-base-uncased"
    MODEL_DIR: str = "./ml_artifacts/clause_classifier_v1"
    MLFLOW_TRACKING_URI: str = "./mlruns"


@lru_cache
def get_settings() -> Settings:
    return Settings()
