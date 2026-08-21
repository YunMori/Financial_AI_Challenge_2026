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

    ★ `used_for`(이 근거로 뒷받침한 요지, 한 줄)를 걷어냈다 (2026-08-21).
      **아무도 읽지 않는 필드였다** — 후처리·API 응답·프론트·평가기 어디에도
      참조가 없었다. 그런데 근거 5건이면 한국어 산문 5줄을 매 응답 생성했고,
      로컬 디코딩이 13 tok/s 라 그 값이 그대로 이용자 대기 시간이 됐다.

      후처리가 검사하는 것은 `ref` 의 **실재성**이지 요지의 내용이 아니다
      (`tiering.postprocess`). 즉 이 필드가 없어도 가드레일은 그대로다.
    """

    ref: int = Field(description="컨텍스트의 [근거 n] 에서 n. 1부터 시작")


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
    # 근거는 `context_top_n`(=5) 개만 주므로 정상 답변은 그 이하다. 12 는 넉넉한
    # 상한이면서 `numbers_used` 와 같은 폭주(같은 ref 반복)를 문법에서 막는다.
    citations: list[Citation] = Field(
        default_factory=list,
        max_length=12,
        description="사용한 근거. 비어 있으면 후처리가 응답을 차단한다.",
    )
    numbers_used: list[str] = Field(
        default_factory=list,
        max_length=24,
        description=(
            "답변에 등장시킨 모든 수치·금액·날짜·기간. 모델이 스스로 나열하게 하는 것이 "
            "숫자 대조 검사의 입력이다. 누락되면 검사가 통과는 하되 아무것도 잡지 못한다. "
            "날짜는 쪼개지 말고 한 항목으로 적는다 (`2025-03-21` — `2025`,`3`,`21` 이 아니다). "
            "같은 값을 여러 번 적지 않는다."
        ),
    )
    # ★ `max_length` 가 **문법 상한**이 된다 (JSON Schema `maxItems` → XGrammar).
    #   없으면 배열이 무한 허용되고, 모델이 같은 값을 반복하다 `max_new_tokens` 를
    #   소진해 JSON 이 잘린다 → 파싱 실패 → `upstream_error` 폴백이다.
    #
    #   실측 (2026-08-19 · exp_011 · Qwen3.5-4B):
    #     Q-OPEN-004  numbers_used 가 "2025","3","21" 을 무한 반복 (raw 3,490자에서 잘림)
    #     Q-OPEN-009  numbers_used 가 "3,613" 을 무한 반복 (raw 3,131자에서 잘림)
    #   **두 문항 모두 `answer` 본문은 정상적인 A 등급 답변이었다.** 본문이 멀쩡한데
    #   뒤쪽 배열 하나가 폭주해 통째로 버려지고 있었다 — 160문항 중 13건(8.1%).
    #
    #   상한을 24 로 둔 근거: 정상 답변의 numbers_used 는 통상 5~15개다. 24 는
    #   여유가 있으면서 폭주는 막는다. **자르는 것이 목적이 아니라 멈추게 하는 것이
    #   목적이다** — 문법이 닫으면 정지 조건(`local_client._grammar_finished_criteria`)
    #   이 그 즉시 생성을 끝낸다.
    #
    #   ⚠ 상한에 걸려 실제 수치가 잘리면 숫자 대조가 그만큼 헐거워진다. 24 를 넘는
    #   답변이 나오는지 리포트로 확인해야 한다 (아래 `citations` 도 같은 이유로 둔다).
    needs_confirmation: bool = Field(
        default=False,
        description="금융기관에 사전 확인이 필요한 내용 — 계층 B 강제 문구의 트리거",
    )
    out_of_scope: bool = Field(
        default=False,
        description="근거로 답할 수 없는 질문이라고 모델이 신고 — 즉시 폴백",
    )
