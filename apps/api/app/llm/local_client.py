"""로컬 생성 백엔드 — 외부 호출 없음 (ADR-004).

왜 로컬인가
-----------
ADR-002 는 "감수하는 것"으로 **국내 리전 원칙 미충족**을 적어 두었다.
Anthropic API 가 해외 리전이기 때문이다. 생성을 로컬 모델로 내리면 그 항목이
문구 수정이 아니라 **실제로 충족**된다 — 금융 도메인에서 데이터 주권은
서술로 덮을 수 있는 종류의 것이 아니다.

`LLMClient` 프로토콜만 만족하면 파이프라인은 손댈 필요가 없다. planner §2.3 이
"교체 비용 0"을 노리고 경계를 먼저 그어 둔 것이 여기서 쓰인다.

구조화 출력
-----------
**이 백엔드의 성패는 여기서 갈린다.** Anthropic 경로는 structured output 이
스키마를 강제해 주지만, 로컬 모델은 그런 보장이 없다. `citations` 나
`numbers_used` 가 깨지면 후처리의 인용 검사·숫자 대조가 통째로 무력해진다.

그래서 **XGrammar** 로 문법을 강제한다. 문법 제약은 형식적으로 잘못된 출력을
불가능하게 만들므로, 남는 위험은 형식이 아니라 **내용**(인용이 맞는가,
수치를 빠짐없이 나열했는가)으로 옮겨간다 — 그건 골든셋이 재는 것이다.

⚠ 문법은 **배열이 있다는 것만** 강제한다. `numbers_used` 의 *완전성*은
강제하지 못한다. 누락되면 숫자 대조가 "통과는 하되 아무것도 못 잡는다"
(ADR-002) — 환각 방어가 조용히 꺼진다. 골든셋 `gold_numbers` 로 별도 확인한다.

⚠ 스키마는 프롬프트에 **주입되지 않는다.** 모델은 스키마를 보지 못하므로
기대 구조를 프롬프트 본문에 서술해야 한다 (`app/llm/prompts.py`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from app.llm.base import (
    GenerationFailed,
    GenerationResult,
    GenerationStats,
    StreamEvent,
)
from app.schemas.common import Lang
from app.schemas.llm import LLMAnswer

log = logging.getLogger(__name__)


def resolve_device(preference: str = "auto") -> str:
    """실행 장치를 고른다.

    개발은 mps(Apple Silicon), 배포는 cuda(AWS g5/g6). **같은 코드 경로**로
    양쪽을 돌려야 "로컬에선 됐는데 배포에서는" 사고를 피할 수 있다 —
    이 프로젝트가 컨테이너 기동 실패와 조판에서 이미 두 번 겪은 형태다.
    """
    if preference != "auto":
        return preference
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(device: str, preference: str = "auto") -> Any:
    """장치별 기본 dtype.

    mps 는 bfloat16 지원이 고르지 않아 float16 을 쓴다. cpu 에서 float16 은
    오히려 느리므로 float32 로 둔다.
    """
    import torch

    if preference != "auto":
        return getattr(torch, preference)
    if device == "cuda":
        return torch.bfloat16
    if device == "mps":
        return torch.float16
    return torch.float32


class LocalLLMClient:
    """transformers + XGrammar 기반 생성 클라이언트.

    모델을 **지연 로드**한다. 임포트 시점에 수 GB 를 올리면 테스트와 CLI 가
    전부 느려지고, 이 백엔드를 쓰지 않는 설정에서도 메모리를 먹는다.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        dtype: str | None = None,
    ) -> None:
        from app.config import get_settings

        s = get_settings()
        self.model_name = model_name or s.local_model
        self.device = resolve_device(device or s.local_device)
        self._dtype_pref = dtype or s.local_dtype
        self._model = None
        self._tokenizer = None
        self._compiler = None  # XGrammar GrammarCompiler

    # ── 지연 로드 ────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype = resolve_dtype(self.device, self._dtype_pref)
        started = time.perf_counter()
        log.info("로컬 모델 로드: %s (device=%s dtype=%s)", self.model_name, self.device, dtype)

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name, dtype=dtype,
        ).to(self.device)
        self._model.eval()
        torch.set_grad_enabled(False)
        log.info("로컬 모델 로드 완료 — %.1f초", time.perf_counter() - started)

    @property
    def compiler(self):
        """XGrammar 컴파일러. 토크나이저 어휘에 묶이므로 모델당 하나다."""
        if self._compiler is None:
            import xgrammar as xgr

            self._load()
            info = xgr.TokenizerInfo.from_huggingface(
                self._tokenizer, vocab_size=self._model.config.vocab_size,
            )
            self._compiler = xgr.GrammarCompiler(info)
        return self._compiler

    # ── LLMClient 프로토콜 ───────────────────────────────────────────

    async def stream(
        self, *, system: str, user: str, lang: Lang,
    ) -> AsyncIterator[StreamEvent]:
        """구조화 출력 생성. Step 2 에서 구현한다."""
        raise GenerationFailed("로컬 생성은 아직 구현되지 않았습니다 (Step 2)")
        yield  # pragma: no cover - 시그니처를 제너레이터로 유지

    async def translate_to_search_terms(self, query: str, lang: str) -> str:
        """질의 정규화. Step 1 에서 구현한다."""
        raise GenerationFailed("로컬 정규화는 아직 구현되지 않았습니다 (Step 1)")


def build_local_client() -> LocalLLMClient:
    return LocalLLMClient()


__all__ = ["LocalLLMClient", "build_local_client", "resolve_device", "resolve_dtype"]
