"""Central configuration. Everything is driven by environment variables so the
evaluator can switch model/provider without touching code (see .env.example)."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["ollama", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://lenny:lenny@localhost:5432/lenny"
    db_connect_retries: int = 20
    db_connect_retry_delay_s: float = 1.5

    # --- LLM provider selection --------------------------------------------
    llm_provider: ProviderName = "ollama"  # default provider for new sessions
    llm_fallback_provider: ProviderName | None = None  # used if primary is down
    llm_timeout_s: float = 180.0  # total wall-clock budget per LLM call
    llm_idle_timeout_s: float = 90.0  # max silence between streamed chunks

    # Local (Ollama, via its Anthropic-compatible endpoint)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Cloud (Anthropic)
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str | None = None  # any Anthropic-compatible gateway

    # --- Knowledge base -----------------------------------------------------
    transcripts_dir: str = "./data/transcripts"
    transcripts_repo_tarball: str = (
        "https://codeload.github.com/ChatPRD/lennys-podcast-transcripts/tar.gz/refs/heads/main"
    )
    auto_ingest: bool = True  # ingest on startup if the chunks table is empty
    chunk_target_words: int = 320
    chunk_overlap_turns: int = 1
    retrieval_top_k: int = 8
    retrieval_min_score: float = 4.0  # BM25 floor below which we refuse to answer
    retrieval_min_coverage: float = 0.45  # share of (idf-weighted) query terms the best chunk must contain
    max_chunks_per_episode: int = 3

    source_char_budget_local: int = 11000  # prompt budget for retrieved text on small local models
    source_char_budget_cloud: int = 32000
    essay_source_count: int = 10

    # --- Conversation -------------------------------------------------------
    history_max_messages: int = 8
    history_max_chars: int = 6000
    essay_target_words: int = 1250

    # --- Server -------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator("llm_fallback_provider", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if v in ("", "none", "None") else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
