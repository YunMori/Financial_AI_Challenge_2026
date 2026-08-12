"""① ~ ⑩ 파이프라인 조립 (planner §6.1).

라우터가 아니라 여기에 둔다 — HTTP 와 분리해야 단위 테스트가 가능하고,
나중에 배치 채점(`eval/run_eval.py`)이 같은 경로를 재사용할 수 있다.

    ① 입력 가드레일      정규식      인젝션 중화 / PII 마스킹
    ② 계층 C 선판정      정규식      개별 심사 요구는 여기서 끝난다 (LLM 미호출)
    ③ 질의 정규화        사전+LLM    다국어 → 한국어 검색어
    ④ 하이브리드 검색    규칙        dense ∥ BM25 → RRF
    ⑤ 리랭킹             —          M1 생략 (RRF 상위 5건 직행)
    ⑥ 컨텍스트 조립      규칙        [근거 n] 부여 + 시점 경고
    ⑦ 생성               LLM        구조화 출력 스트리밍
    ⑧ 출력 가드레일      규칙   ┐
    ⑨ 계층 최종 확정     규칙   ┘   finalize() 가 둘을 함께 수행한다
    ⑩ AI 생성 표시       UI

**②와 ⑨가 이 서비스의 심장이다.** 프롬프트가 아니라 코드가 막는다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.config import get_settings
from app.guardrail.input_filter import filter_input
from app.llm.base import GenerationFailed, GenerationRefused, GenerationStats, LLMClient
from app.llm.prompts import system_prompt, user_message
from app.rag.context import EvidenceContext, build_context
from app.rag.normalize import QueryNormalizer
from app.rag.retrieve import HybridRetriever
from app.schemas.chat import ChatRequest
from app.schemas.common import FallbackReason, Lang, RetrievalMeta, Tier
from app.tiering.postprocess import FinalResponse, finalize, make_fallback
from app.tiering.rules import BlockReason, match_tier_c

log = logging.getLogger(__name__)

# 계층 C 트리거 → 폴백 사유. 사기 판정만 별도 문구·연락처를 쓴다.
_BLOCK_TO_REASON: dict[BlockReason, FallbackReason] = {
    BlockReason.SCAM_VERDICT: FallbackReason.SCAM_VERDICT,
}


@dataclass(slots=True)
class PipelineEvent:
    """스트림 이벤트. 라우터가 SSE 로, 평가기가 리스트로 소비한다."""

    kind: str  # "meta" | "token" | "final"
    meta: RetrievalMeta | None = None
    # 검색된 청크 ID. **응답에는 넣지 않는다** — 이용자에게 필요한 것은 출처이지
    # 내부 식별자가 아니다. Recall@5 를 재려면 평가기가 이 값을 봐야 하고,
    # 폴백된 질의에도 담아야 "검색이 실패한 것"과 "임계값이 높은 것"을 가른다.
    chunk_ids: list[str] = field(default_factory=list)
    # 검색 신뢰도 점수. **모든 이벤트에 싣는다** — `meta` 는 임계값을 통과한
    # 뒤에만 나가므로, 여기에만 담으면 폴백된 질의의 점수가 어디에도 남지
    # 않는다. 임계값을 튜닝하면서 그 입력을 못 보는 상태가 된다.
    top1: float = 0.0
    text: str = ""
    response: FinalResponse | None = None
    stats: GenerationStats | None = None
    latency_ms: int = 0


@dataclass(slots=True)
class PipelineTelemetry:
    """지표용. **질의·답변 원문은 담지 않는다** (planner §15.5)."""

    lang: str = ""
    visa: str | None = None
    tier: str | None = None
    fallback_reason: str | None = None
    top1: float = 0.0
    n_candidates: int = 0
    normalize_via: str = ""
    injection_hits: int = 0
    pii_kinds: list[str] = field(default_factory=list)
    latency_ms: int = 0
    llm: GenerationStats | None = None


class ChatPipeline:
    def __init__(self, llm: LLMClient, retriever: HybridRetriever,
                 normalizer: QueryNormalizer | None = None) -> None:
        self._llm = llm
        self._retriever = retriever
        self._normalizer = normalizer or QueryNormalizer()

    async def run(self, req: ChatRequest) -> AsyncIterator[PipelineEvent]:
        started = time.perf_counter()
        s = get_settings()
        lang = req.lang
        tel = PipelineTelemetry(lang=lang.value)

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        retrieved: list[str] = []

        def done(resp: FinalResponse, stats: GenerationStats | None = None) -> PipelineEvent:
            tel.tier = resp.tier.value
            tel.fallback_reason = resp.fallback_reason.value if resp.fallback_reason else None
            tel.latency_ms = elapsed()
            tel.llm = stats
            log.info("chat lang=%s visa=%s tier=%s fallback=%s top1=%.3f n=%d "
                     "via=%s inj=%d pii=%s %dms",
                     tel.lang, tel.visa, tel.tier, tel.fallback_reason, tel.top1,
                     tel.n_candidates, tel.normalize_via, tel.injection_hits,
                     tel.pii_kinds, tel.latency_ms)
            return PipelineEvent(kind="final", response=resp, stats=stats,
                                 latency_ms=tel.latency_ms, chunk_ids=list(retrieved),
                                 top1=tel.top1)

        # ── ① 입력 가드레일 ─────────────────────────────────────────
        filtered = filter_input(req.message)
        tel.injection_hits = filtered.injection_hits
        tel.pii_kinds = filtered.pii_found
        if filtered.blocked:
            yield done(make_fallback(FallbackReason.INJECTION_BLOCKED, lang))
            return

        # 이후 모든 단계는 마스킹·중화된 텍스트만 쓴다.
        question = filtered.text

        # ── ② 계층 C 선판정 ─────────────────────────────────────────
        # LLM 에 도달하기 전에 끝낸다. 가장 안전하고 빠르다.
        if block := match_tier_c(question):
            reason = _BLOCK_TO_REASON.get(block, FallbackReason.TIER_C)
            log.info("계층 C 선판정: %s", block.value)
            yield done(make_fallback(reason, lang))
            return

        # ── ③ 질의 정규화 ───────────────────────────────────────────
        visa = req.context.visa.value if req.context.visa else None
        nq = self._normalizer.normalize(question, lang=lang.value, visa=visa)
        tel.normalize_via = nq.via
        tel.visa = nq.visa

        # 다국어 질의는 정규화된 한국어에 대해 한 번 더 본다(2차 방어).
        if nq.via != "passthrough" and (block := match_tier_c(nq.ko)):
            reason = _BLOCK_TO_REASON.get(block, FallbackReason.TIER_C)
            log.info("계층 C 선판정(정규화 후): %s", block.value)
            yield done(make_fallback(reason, lang))
            return

        # ── ④ 검색 (⑤ 리랭킹은 M1 생략) ────────────────────────────
        result = self._retriever.search(nq.ko, visa=nq.visa)
        retrieved[:] = [c.chunk_id for c in result.candidates]
        # 게이트가 보는 점수. 리랭킹이 켜져 있으면 리랭커 점수다 —
        # dense 코사인은 안/밖 분리 폭이 음수라 게이트로 쓸 수 없다(ADR-001).
        tel.top1 = result.confidence
        tel.n_candidates = len(result.candidates)

        # 언어별 임계값. 교차 언어 검색은 점수가 체계적으로 낮게 나온다.
        threshold = s.threshold_for(lang.value)
        if result.is_empty or result.confidence < threshold:
            log.info("검색 신뢰도 미달: %s=%.3f < %.3f (lang=%s)",
                     "rerank" if result.reranked else "top1",
                     result.confidence, threshold, lang.value)
            yield done(make_fallback(FallbackReason.LOW_CONFIDENCE, lang))
            return

        # ── ⑥ 컨텍스트 조립 ─────────────────────────────────────────
        ctx = build_context(result)

        yield PipelineEvent(kind="meta", meta=RetrievalMeta(
            top1_score=round(result.top1_dense, 4),
            margin=round(result.margin, 5),
            n_candidates=result.n_before_filter,
            stale=ctx.has_stale,
            via=nq.via,
        ), chunk_ids=list(retrieved), top1=tel.top1)

        # ── ⑦ 생성 ──────────────────────────────────────────────────
        gen = None
        try:
            async for ev in self._llm.stream(
                system=system_prompt(lang),
                user=user_message(
                    question=question,
                    context_block=ctx.block,
                    visa=visa,
                    stay=req.context.stay.value if req.context.stay else None,
                    purposes=[p.value for p in req.context.purposes],
                ),
                lang=lang,
            ):
                if ev.kind == "token":
                    yield PipelineEvent(kind="token", text=ev.text)
                elif ev.kind == "final":
                    gen = ev.result
        except GenerationRefused:
            yield done(make_fallback(FallbackReason.MODEL_REFUSAL, lang, ctx.refs))
            return
        except GenerationFailed as e:
            log.warning("생성 실패: %s", e)
            yield done(make_fallback(FallbackReason.UPSTREAM_ERROR, lang, ctx.refs))
            return

        if gen is None:
            yield done(make_fallback(FallbackReason.UPSTREAM_ERROR, lang, ctx.refs))
            return

        # ── ⑧⑨ 출력 검사 + 계층 확정 ★ ────────────────────────────
        yield done(finalize(gen.answer, ctx, lang), gen.stats)


def build_pipeline() -> ChatPipeline:
    from app.llm.anthropic_client import build_client
    from app.llm.base import NullLLMClient
    from app.rag.retrieve import get_retriever

    llm = build_client()
    # 번역기는 **클라이언트가 실제로 생성할 수 있을 때만** 붙인다.
    # 예전에는 `llm_enabled`(= API 키 유무)로 판단했는데, 로컬 백엔드는 키가
    # 없으므로 그 조건이면 ③ 정규화가 조용히 꺼진 채 돌아간다 — 키가 있어도
    # 번역이 꺼져 있던 `run_eval` 의 결함과 같은 형태다.
    translator = None if isinstance(llm, NullLLMClient) else _TranslatorAdapter(llm)
    return ChatPipeline(
        llm=llm, retriever=get_retriever(), normalizer=QueryNormalizer(translator=translator)
    )


class _TranslatorAdapter:
    """`QueryNormalizer` 가 기대하는 동기 인터페이스에 비동기 클라이언트를 맞춘다.

    정규화는 파이프라인의 동기 구간에서 호출되므로 이벤트 루프를 새로 만들지
    않고 스레드에서 돌린다. 캐시 히트가 대부분이라 실제 호출은 드물다.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def to_search_terms(self, query: str, lang: str) -> str:
        import asyncio
        import concurrent.futures

        coro = self._llm.translate_to_search_terms(query, lang)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=15)
