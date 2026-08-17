"""공통 타입.

이 모듈의 Pydantic 모델이 `docs/functional-spec.md` 의 "입력"·"출력" 항목
소스다. 필드를 바꾸면 명세서 해당 절도 함께 갱신한다(planner §1.2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Lang(StrEnum):
    """공개 언어.

    M1 은 ko/en/vi 3종. planner 는 6개 언어를 상정했으나 §10.3 이
    "검수되지 않은 언어는 공개하지 않는다"를 요구하므로, 원어민 검수를
    확보한 만큼만 늘린다(§17-R2 축소안을 처음부터 채택).
    """

    KO = "ko"
    EN = "en"
    VI = "vi"


class Tier(StrEnum):
    """응답 계층 (planner §7.1).

    최종 확정은 모델이 아니라 `app.tiering.postprocess.finalize()` 가 한다.
    모델이 스스로 신고한 tier 는 참고값일 뿐이며 강등될 수 있다.
    """

    A = "A"  # 법령·규정·기관 공식 안내에 명시. 파란 출처 배지
    B = "B"  # 통상적 경향. "일반적으로" 문구 + 사전확인 권고 강제 삽입
    C = "C"  # 개별 심사 결과. 생성 금지, 공식 채널 안내로 대체


class EvidenceStatus(StrEnum):
    """요건 매트릭스 셀 상태 (planner §4.2).

    공식 문서로 96셀을 다 채우는 것은 불가능하다. 억지로 채우면 그 자체가
    환각이 되어 서비스의 핵심 가치를 무너뜨린다. 모르는 칸을 모른다고
    표시하는 것이 오히려 신뢰성 어필 포인트다.
    """

    OFFICIAL = "official"  # 해당 기관·정부 공식 문서에 명시 → 계층 A 가능
    INFERRED = "inferred"  # 일반 규정에서 추론, 기관 개별 명시 없음 → 계층 B
    UNKNOWN = "unknown"  # 근거 없음 → 화면에 "확인 필요", 생성 금지


class FallbackReason(StrEnum):
    """생성을 차단하고 정적 안내로 대체한 이유 (planner §7.4, §7.5).

    이용자에게는 사유를 그대로 노출하지 않는다(`unsupported_number` 를 보여주면
    "AI가 숫자를 지어냈다"는 사실만 전달된다). 로그와 지표 산출에만 쓰고,
    화면에는 사유별로 매핑된 정적 문구를 보여준다.
    """

    # ── 생성 전 차단 (규칙 기반, LLM 호출 자체를 하지 않음) ──────────
    TIER_C = "tier_c"  # 개별 승인·한도·금리 예측 요구
    SCAM_VERDICT = "scam_verdict"  # "이거 사기인가요?" — 판정 금지 (F8)
    INJECTION_BLOCKED = "injection_blocked"  # 입력 대부분이 인젝션 패턴
    LOW_CONFIDENCE = "low_confidence"  # 검색 신뢰도 임계값 미달

    # ── 생성 후 차단 (출력 검사) ─────────────────────────────────────
    NO_CITATION = "no_citation"  # 인용 0건
    PHANTOM_CITATION = "phantom_citation"  # 존재하지 않는 [근거 n] 을 지어냄
    UNSUPPORTED_NUMBER = "unsupported_number"  # 근거에 없는 수치 ★환각의 대부분
    FORBIDDEN_EXPRESSION = "forbidden_expression"  # "보장", "반드시 승인" 등
    CREDENTIAL_REQUEST = "credential_request"  # 답변이 계좌·비밀번호를 요구 (사칭 대응)
    OUT_OF_SCOPE = "out_of_scope"  # 모델이 스스로 범위 밖이라 신고
    # 요청 언어가 아닌 언어로 답함. 다국어 서비스에서 이건 인용이 맞고 숫자가 맞아도
    # 이용자에게는 **읽을 수 없는 답변**이다. 소형 모델의 전형적 실패라 로컬 백엔드
    # 전환(ADR-004)과 함께 들어왔다.
    LANGUAGE_MISMATCH = "language_mismatch"

    # ── 외부 요인 ────────────────────────────────────────────────────
    MODEL_REFUSAL = "model_refusal"  # 안전 분류기 거절 (stop_reason=refusal)
    UPSTREAM_ERROR = "upstream_error"  # 재시도·폴백 후에도 실패


class EvidenceRef(BaseModel):
    """이용자에게 노출하는 출처 한 건.

    `verified_at`(내가 원문을 확인한 날)이 이 서비스의 차별점이다.
    발행일만 보여주는 서비스는 많지만, 확인일을 보여주면 정보의 최신성을
    운영자가 책임진다는 뜻이 된다(planner §4.1).
    """

    ref: int = Field(description="본문의 [근거 n] 에서 n")
    chunk_id: str
    title: str
    publisher: str
    published_at: str | None = Field(default=None, description="ISO 날짜")
    verified_at: str | None = Field(default=None, description="원문 확인일 (ISO)")
    url: str | None = None
    stale: bool = Field(
        default=False,
        description="확인일이 stale_days 를 넘김 — UI 에 시점 경고 배지",
    )


class RetrievalMeta(BaseModel):
    """검색 단계 요약. 디버깅과 지표 산출용이며 개인정보를 담지 않는다."""

    top1_score: float
    margin: float = Field(description="1위와 3위의 점수 차 (변별력)")
    n_candidates: int
    stale: bool = Field(default=False, description="근거 중 하나라도 오래됨")
    via: Literal["glossary", "llm", "passthrough"] = Field(
        default="passthrough", description="질의 정규화 경로 (planner §6.2)"
    )


class ErrorEnvelope(BaseModel):
    """공통 에러 스키마 (planner §9.1)."""

    code: str
    message_i18n: dict[Lang, str]
    detail: str | None = None
