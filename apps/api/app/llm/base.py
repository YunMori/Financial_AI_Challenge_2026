"""LLM 추상화 (planner §2.3).

구현체를 나중에 꽂을 수 있게 인터페이스를 먼저 둔다. 지금은 Anthropic 하나뿐이지만
경계가 있어야 계층 판정·출력 검사를 프레임워크 밖 순수 함수로 유지할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from app.schemas.common import Lang
from app.schemas.llm import LLMAnswer


class GenerationRefused(Exception):
    """안전 분류기가 요청을 거절했다 (`stop_reason="refusal"`).

    HTTP 200 으로 돌아오므로 예외로 승격해 호출부가 놓치지 않게 한다.
    `response.content` 를 읽기 **전에** 판정해야 한다 — 거절 시 content 는
    비어 있거나 부분 출력이다.
    """

    def __init__(self, category: str | None = None) -> None:
        self.category = category
        super().__init__(f"모델이 요청을 거절했습니다 (category={category})")


class GenerationFailed(Exception):
    """재시도·폴백 후에도 실패."""


@dataclass(slots=True)
class GenerationStats:
    """지표 산출용. **질의·답변 원문은 담지 않는다**(planner §15.5)."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    fallback_used: bool = False
    retries: int = 0
    latency_ms: int = 0

    @property
    def cache_hit(self) -> bool:
        return self.cache_read_tokens > 0


@dataclass(slots=True)
class GenerationResult:
    answer: LLMAnswer
    stats: GenerationStats
    raw_text: str = ""  # 디버깅용. 로그에 남기지 않는다


@dataclass(slots=True)
class StreamEvent:
    """스트림 중 발생하는 사건."""

    kind: str            # "token" | "final"
    text: str = ""       # kind="token" 일 때 답변 조각
    result: GenerationResult | None = None  # kind="final"


class LLMClient(Protocol):
    """생성 클라이언트."""

    async def stream(
        self, *, system: str, user: str, lang: Lang,
    ) -> AsyncIterator[StreamEvent]:
        """구조화 출력을 스트리밍한다.

        `answer` 필드가 흘러나오는 대로 `kind="token"` 이벤트를 내고,
        스트림이 끝나면 `kind="final"` 로 검증된 `LLMAnswer` 를 돌려준다.
        """
        ...

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        """질의 정규화용 소형 모델 호출."""
        ...


@dataclass(slots=True)
class NullLLMClient:
    """API 키가 없을 때 쓰는 구현체.

    파이프라인이 죽지 않고 **정직하게 실패**하도록 한다. 검색까지는 정상
    동작하므로 개발·테스트에서 유용하다.
    """

    calls: list[str] = field(default_factory=list)

    async def stream(self, *, system: str, user: str, lang: Lang) -> AsyncIterator[StreamEvent]:
        self.calls.append(user)
        raise GenerationFailed("ANTHROPIC_API_KEY 가 설정되지 않았습니다")
        yield  # pragma: no cover - 시그니처를 제너레이터로 유지

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        raise GenerationFailed("ANTHROPIC_API_KEY 가 설정되지 않았습니다")
