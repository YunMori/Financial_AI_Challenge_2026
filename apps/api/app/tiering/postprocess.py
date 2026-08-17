"""⑨ 계층 최종 확정 (planner §7.4) — **이 서비스의 심장**.

모델이 무엇을 주장하든 여기서 확정한다. "프롬프트가 아니라 아키텍처로
강제한다"는 주장의 실체가 이 함수다. 프레임워크 밖 순수 함수로 두어
단위 테스트와 골든셋 채점이 가능하게 한다.

검사 순서에 의미가 있다 — **싸고 확실한 것부터** 본다. 인용이 아예 없으면
숫자를 대조할 이유가 없다.

    1. 인용 존재      → 없으면 차단
    2. 인용 실재성    → 지어낸 [근거 n] 이면 차단
    3. 숫자 대조      → 근거에 없는 수치면 차단  ★환각의 대부분이 여기서 잡힌다
    4. 금지 표현      → "반드시 승인" 등이면 차단
    5. 민감정보 요구  → 답변이 계좌·비밀번호를 물으면 차단 (사칭 대응)
    6. 출력 언어      → 요청 언어가 아니면 차단  ★다국어 서비스의 최소 조건
    7. 계층 강등      → 근거가 약하면 A→B
    8. 계층 C 확정    → 모델 판단과 무관하게 폴백
    9. 계층 B 문구    → 사전확인 권고 강제 삽입
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.i18n.fallbacks import fallback_contacts, fallback_text, prepend_generic_notice
from app.rag.context import EvidenceContext
from app.schemas.common import EvidenceRef, FallbackReason, Lang, Tier
from app.schemas.llm import LLMAnswer
from app.tiering.rules import find_credential_request, find_forbidden
from app.util.language import detect_answer_language
from app.util.numerals import numeral_supported

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FinalResponse:
    """이용자에게 나가는 최종 형태."""

    tier: Tier
    answer: str
    refs: list[EvidenceRef]
    fallback_reason: FallbackReason | None = None
    contacts: list[str] | None = None
    downgraded_from: Tier | None = None
    unsupported_numbers: list[str] | None = None  # 로그·지표용. 화면에 노출하지 않는다

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None


def make_fallback(reason: FallbackReason, lang: Lang,
                  refs: list[EvidenceRef] | None = None,
                  **extra) -> FinalResponse:
    """정적 폴백 응답. **LLM을 다시 부르지 않는다.**

    근거는 표시한다 — 답을 못 주더라도 "우리가 무엇을 봤는지"는 보여주는 편이
    이용자가 스스로 판단하는 데 도움이 된다.
    """
    return FinalResponse(
        tier=Tier.C,
        answer=fallback_text(reason, lang),
        refs=refs or [],
        fallback_reason=reason,
        contacts=fallback_contacts(reason, lang),
        **extra,
    )


def finalize(answer: LLMAnswer, ctx: EvidenceContext, lang: Lang) -> FinalResponse:
    """모델 출력을 검사하고 계층을 확정한다."""

    # ── 1. 인용 존재 ────────────────────────────────────────────────
    # 계층 C 는 애초에 답변을 만들지 않으므로 인용이 없는 게 정상이다.
    if answer.out_of_scope or answer.tier is Tier.C:
        return make_fallback(FallbackReason.TIER_C if answer.tier is Tier.C
                             else FallbackReason.OUT_OF_SCOPE, lang, ctx.refs)

    if not answer.citations:
        log.info("차단: 인용 없음")
        return make_fallback(FallbackReason.NO_CITATION, lang, ctx.refs)

    # ── 2. 인용 실재성 ──────────────────────────────────────────────
    valid = ctx.ref_range()
    if phantom := [c.ref for c in answer.citations if c.ref not in valid]:
        log.info("차단: 존재하지 않는 근거 번호 %s (유효 1~%d)", phantom, ctx.n)
        return make_fallback(FallbackReason.PHANTOM_CITATION, lang, ctx.refs)

    # ── 3. 숫자 대조 ★ ──────────────────────────────────────────────
    # 금융 안내의 치명적 오류는 대부분 숫자에서 나온다.
    if unsupported := [t for t in answer.numbers_used
                       if not numeral_supported(t, ctx.numerals)]:
        log.info("차단: 근거에 없는 수치 %s", unsupported)
        return make_fallback(FallbackReason.UNSUPPORTED_NUMBER, lang, ctx.refs,
                             unsupported_numbers=unsupported)

    # ── 4. 금지 표현 ────────────────────────────────────────────────
    if hit := find_forbidden(answer.answer):
        log.info("차단: 금지 표현 %r", hit)
        return make_fallback(FallbackReason.FORBIDDEN_EXPRESSION, lang, ctx.refs)

    # ── 5. 민감정보 요구 (서비스 사칭 대응) ─────────────────────────
    # 우리 서비스는 계좌번호·비밀번호를 절대 묻지 않는다. 답변에 그런 요구가
    # 나오면 근거가 무엇이든 사고다.
    if hit := find_credential_request(answer.answer):
        log.warning("차단: 답변이 민감정보를 요구함 %r", hit)
        return make_fallback(FallbackReason.CREDENTIAL_REQUEST, lang, ctx.refs)

    # ── 6. 출력 언어 ★ ──────────────────────────────────────────────
    # 요청 언어가 아닌 언어로 답하면, 인용이 맞고 숫자가 맞아도 이용자에게는
    # **읽을 수 없는 답변**이다. 형식 검사를 다 통과한 뒤·계층을 매기기 전이
    # 이 검사의 자리다 — 내보낼 수 없는 답변에 계층을 붙일 이유가 없다.
    #
    # `is False` 로 검사한다. `detect_answer_language` 는 판정 불가에 None 을
    # 주므로 `not ...` 로 쓰면 짧은 정상 답변까지 막힌다.
    if detect_answer_language(answer.answer, lang.value) is False:
        log.info("차단: 요청 언어(%s)가 아닌 언어로 답변", lang.value)
        return make_fallback(FallbackReason.LANGUAGE_MISMATCH, lang, ctx.refs)

    # ── 7. 계층 강등 ────────────────────────────────────────────────
    tier = answer.tier
    original = tier
    if tier is Tier.A and not ctx.all_government:
        # 기관 자료·2차 자료가 섞였으면 "공식 안내에 명시"라고 할 수 없다.
        tier = Tier.B
    if tier is Tier.A and ctx.has_stale:
        # 확인일이 오래된 근거로 단정하지 않는다.
        tier = Tier.B
    if answer.needs_confirmation and tier is Tier.A:
        tier = Tier.B

    # ── 9. 계층 B 강제 문구 ─────────────────────────────────────────
    text = answer.answer
    if tier is Tier.B:
        text = prepend_generic_notice(text, lang)

    return FinalResponse(
        tier=tier,
        answer=text,
        refs=ctx.refs,
        downgraded_from=original if tier is not original else None,
    )
