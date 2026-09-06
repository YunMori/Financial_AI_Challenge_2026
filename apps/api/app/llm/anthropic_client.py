"""Anthropic 구현체 (claude-sonnet-5, 폴백 claude-opus-5).

호출 형태에서 지킬 네 가지 (ADR-002):

1. **캐시 경계** — 시스템 프롬프트에만 `cache_control` 을 건다. 검색된 근거는
   매 요청 바뀌므로 user 메시지에 둔다. sonnet-5 의 최소 캐시 프리픽스는
   **1,024토큰**이며, 미달이면 에러 없이 조용히 캐시되지 않는다.
2. **`effort: medium`** — sonnet-5 기본값은 high 라 그대로 두면 지연 예산을
   넘긴다. low 로 내리지 않는 이유는 `numbers_used` 누락 위험 때문이다 —
   누락되면 숫자 대조 검사가 통과는 하되 아무것도 잡지 못한다.
3. **`stop_reason == "refusal"`** — HTTP 200 으로 돌아온다. content 를 읽기
   **전에** 분기한다. 보이스피싱 대응 안내가 오탐 위험 구간이다.
4. **모델 폴백** — 재시도 2회 후 opus-5. 발동 자체를 지표로 남긴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from app.config import get_settings
from app.llm.base import (
    GenerationFailed,
    GenerationRefused,
    GenerationResult,
    GenerationStats,
    StreamEvent,
)
from app.llm.prompts import normalize_prompt
from app.schemas.common import Lang
from app.schemas.llm import LLMAnswer
from app.streaming.partial_json import AnswerStreamer

log = logging.getLogger(__name__)

# 정규화는 짧은 검색어만 만들면 되므로 작게 잡는다.
NORMALIZE_MAX_TOKENS = 128


class AnthropicClient:
    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        s = get_settings()
        self._settings = s
        self._client = anthropic.AsyncAnthropic(api_key=api_key or s.anthropic_api_key)
        self._models = [s.llm_model, s.llm_model_fallback]

    # ── 생성 ─────────────────────────────────────────────────────────

    async def stream(self, *, system: str, user: str, lang: Lang) -> AsyncIterator[StreamEvent]:
        s = self._settings
        last_error: Exception | None = None

        for model_index, model in enumerate(self._models):
            for attempt in range(s.llm_max_retries + 1):
                try:
                    async for event in self._stream_once(
                        model=model, system=system, user=user,
                        fallback_used=model_index > 0,
                        retries=attempt + model_index * (s.llm_max_retries + 1),
                    ):
                        yield event
                    return
                except GenerationRefused:
                    # 거절은 재시도해도 같다. 폴백 모델은 분류기가 다르므로
                    # 다음 모델로는 넘어간다.
                    log.info("모델 %s 가 요청을 거절했습니다", model)
                    last_error = GenerationRefused()
                    break
                except Exception as e:  # 네트워크·쿼터·과부하
                    last_error = e
                    if attempt < s.llm_max_retries:
                        wait = 0.5 * (2**attempt)
                        log.warning("%s 실패 (%s) — %.1fs 후 재시도", model, type(e).__name__, wait)
                        await asyncio.sleep(wait)
                    else:
                        log.warning("%s 재시도 소진 — 다음 모델로", model)

        if isinstance(last_error, GenerationRefused):
            raise last_error
        raise GenerationFailed(f"모든 모델 실패: {last_error}")

    async def _stream_once(
        self, *, model: str, system: str, user: str,
        fallback_used: bool, retries: int,
    ) -> AsyncIterator[StreamEvent]:
        s = self._settings
        started = time.perf_counter()
        streamer = AnswerStreamer()

        async with self._client.messages.stream(
            model=model,
            max_tokens=s.llm_max_tokens,
            system=[{
                "type": "text",
                "text": system,
                # ★ 안정 프리픽스만 캐시한다. 근거는 user 메시지에 있다.
                "cache_control": {"type": "ephemeral"},
            }],
            thinking={"type": s.llm_thinking},
            output_config={
                "effort": s.llm_effort,
                "format": {
                    "type": "json_schema",
                    "schema": _strict_schema(LLMAnswer.model_json_schema()),
                },
            },
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for chunk in stream.text_stream:
                if piece := streamer.feed(chunk):
                    yield StreamEvent(kind="token", text=piece)

            message = await stream.get_final_message()

        # ★ content 를 읽기 **전에** 거절을 판정한다.
        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            raise GenerationRefused(getattr(details, "category", None))

        raw = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        try:
            answer = LLMAnswer.model_validate_json(raw)
        except Exception as e:
            raise GenerationFailed(f"구조화 출력 파싱 실패: {type(e).__name__}") from e

        usage = message.usage
        yield StreamEvent(
            kind="final",
            result=GenerationResult(
                answer=answer,
                raw_text=raw,
                stats=GenerationStats(
                    model=model,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    fallback_used=fallback_used,
                    retries=retries,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
            ),
        )

    # ── 질의 정규화 (소형 모델) ──────────────────────────────────────

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        message = await self._client.messages.create(
            model=self._settings.llm_model_small,
            max_tokens=NORMALIZE_MAX_TOKENS,
            messages=[{"role": "user",
                       "content": normalize_prompt(query, lang)}],
        )
        if getattr(message, "stop_reason", None) == "refusal":
            raise GenerationRefused()
        return "".join(
            b.text for b in message.content if getattr(b, "type", "") == "text"
        ).strip()


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """구조화 출력이 요구하는 형태로 다듬는다.

    모든 object 에 `additionalProperties: false` 가 있어야 하고, Pydantic 이
    붙이는 `$defs`/`title` 은 그대로 두어도 된다. 여기서 스키마를 손보는 대신
    `LLMAnswer` 를 고치면 명세서와 어긋나므로, 변환은 이 함수에 가둔다.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items()}
            if node.get("type") == "object" and "additionalProperties" not in node:
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def build_client():
    """설정에 맞는 생성 클라이언트.

    `LLM_BACKEND=local` 이면 외부 호출을 **하나도** 하지 않는 구현체를 준다
    (ADR-004). 키 유무는 anthropic 백엔드일 때만 따진다 — 로컬 경로에서
    키를 요구하면 "완전 로컬"이라는 주장이 성립하지 않는다.
    """
    s = get_settings()
    if s.llm_backend == "local":
        from app.llm.local_client import build_local_client

        return build_local_client()

    if not s.anthropic_key_present:
        from app.llm.base import NullLLMClient

        log.warning("ANTHROPIC_API_KEY 미설정 — 생성 기능이 비활성화됩니다 "
                    "(검색·계층 판정은 정상 동작). 로컬 모델을 쓰려면 "
                    "LLM_BACKEND=local 로 두세요.")
        return NullLLMClient()
    return AnthropicClient()
