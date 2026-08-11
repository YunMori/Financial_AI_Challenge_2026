"""환경 설정.

값의 출처는 `.env` 하나뿐이다. 코드에 기본값을 두는 것은 개발 편의를 위한
비민감 항목(모델명·경로·임계값)에 한하고, 키는 기본값 없이 필수로 둔다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
# `app/` 를 담고 있는 디렉터리. 로컬은 `apps/api`, Docker 는 `/app` 이다.
# 인덱스 같은 런타임 자산은 이 기준으로 푼다 — 그래야 실행 위치와 무관하게 맞다.
API_ROOT = Path(__file__).resolve().parents[1]


def _resolve(path: Path, base: Path) -> Path:
    """상대 경로를 `base` 기준으로 푼다.

    `.env` 에 `data/chroma` 라고 쓰면 실행 위치(CWD)에 따라 다른 곳을 가리킨다.
    uvicorn 을 `apps/api` 에서 띄우든 리포 루트에서 띄우든 같은 파일을 봐야 한다.
    """
    return path if path.is_absolute() else (base / path).resolve()


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
    # **모델명의 단일 출처.** 색인기(04_index)와 런타임이 같은 값을 봐야 한다.
    # 계획은 BGE-m3 를 상정했으나 fastembed 가 지원하지 않아 e5-large 로 진행한다
    # (BGE-m3 는 EMBED_BACKEND=sentence_transformers 필요 — torch).
    # 확정은 Phase 7 컨테이너 메모리 실측 후 ADR-003.
    embed_model: str = "intfloat/multilingual-e5-large"

    # ── 인덱스 ───────────────────────────────────────────────────────
    chroma_path: Path = API_ROOT / "data" / "chroma"
    bm25_index_path: Path = API_ROOT / "data" / "bm25.pkl"
    chroma_collection: str = "kbuddy"

    # ── 검색 파라미터 (planner §6.3, §6.6) ───────────────────────────
    retrieve_top_k: int = 20  # dense / lexical 각각의 후보 수
    context_top_n: int = 5  # 생성에 넣을 최종 근거 수
    rrf_k: int = 60
    rrf_w_lexical: float = 1.0  # 3주 차 그리드 튜닝 대상
    # 검색 신뢰도 임계값 (planner §6.6).
    #
    # planner 원안의 0.42 는 다른 점수 척도를 가정한 값이다. e5 의 코사인
    # 유사도는 0.76~0.91 구간에 몰리므로 0.42 로는 폴백이 **전혀** 걸리지 않는다.
    # `python -m app.rag.cli --calibrate` 실측(2026-08-12, 코퍼스 안 8 / 밖 8):
    #   코퍼스 안  최소 0.8494 / 평균 0.8864
    #   코퍼스 밖  최대 0.8293 / 평균 0.7960
    # 두 분포의 중간값을 잠정 채택한다. **분리 폭이 0.02 로 좁으므로
    # 골든셋(함정 40문항)이 생기면 반드시 다시 잡아야 한다.**
    threshold_top1: float = 0.839
    #
    # margin(1위와 3위의 RRF 차)은 **신뢰도 신호로 쓰지 않는다.** 실측에서
    # 코퍼스 밖 질의가 오히려 높은 margin 을 냈다 — 두 검색기가 같은 문서를
    # 고르면 커지는 값이라 "근거가 존재하는가"와 무관하기 때문이다.
    # 0 은 비활성. 대체 신호는 골든셋과 함께 설계한다.
    threshold_margin: float = 0.0
    stale_days: int = 90  # verified_at 이 이보다 오래되면 시점 경고

    # ── 외부 OpenAPI (M1 범위 밖) ────────────────────────────────────
    fss_api_key: str = ""
    ecos_api_key: str = ""

    # ── 운영 ─────────────────────────────────────────────────────────
    # `NoDecode` 가 없으면 pydantic-settings 가 **검증자보다 먼저** 값을 JSON 으로
    # 파싱하려 해서 `CORS_ORIGINS=http://localhost:3000` 이 JSONDecodeError 를 낸다.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    rate_limit_per_min: int = 20
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """CORS_ORIGINS=a,b 형태의 쉼표 구분 문자열을 허용한다."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("chroma_path", "bm25_index_path", mode="after")
    @classmethod
    def _resolve_runtime_paths(cls, v: Path) -> Path:
        return _resolve(v, API_ROOT)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
