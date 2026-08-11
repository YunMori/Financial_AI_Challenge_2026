"""LLM 구조화 출력 스키마 (planner §7.3).

이 모듈의 `LLMAnswer` 는 Anthropic structured output 의 JSON 스키마로
그대로 넘어간다. 필드를 바꾸면 프롬프트(부록 A)와 후처리
(`app.tiering.postprocess`) 양쪽이 함께 바뀌어야 한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import Tier


class Citation(BaseModel):
    """모델이 사용했다고 신고한 근거 한 건.

    긴 `chunk_id` 를 모델에게 출력시키면 오타가 난다. 컨텍스트에 `[근거 n]`
    으로 번호를 붙여 주고 그 n 만 회수한 뒤, 서버가 chunk_id 로 역매핑한다.
    """

    ref: int = Field(description="컨텍스트의 [근거 n] 에서 n. 1부터 시작")
    used_for: str = Field(description="이 근거로 뒷받침한 문장의 요지 (한 줄)")


class LLMAnswer(BaseModel):
    """생성 단계의 구조화 출력.

    **필드 순서에 설계 의도가 있다.** 구조화 출력은 스키마 순서대로 생성되므로
    `answer` 를 첫 필드에 두어야 스트리밍에서 본문이 가장 먼저 흘러나온다.
    TTFT 1.8초 목표(§14.2)가 여기에 걸려 있다 — 순서를 바꾸면 이용자는
    tier·citations 가 다 생성될 때까지 빈 화면을 본다.
    """

    answer: str = Field(
        description="대상 언어로 작성한 답변 본문. 계층 C 이면 빈 문자열."
    )
    tier: Tier = Field(
        description="모델이 판단한 계층. 최종값이 아니며 후처리에서 강등될 수 있다."
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="사용한 근거. 비어 있으면 후처리가 응답을 차단한다.",
    )
    numbers_used: list[str] = Field(
        default_factory=list,
        description=(
            "답변에 등장시킨 모든 수치·금액·날짜·기간. 모델이 스스로 나열하게 하는 것이 "
            "숫자 대조 검사의 입력이다. 누락되면 검사가 통과는 하되 아무것도 잡지 못한다."
        ),
    )
    needs_confirmation: bool = Field(
        default=False,
        description="금융기관에 사전 확인이 필요한 내용 — 계층 B 강제 문구의 트리거",
    )
    out_of_scope: bool = Field(
        default=False,
        description="근거로 답할 수 없는 질문이라고 모델이 신고 — 즉시 폴백",
    )
