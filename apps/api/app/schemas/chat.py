"""`/api/v1/chat` 요청·응답 스키마 (planner §9.2).

**서버는 상태를 저장하지 않는다.** 세션 컨텍스트는 클라이언트
sessionStorage 에 있고 매 요청 body 로 실려 온다. 회원 테이블도 대화 로그
테이블도 없다는 사실 자체가 개인정보보호 어필 포인트이며, 동시에 8주
개발에서 인증·DB 작업을 통째로 제거해 준다(§2.1).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import EvidenceRef, FallbackReason, Lang, RetrievalMeta, Tier

# 대화 히스토리 상한. 컨텍스트 비용 통제 목적이며 클라이언트도 같은 값으로 자른다.
MAX_HISTORY_TURNS = 6
MAX_MESSAGE_CHARS = 2000


class VisaCode(StrEnum):
    """지원 체류자격 12종 (planner §10 F1).

    2026-06 기준 이 12종이 체류외국인의 68.7% 를 덮는다. 빠진 상위 자격은
    B-2(관광통과)·C-3(단기방문)·B-1(사증면제) 로 전부 단기체류라 계좌개설
    대상이 아니다. 근거: `corpus/stats/moj_24_foreigners_by_visa_monthly.csv`

    확장 후보는 F-3(동반, 84,976명) — E-7 과 맞먹는 규모이고 취업·유학
    체류자의 배우자·자녀로 장기체류하며 계좌가 실제로 필요하다.
    """

    E9 = "E-9"  # 비전문취업 349,009
    E8 = "E-8"  # 계절근로  81,405
    E7 = "E-7"  # 특정활동  87,097
    D2 = "D-2"  # 유학     236,366
    D4 = "D-4"  # 일반연수  90,691
    D8 = "D-8"  # 기업투자   8,558
    D10 = "D-10"  # 구직     26,917
    F2 = "F-2"  # 거주      70,405
    F4 = "F-4"  # 재외동포  602,101
    F5 = "F-5"  # 영주     228,485
    F6 = "F-6"  # 결혼이민  157,188
    H2 = "H-2"  # 방문취업   35,678


class Purpose(StrEnum):
    """거래 목적. 계좌개설 시 목적 증빙 서류 조합이 여기서 갈린다."""

    SALARY = "salary"  # 급여수령
    TUITION = "tuition"  # 학비
    LIVING = "living"  # 생활비
    REMITTANCE = "remittance"  # 송금
    BUSINESS = "business"  # 사업


class StayPeriod(StrEnum):
    """체류기간 구간. 자유 입력을 받지 않는 이유는 §5.1 최소 수집 원칙."""

    UNDER_6M = "under_6m"
    M6_12 = "6m_12m"
    Y1_2 = "1y_2y"
    OVER_2Y = "over_2y"


class SessionContext(BaseModel):
    """온보딩에서 수집하는 전부 (planner §10 F1).

    **자유 입력이 없다.** 전부 선택지이며 식별정보를 담지 않는다.
    국적은 ISO 3166-1 alpha-2 코드만 받는다.
    """

    nationality: str | None = Field(default=None, min_length=2, max_length=2)
    visa: VisaCode | None = None
    stay: StayPeriod | None = None
    purposes: list[Purpose] = Field(default_factory=list, max_length=5)

    @field_validator("nationality")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    lang: Lang
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    context: SessionContext = Field(default_factory=SessionContext)
    history: list[HistoryTurn] = Field(default_factory=list)

    @field_validator("history")
    @classmethod
    def _truncate_history(cls, v: list[HistoryTurn]) -> list[HistoryTurn]:
        """상한을 넘으면 거절하지 않고 최근 것만 남긴다.

        클라이언트 버그로 히스토리가 길어졌다는 이유로 이용자의 질문을
        실패시킬 이유가 없다.
        """
        return v[-MAX_HISTORY_TURNS:]


# ── SSE 이벤트 페이로드 ──────────────────────────────────────────────
#
# 이벤트 순서: meta → token* → citations → (invalidate) → done
#
# `meta` 의 tier 는 **잠정값**이다. 최종 tier 는 `done` 에서 확정된다.
# 스트리밍 중 출력 검사에 실패하면 `invalidate` 를 보내고 클라이언트가 표시된
# 텍스트를 폴백 카드로 **교체**한다. 이 처리를 빠뜨리면 "차단했다"고 주장하면서
# 화면엔 환각이 남는 사고가 난다(planner §9.2).


class MetaEvent(BaseModel):
    """event: meta — 생성 시작 직전. 검색 결과 요약."""

    tier: Tier = Field(description="잠정값. done 에서 확정된다")
    retrieval: RetrievalMeta


class TokenEvent(BaseModel):
    """event: token — 답변 본문 조각."""

    t: str


class CitationsEvent(BaseModel):
    """event: citations — 출처 배지 렌더용."""

    items: list[EvidenceRef]


class InvalidateEvent(BaseModel):
    """event: invalidate — 표시된 텍스트를 폴백 카드로 교체하라.

    `reason` 은 로그·지표용이며 이용자에게 그대로 보여주지 않는다.
    화면에는 `fallback_text` 를 쓴다.
    """

    reason: FallbackReason
    fallback_text: str
    contacts: list[str] = Field(
        default_factory=list, description="금융감독원 1332 등 공식 채널"
    )


class DoneEvent(BaseModel):
    """event: done — 최종 확정."""

    tier: Tier
    latency_ms: int
    ai_generated: Literal[True] = True
    fallback_reason: FallbackReason | None = None
