"""환경 설정.

값의 출처는 `.env` 하나뿐이다. 코드에 기본값을 두는 것은 개발 편의를 위한
비민감 항목(모델명·경로·임계값)에 한하고, 키는 기본값 없이 필수로 둔다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", description="비어 있으면 생성 기능이 비활성화된다")
    llm_model: str = "claude-sonnet-5"
    # 장애·거절 시 위로 올린다(성공률 > 비용). ADR-002 참조
    llm_model_fallback: str = "claude-opus-5"
    llm_model_small: str = "claude-haiku-4-5"
    # sonnet-5 의 기본 effort 는 high 라 그대로 두면 지연 예산(§14.1)을 넘긴다.
    # low 는 numbers_used 나열이 누락될 위험이 있어 medium 에서 시작한다.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_tokens: int = 4096
    llm_max_retries: int = 2

    # ── 임베딩 ───────────────────────────────────────────────────────
    # 문서 임베딩은 빌드 타임, 런타임은 쿼리 1건만. 확정은 Phase 7 실측 후(ADR-003)
    embed_model: str = "BAAI/bge-m3"

    # ── 인덱스 ───────────────────────────────────────────────────────
    chroma_path: Path = REPO_ROOT / "apps" / "api" / "data" / "chroma"
    bm25_index_path: Path = REPO_ROOT / "apps" / "api" / "data" / "bm25.pkl"
    chroma_collection: str = "kbuddy"

    # ── 검색 파라미터 (planner §6.3, §6.6) ───────────────────────────
    retrieve_top_k: int = 20  # dense / lexical 각각의 후보 수
    context_top_n: int = 5  # 생성에 넣을 최종 근거 수
    rrf_k: int = 60
    rrf_w_lexical: float = 1.0  # 3주 차 그리드 튜닝 대상
    threshold_top1: float = 0.42
    threshold_margin: float = 0.05
    stale_days: int = 90  # verified_at 이 이보다 오래되면 시점 경고

    # ── 외부 OpenAPI (M1 범위 밖) ────────────────────────────────────
    fss_api_key: str = ""
    ecos_api_key: str = ""

    # ── 운영 ─────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_min: int = 20
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """CORS_ORIGINS=a,b 형태의 쉼표 구분 문자열을 허용한다."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
